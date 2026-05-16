from __future__ import annotations

import csv
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.ops.candidate_coverage_gap import run_candidate_gap_audit
from qsys.data.adapter import QlibAdapter
from qsys.data.collector import TushareCollector
from qsys.ops.instrument_coverage import build_instrument_coverage_rows, read_calendar_summary, summarize_universe_registry
from qsys.ops.universe_sync import build_universe_snapshot

CORE_FIELDS = ["$open", "$high", "$low", "$close", "$volume", "$amount"]
SOURCE_STATS_FIELDS = [
    "symbol",
    "in_universe",
    "raw_file_exists",
    "source_row_count",
    "source_unique_date_count",
    "source_duplicate_date_count",
    "source_future_date_count",
    "source_first_date",
    "source_last_date",
]
SYMBOL_COVERAGE_FIELDS = [
    "symbol",
    "source_row_count",
    "source_unique_date_count",
    "source_duplicate_date_count",
    "source_future_date_count",
    "qlib_row_count",
    "qlib_first_date",
    "qlib_last_date",
    "duplicate_date_count",
    "has_only_one_row",
    "has_future_date",
    "core_field_non_null_ratio_min",
    "core_field_non_null_ratio_mean",
    "missing_core_fields",
    "failed_reasons",
]
FIELD_COVERAGE_FIELDS = [
    "field",
    "non_null_ratio",
    "non_null_count",
    "row_count",
    "eligible_non_null_ratio",
    "eligible_non_null_count",
    "eligible_row_count",
    "true_missing_count",
    "excluded_pre_listing_count",
    "excluded_not_active_count",
    "excluded_suspended_or_no_trade_count",
    "excluded_static_universe_denominator_issue_count",
]
FAILED_SYMBOL_FIELDS = ["symbol", "failed_reasons"]


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _normalize_path(base_dir: Path, path_value: str | Path | None, *, default: str) -> Path:
    if path_value is None:
        path = Path(default)
    else:
        path = Path(path_value)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _scan_raw_source_stats(raw_dir: Path, symbols: list[str], *, end_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    end_ts = pd.Timestamp(end_date)
    for symbol in symbols:
        path = raw_dir / f"{symbol}.feather"
        if not path.exists():
            rows.append(
                {
                    "symbol": symbol,
                    "in_universe": True,
                    "raw_file_exists": False,
                    "source_row_count": 0,
                    "source_unique_date_count": 0,
                    "source_duplicate_date_count": 0,
                    "source_future_date_count": 0,
                    "source_first_date": None,
                    "source_last_date": None,
                }
            )
            continue
        df = pd.read_feather(path)
        if df.empty or "trade_date" not in df.columns:
            rows.append(
                {
                    "symbol": symbol,
                    "in_universe": True,
                    "raw_file_exists": True,
                    "source_row_count": 0,
                    "source_unique_date_count": 0,
                    "source_duplicate_date_count": 0,
                    "source_future_date_count": 0,
                    "source_first_date": None,
                    "source_last_date": None,
                }
            )
            continue
        dates = pd.to_datetime(df["trade_date"], errors="coerce")
        valid_dates = dates.dropna()
        if valid_dates.empty:
            first_date = None
            last_date = None
            unique_count = 0
            duplicate_count = 0
            future_count = 0
        else:
            first_date = valid_dates.min().strftime("%Y-%m-%d")
            last_date = valid_dates.max().strftime("%Y-%m-%d")
            unique_count = int(valid_dates.dt.normalize().nunique())
            duplicate_count = int(valid_dates.dt.normalize().duplicated().sum())
            future_count = int((valid_dates > end_ts).sum())
        rows.append(
            {
                "symbol": symbol,
                "in_universe": True,
                "raw_file_exists": True,
                "source_row_count": int(len(valid_dates)),
                "source_unique_date_count": unique_count,
                "source_duplicate_date_count": duplicate_count,
                "source_future_date_count": future_count,
                "source_first_date": first_date,
                "source_last_date": last_date,
            }
        )
    return rows


def _copy_selected_artifact(path: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() == ".csv":
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        shutil.copy2(path, target)


def build_candidate(
    *,
    base_dir: str | Path,
    universe: str = "csi800",
    start_date: str = "2025-01-01",
    end_date: str = "2026-04-30",
    output_qlib_dir: str | Path | None = None,
    output_dir: str | Path = "experiments/ops_diagnostics/csi800_full_rebuild",
    force: bool = False,
) -> dict[str, Any]:
    base_dir = Path(base_dir).resolve()
    output_dir = _normalize_path(base_dir, output_dir, default="experiments/ops_diagnostics/csi800_full_rebuild")
    candidate_dir = _normalize_path(base_dir, output_qlib_dir, default=f"data/qlib_bin_candidate_{end_date.replace('-', '')}")

    if candidate_dir.exists():
        if not force:
            raise FileExistsError(f"candidate qlib dir already exists: {candidate_dir}")
        shutil.rmtree(candidate_dir)

    collector = TushareCollector()
    symbols = sorted(set(str(symbol) for symbol in collector.get_universe(universe)))
    adapter = QlibAdapter()
    source_stats = _scan_raw_source_stats(adapter.raw_dir, symbols, end_date=end_date)
    source_stats_path = _write_csv(output_dir / "candidate_source_stats.csv", source_stats, SOURCE_STATS_FIELDS)

    candidate_dir.mkdir(parents=True, exist_ok=True)
    meta_dir = candidate_dir / "_build_meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(meta_dir / "source_symbol_coverage.csv", source_stats, SOURCE_STATS_FIELDS)

    temp_csv_dir = output_dir / "candidate_source_csv"
    candidate_adapter = QlibAdapter(qlib_dir=candidate_dir)
    candidate_adapter.convert_all(
        output_qlib_dir=candidate_dir,
        selected_symbols=symbols,
        until_date=end_date,
        csv_output_dir=temp_csv_dir,
        refresh_universes=[],
    )

    candidate_post = QlibAdapter(qlib_dir=candidate_dir)
    _, csi800_summary, _, _ = build_universe_snapshot(
        adapter=candidate_post,
        universe="csi800",
        as_of_date=end_date,
        output_dir=output_dir / "candidate_registry_csi800",
        apply=True,
    )
    _, csi300_summary, _, _ = build_universe_snapshot(
        adapter=candidate_post,
        universe="csi300",
        as_of_date=end_date,
        output_dir=output_dir / "candidate_registry_csi300",
        apply=True,
    )

    calendar_summary = read_calendar_summary(candidate_post)
    all_instrument_path = candidate_dir / "instruments" / "all.txt"
    build_summary = {
        "universe": universe,
        "start_date": start_date,
        "end_date": end_date,
        "candidate_dir": str(candidate_dir),
        "symbol_count": len(symbols),
        "raw_symbol_count": int(sum(1 for row in source_stats if row["raw_file_exists"])),
        "raw_symbols_with_future_dates": int(sum(1 for row in source_stats if row["source_future_date_count"] > 0)),
        "all_instrument_exists": all_instrument_path.exists(),
        "calendar_summary": calendar_summary,
        "csi800_registry_summary": csi800_summary,
        "csi300_registry_summary": csi300_summary,
    }
    summary_path = _write_json(output_dir / "candidate_build_summary.json", build_summary)
    _write_json(meta_dir / "build_summary.json", build_summary)
    _copy_selected_artifact(summary_path, meta_dir / "build_summary.json")
    _copy_selected_artifact(source_stats_path, meta_dir / "source_symbol_coverage.csv")
    return build_summary


def _load_source_stats(candidate_dir: Path) -> pd.DataFrame:
    path = candidate_dir / "_build_meta" / "source_symbol_coverage.csv"
    if not path.exists():
        return pd.DataFrame(columns=SOURCE_STATS_FIELDS)
    return pd.read_csv(path)


def validate_candidate(
    *,
    base_dir: str | Path,
    candidate_qlib_dir: str | Path,
    universe: str = "csi800",
    start_date: str = "2025-01-01",
    end_date: str = "2026-04-30",
    expected_end_date: str = "2026-04-30",
    output_dir: str | Path = "experiments/ops_diagnostics/csi800_full_rebuild",
) -> dict[str, Any]:
    base_dir = Path(base_dir).resolve()
    output_dir = _normalize_path(base_dir, output_dir, default="experiments/ops_diagnostics/csi800_full_rebuild")
    candidate_dir = _normalize_path(base_dir, candidate_qlib_dir, default="data/qlib_bin_candidate")
    adapter = QlibAdapter(qlib_dir=candidate_dir)
    adapter.init_qlib()

    calendar_summary = read_calendar_summary(adapter)
    calendar_last_date = calendar_summary.get("calendar_last_date")
    calendar_first_date = calendar_summary.get("calendar_first_date")
    calendar_contains_expected_end_date = calendar_last_date == expected_end_date
    calendar_future_date_count = 0
    day_path = candidate_dir / "calendars" / "day.txt"
    if day_path.exists():
        cal_dates = pd.read_csv(day_path, header=None, names=["date"])
        cal_series = pd.to_datetime(cal_dates["date"], errors="coerce").dropna()
        calendar_future_date_count = int((cal_series > pd.Timestamp(expected_end_date)).sum())

    registry_summary = summarize_universe_registry(adapter, universe=universe, trade_date=calendar_last_date or expected_end_date).to_dict()
    coverage_rows = build_instrument_coverage_rows(adapter, universe=universe, last_qlib_date=calendar_last_date or expected_end_date, fields=CORE_FIELDS)

    symbols = [row["instrument"] for row in coverage_rows]
    feature_frame = adapter.get_features(symbols, CORE_FIELDS, start_time=calendar_first_date or "2010-01-01", end_time=calendar_last_date or expected_end_date)
    if feature_frame is None or feature_frame.empty:
        feature_frame = pd.DataFrame(columns=CORE_FIELDS)
    if isinstance(feature_frame.index, pd.MultiIndex):
        feature_work = feature_frame.reset_index().copy()
    else:
        feature_work = feature_frame.copy().reset_index()
    if "instrument" not in feature_work.columns:
        feature_work["instrument"] = None
    if "datetime" not in feature_work.columns:
        feature_work["datetime"] = pd.NaT
    feature_work["instrument"] = feature_work["instrument"].astype(str)
    feature_work["datetime"] = pd.to_datetime(feature_work["datetime"], errors="coerce")

    field_rows: list[dict[str, Any]] = []
    row_count = int(len(feature_work))
    for field in CORE_FIELDS:
        non_null_count = int(pd.to_numeric(feature_work.get(field), errors="coerce").notna().sum()) if field in feature_work.columns else 0
        field_rows.append(
            {
                "field": field,
                "non_null_ratio": float(non_null_count / row_count) if row_count else 0.0,
                "non_null_count": non_null_count,
                "row_count": row_count,
                "eligible_non_null_ratio": 0.0,
                "eligible_non_null_count": 0,
                "eligible_row_count": 0,
                "true_missing_count": 0,
                "excluded_pre_listing_count": 0,
                "excluded_not_active_count": 0,
                "excluded_suspended_or_no_trade_count": 0,
                "excluded_static_universe_denominator_issue_count": 0,
            }
        )

    source_stats_df = _load_source_stats(candidate_dir)
    source_stats_map = {
        str(row["symbol"]): row for row in source_stats_df.to_dict("records")
    }

    symbol_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    duplicate_date_count_total = 0
    future_date_count_total = calendar_future_date_count
    symbols_with_only_one_row = 0
    for symbol in symbols:
        symbol_frame = feature_work[feature_work["instrument"] == symbol].copy()
        symbol_frame = symbol_frame[symbol_frame["datetime"].notna()]
        duplicate_dates = int(symbol_frame["datetime"].duplicated().sum())
        duplicate_date_count_total += duplicate_dates
        qlib_row_count = int(len(symbol_frame))
        if qlib_row_count <= 1:
            symbols_with_only_one_row += 1
        qlib_first_date = symbol_frame["datetime"].min().strftime("%Y-%m-%d") if not symbol_frame.empty else None
        qlib_last_date = symbol_frame["datetime"].max().strftime("%Y-%m-%d") if not symbol_frame.empty else None
        source_row = source_stats_map.get(symbol, {})
        source_duplicate_date_count = int(source_row.get("source_duplicate_date_count", 0) or 0)
        source_future_date_count = int(source_row.get("source_future_date_count", 0) or 0)
        future_date_count_total += source_future_date_count
        non_null_ratios = []
        missing_core_fields = []
        for field in CORE_FIELDS:
            if field not in symbol_frame.columns:
                missing_core_fields.append(field)
                non_null_ratios.append(0.0)
                continue
            numeric = pd.to_numeric(symbol_frame[field], errors="coerce")
            non_null_ratios.append(float(numeric.notna().mean()) if len(symbol_frame) else 0.0)
            if numeric.notna().sum() == 0:
                missing_core_fields.append(field)
        failed_reasons = []
        if qlib_row_count <= 1:
            failed_reasons.append("only_one_row")
        if duplicate_dates > 0 or source_duplicate_date_count > 0:
            failed_reasons.append("duplicate_date")
        if source_future_date_count > 0:
            failed_reasons.append("future_date")
        if missing_core_fields:
            failed_reasons.append("missing_core_fields")
        row = {
            "symbol": symbol,
            "source_row_count": int(source_row.get("source_row_count", 0) or 0),
            "source_unique_date_count": int(source_row.get("source_unique_date_count", 0) or 0),
            "source_duplicate_date_count": source_duplicate_date_count,
            "source_future_date_count": source_future_date_count,
            "qlib_row_count": qlib_row_count,
            "qlib_first_date": qlib_first_date,
            "qlib_last_date": qlib_last_date,
            "duplicate_date_count": duplicate_dates + source_duplicate_date_count,
            "has_only_one_row": qlib_row_count <= 1,
            "has_future_date": source_future_date_count > 0,
            "core_field_non_null_ratio_min": min(non_null_ratios) if non_null_ratios else 0.0,
            "core_field_non_null_ratio_mean": float(sum(non_null_ratios) / len(non_null_ratios)) if non_null_ratios else 0.0,
            "missing_core_fields": ",".join(missing_core_fields),
            "failed_reasons": ",".join(sorted(set(failed_reasons))),
        }
        symbol_rows.append(row)
        if failed_reasons:
            failed_rows.append({"symbol": symbol, "failed_reasons": row["failed_reasons"]})

    gap_audit = run_candidate_gap_audit(
        base_dir=base_dir,
        candidate_qlib_dir=candidate_dir,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        output_dir=output_dir / "candidate_gap_audit_validation",
        validation_summary_path=None,
    )
    gap_summary = gap_audit["coverage_gap_summary"]
    eligible_coverage = float(gap_summary.get("eligible_core_market_field_coverage", 0.0) or 0.0)
    naive_coverage = float(gap_summary.get("naive_core_market_field_coverage", 0.0) or 0.0)
    eligible_non_null_count = int(gap_summary.get("eligible_non_null_cells", 0) or 0)
    eligible_row_count = int(gap_summary.get("eligible_cells", 0) or 0)
    true_missing_count = int(gap_summary.get("true_missing_cells", 0) or 0)
    excluded_pre_listing_count = int(gap_summary.get("excluded_pre_listing_cells", 0) or 0)
    excluded_not_active_count = int(gap_summary.get("excluded_not_active_cells", 0) or 0)
    excluded_suspended_count = int(gap_summary.get("excluded_suspended_cells", 0) or 0)
    excluded_static_count = int(gap_summary.get("excluded_static_universe_cells", 0) or 0)

    per_field_eligible_non_null = eligible_non_null_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    per_field_eligible_row_count = eligible_row_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    per_field_true_missing_count = true_missing_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    per_field_excluded_pre_listing = excluded_pre_listing_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    per_field_excluded_not_active = excluded_not_active_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    per_field_excluded_suspended = excluded_suspended_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    per_field_excluded_static = excluded_static_count // len(CORE_FIELDS) if CORE_FIELDS else 0
    for row in field_rows:
        row["eligible_non_null_ratio"] = eligible_coverage
        row["eligible_non_null_count"] = per_field_eligible_non_null
        row["eligible_row_count"] = per_field_eligible_row_count
        row["true_missing_count"] = per_field_true_missing_count
        row["excluded_pre_listing_count"] = per_field_excluded_pre_listing
        row["excluded_not_active_count"] = per_field_excluded_not_active
        row["excluded_suspended_or_no_trade_count"] = per_field_excluded_suspended
        row["excluded_static_universe_denominator_issue_count"] = per_field_excluded_static

    summary = {
        "candidate_dir": str(candidate_dir),
        "candidate_qlib_dir": str(candidate_dir),
        "universe": universe,
        "pit_constituent_accurate": False,
        "audit_start_date": start_date,
        "audit_end_date": end_date,
        "calendar_first_date": calendar_first_date,
        "calendar_last_date": calendar_last_date,
        "calendar_count": int(calendar_summary.get("calendar_count", 0) or 0),
        "calendar_contains_2026_04_30": calendar_contains_expected_end_date,
        "calendar_future_date_count": calendar_future_date_count,
        "csi800_symbol_count": int(registry_summary.get("instrument_total", 0)),
        "active_on_calendar_last_date": int(registry_summary.get("active_on_trade_date", 0)),
        "stale_end_date_count": int(registry_summary.get("stale_end_date_count", 0)),
        "core_market_field_coverage": eligible_coverage,
        "core_market_field_coverage_min": eligible_coverage,
        "naive_core_market_field_coverage": naive_coverage,
        "eligible_core_market_field_coverage": eligible_coverage,
        "eligible_core_market_field_threshold": 0.98,
        "excluded_pre_listing_cells": excluded_pre_listing_count,
        "excluded_not_active_cells": excluded_not_active_count,
        "excluded_suspended_or_no_trade_cells": excluded_suspended_count,
        "excluded_static_universe_denominator_issue_cells": excluded_static_count,
        "true_missing_cells": true_missing_count,
        "coverage_denominator_mode": "eligible_trading_cells",
        "coverage_gate_status": "pass" if eligible_coverage >= 0.98 else "fail",
        "duplicate_date_count": int(duplicate_date_count_total),
        "symbols_with_only_one_row": int(symbols_with_only_one_row),
        "future_date_count": int(future_date_count_total),
        "failed_symbol_count": int(len(failed_rows)),
        "hard_gate": {
            "active_on_latest_ge_750": int(registry_summary.get("active_on_trade_date", 0)) >= 750,
            "eligible_core_market_field_coverage_ge_0_98": eligible_coverage >= 0.98,
            "duplicate_date_count_eq_0": duplicate_date_count_total == 0,
            "symbols_with_only_one_row_eq_0": symbols_with_only_one_row == 0,
            "future_date_count_eq_0": future_date_count_total == 0,
        },
    }
    summary["go_no_go"] = "Go" if all(summary["hard_gate"].values()) else "No-Go"

    _write_json(output_dir / "candidate_validation_summary.json", summary)
    _write_csv(output_dir / "candidate_symbol_coverage.csv", symbol_rows, SYMBOL_COVERAGE_FIELDS)
    _write_csv(output_dir / "candidate_field_coverage.csv", field_rows, FIELD_COVERAGE_FIELDS)
    _write_csv(output_dir / "candidate_failed_symbols.csv", failed_rows, FAILED_SYMBOL_FIELDS)
    return summary


def plan_candidate_switch(
    *,
    base_dir: str | Path,
    candidate_qlib_dir: str | Path,
    validation_summary_path: str | Path,
    output_dir: str | Path = "experiments/ops_diagnostics/csi800_full_rebuild",
) -> dict[str, Any]:
    base_dir = Path(base_dir).resolve()
    output_dir = _normalize_path(base_dir, output_dir, default="experiments/ops_diagnostics/csi800_full_rebuild")
    candidate_dir = _normalize_path(base_dir, candidate_qlib_dir, default="data/qlib_bin_candidate")
    validation_path = _normalize_path(base_dir, validation_summary_path, default="experiments/ops_diagnostics/csi800_full_rebuild/candidate_validation_summary.json")
    summary = json.loads(validation_path.read_text(encoding="utf-8")) if validation_path.exists() else {}
    formal_dir = (base_dir / "data" / "qlib_bin").resolve()
    backup_dir = formal_dir.parent / f"qlib_bin_backup_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    plan = {
        "formal_qlib_dir": str(formal_dir),
        "candidate_qlib_dir": str(candidate_dir),
        "target_qlib_dir": str(formal_dir),
        "backup_qlib_dir": str(backup_dir),
        "validation_summary_path": str(validation_path),
        "validation_go_no_go": summary.get("go_no_go"),
        "candidate_validation_status": summary.get("go_no_go"),
        "backup_required": True,
        "post_switch_audit_required": True,
        "post_switch_daily_smoke_required": True,
        "steps": [
            f"backup {formal_dir} -> {backup_dir}",
            f"rename {candidate_dir} -> {formal_dir}",
            "re-run post-switch qlib instrument audit",
            "re-run post-switch daily smoke",
        ],
        "apply_executed": False,
    }
    _write_json(output_dir / "switch_plan.json", plan)
    return plan


def apply_candidate_switch(
    *,
    base_dir: str | Path,
    candidate_qlib_dir: str | Path,
    validation_summary_path: str | Path,
    output_dir: str | Path = "experiments/ops_diagnostics/csi800_full_rebuild",
) -> dict[str, Any]:
    plan = plan_candidate_switch(
        base_dir=base_dir,
        candidate_qlib_dir=candidate_qlib_dir,
        validation_summary_path=validation_summary_path,
        output_dir=output_dir,
    )
    if plan.get("validation_go_no_go") != "Go":
        result = {
            "status": "blocked",
            "reason": "candidate_validation_failed",
            "switch_plan": plan,
        }
        _write_json(_normalize_path(Path(base_dir).resolve(), output_dir, default="experiments/ops_diagnostics/csi800_full_rebuild") / "switch_result.json", result)
        return result
    raise RuntimeError("apply_candidate_switch is intentionally disabled unless explicitly requested by the user")
