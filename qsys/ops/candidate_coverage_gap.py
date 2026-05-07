from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import read_calendar_summary, read_instrument_file

CORE_FIELDS = ["$open", "$high", "$low", "$close", "$volume", "$amount"]
RAW_FIELD_MAP = {
    "$open": ["open"],
    "$high": ["high"],
    "$low": ["low"],
    "$close": ["close", "close_x", "close_y"],
    "$volume": ["volume", "vol"],
    "$amount": ["amount"],
}
EXCLUDED_GAP_REASONS = {
    "pre_listing_date",
    "not_in_instrument_active_range",
    "suspended_or_no_trade",
    "static_universe_denominator_issue",
}
TRUE_MISSING_GAP_REASONS = {
    "raw_missing",
    "raw_field_nan",
    "qlib_missing",
    "qlib_field_nan",
    "dump_conversion_issue",
    "unknown",
}
DETAIL_FIELDS = [
    "symbol",
    "date",
    "field",
    "raw_available",
    "raw_value_non_null",
    "qlib_available",
    "qlib_value_non_null",
    "instrument_active",
    "listed_before_date",
    "candidate_calendar_date",
    "gap_reason",
]
BY_SYMBOL_FIELDS = [
    "symbol",
    "total_cells",
    "qlib_non_null_cells",
    "naive_coverage",
    "eligible_cells",
    "eligible_non_null_cells",
    "eligible_coverage",
    "true_missing_cells",
    "excluded_pre_listing_cells",
    "excluded_static_universe_cells",
    "excluded_not_active_cells",
    "excluded_suspended_cells",
    "top_gap_reason",
]
BY_DATE_FIELDS = [
    "date",
    "total_cells",
    "qlib_non_null_cells",
    "naive_coverage",
    "eligible_cells",
    "eligible_non_null_cells",
    "eligible_coverage",
    "true_missing_cells",
    "excluded_pre_listing_cells",
    "excluded_static_universe_cells",
    "excluded_not_active_cells",
    "excluded_suspended_cells",
    "top_gap_reason",
]
REASON_BREAKDOWN_FIELDS = ["gap_reason", "cell_count", "category"]
SAMPLE_FIELDS = [
    "bucket",
    "symbol",
    "row_count",
    "first_date",
    "last_date",
    "non_null_ratio",
    "has_only_one_row",
    "has_2025_history",
    "has_2026_04_30",
]


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _normalize_path(base_dir: Path, path_value: str | Path | None, *, default: str) -> Path:
    path = Path(default) if path_value is None else Path(path_value)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def _normalize_date(value: Any) -> pd.Timestamp | None:
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            ts = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).normalize()


def _date_text(value: pd.Timestamp | None) -> str | None:
    return value.strftime("%Y-%m-%d") if value is not None else None


def _read_calendar_dates(
    candidate_dir: Path,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[pd.Timestamp]:
    day_path = candidate_dir / "calendars" / "day.txt"
    if not day_path.exists():
        return []
    df = pd.read_csv(day_path, header=None, names=["date"])
    dates = pd.to_datetime(df["date"], errors="coerce").dropna().dt.normalize().tolist()
    if start_date is None and end_date is None:
        return dates
    start_ts = pd.Timestamp(start_date) if start_date is not None else min(dates)
    end_ts = pd.Timestamp(end_date) if end_date is not None else max(dates)
    return [d for d in dates if start_ts <= d <= end_ts]


def _coalesce_numeric(df: pd.DataFrame, columns: list[str]) -> pd.Series:
    result = pd.Series(np.nan, index=df.index, dtype="float64")
    for column in columns:
        if column not in df.columns:
            continue
        cur = pd.to_numeric(df[column], errors="coerce")
        result = result.combine_first(cur)
    return result


def _normalize_raw_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "trade_date" not in df.columns:
        return pd.DataFrame(columns=["trade_date", *RAW_FIELD_MAP.keys(), "paused"])
    out = pd.DataFrame()
    out["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.normalize()
    out = out[out["trade_date"].notna()].copy()
    for qlib_field, raw_candidates in RAW_FIELD_MAP.items():
        out[qlib_field] = _coalesce_numeric(df.loc[out.index], raw_candidates)
    out["paused"] = pd.to_numeric(df.loc[out.index, "paused"], errors="coerce") if "paused" in df.columns else pd.NA
    out = out.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
    return out


def _load_raw_frame(raw_dir: Path, symbol: str) -> pd.DataFrame:
    path = raw_dir / f"{symbol}.feather"
    if not path.exists():
        return pd.DataFrame(columns=["trade_date", *RAW_FIELD_MAP.keys(), "paused"])
    return _normalize_raw_frame(pd.read_feather(path))


def _load_qlib_frame(adapter: QlibAdapter, symbols: list[str], fields: list[str], start_date: str, end_date: str) -> pd.DataFrame:
    frame = adapter.get_features(symbols, fields, start_time=start_date, end_time=end_date)
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["instrument", "datetime", *fields])
    work = frame.reset_index().copy()
    work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce").dt.normalize()
    work["instrument"] = work["instrument"].astype(str)
    return work


def classify_gap_reason(
    *,
    instrument_active: bool,
    listed_before_date: bool,
    instrument_start_date: pd.Timestamp | None,
    raw_first_date: pd.Timestamp | None,
    raw_last_date: pd.Timestamp | None,
    raw_available: bool,
    raw_value_non_null: bool,
    qlib_available: bool,
    qlib_value_non_null: bool,
    paused_value: float | None,
    date: pd.Timestamp,
) -> str:
    if not instrument_active:
        return "not_in_instrument_active_range"
    if not listed_before_date:
        if instrument_start_date is not None and raw_first_date is not None and instrument_start_date < raw_first_date:
            return "static_universe_denominator_issue"
        return "pre_listing_date"
    if qlib_value_non_null:
        return "ok"
    if not raw_available:
        if paused_value == 1:
            return "suspended_or_no_trade"
        if raw_first_date is not None and raw_last_date is not None and raw_first_date < date < raw_last_date:
            return "suspended_or_no_trade"
        return "raw_missing"
    if not raw_value_non_null:
        return "raw_field_nan"
    if not qlib_available:
        return "qlib_missing"
    return "qlib_field_nan"


def _coverage(value: int, total: int) -> float:
    return float(value / total) if total else 0.0


def _build_recommendation(summary: dict[str, Any], reason_counter: Counter[str], *, threshold: float = 0.98) -> dict[str, Any]:
    naive = float(summary["naive_core_market_field_coverage"])
    eligible = float(summary["eligible_core_market_field_coverage"])
    validator_observed = float(summary.get("validator_observed_core_market_field_coverage") or naive)
    raw_issue_cells = reason_counter["raw_missing"] + reason_counter["raw_field_nan"]
    qlib_issue_cells = reason_counter["qlib_missing"] + reason_counter["qlib_field_nan"] + reason_counter["dump_conversion_issue"]
    field_nan_cells = reason_counter["raw_field_nan"] + reason_counter["qlib_field_nan"]

    root_cause = "unknown"
    should_rebuild_candidate_again = False
    should_adjust_validator_denominator = False
    should_targeted_raw_backfill = False
    should_targeted_qlib_refresh = False

    if eligible >= threshold and validator_observed < threshold:
        root_cause = "validator_denominator_too_strict"
        should_adjust_validator_denominator = True
        recommendation = (
            "主要问题不是核心行情真缺，而是 static CSI800 bootstrap 下的上市前/非活跃区间被一并算进 validator 分母。"
            "先修 validator denominator，再重跑 candidate validation。"
        )
    elif eligible < threshold:
        if qlib_issue_cells > raw_issue_cells and qlib_issue_cells >= field_nan_cells:
            root_cause = "qlib_dump_missing" if reason_counter["qlib_missing"] >= reason_counter["qlib_field_nan"] else "field_nan"
            should_targeted_qlib_refresh = True
            should_rebuild_candidate_again = True
            recommendation = "eligible coverage 仍未达标，缺口主要在 qlib dump / field NaN，先做 targeted qlib refresh，再重跑 validation。"
        elif raw_issue_cells > qlib_issue_cells:
            root_cause = "raw_missing" if reason_counter["raw_missing"] >= reason_counter["raw_field_nan"] else "field_nan"
            should_targeted_raw_backfill = True
            should_rebuild_candidate_again = True
            recommendation = "eligible coverage 仍未达标，缺口主要在 raw 缺失/字段空值，先做 targeted raw backfill，再重跑 validation。"
        elif field_nan_cells > 0:
            root_cause = "field_nan"
            should_rebuild_candidate_again = True
            recommendation = "eligible coverage 仍未达标，缺口以字段空值为主，需要先查具体字段链路，再重跑 validation。"
        else:
            root_cause = "mixed"
            should_rebuild_candidate_again = True
            recommendation = "eligible coverage 仍未达标，raw 与 qlib 都有缺口，需分批修复后再重跑 validation。"
    else:
        root_cause = "unknown"
        recommendation = "eligible coverage 已过阈值，但当前证据仍不足以直接切换，先保留 No-Go 并补充 validator 口径复核。"

    return {
        "root_cause": root_cause,
        "should_rebuild_candidate_again": should_rebuild_candidate_again,
        "should_adjust_validator_denominator": should_adjust_validator_denominator,
        "should_targeted_raw_backfill": should_targeted_raw_backfill,
        "should_targeted_qlib_refresh": should_targeted_qlib_refresh,
        "safe_to_switch_candidate": False,
        "recommendation": recommendation,
    }


def build_candidate_gap_audit(
    *,
    calendar_dates: list[pd.Timestamp],
    instrument_df: pd.DataFrame,
    raw_frames: dict[str, pd.DataFrame],
    qlib_frame: pd.DataFrame,
    validation_summary: dict[str, Any] | None,
    candidate_qlib_path: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    symbols = sorted(instrument_df["instrument"].astype(str).unique().tolist())
    calendar_dates = sorted(calendar_dates)
    qlib_groups = {
        symbol: group.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last").set_index("datetime")
        for symbol, group in qlib_frame.groupby("instrument")
    }

    detail_rows: list[dict[str, Any]] = []
    sample_stats: dict[str, dict[str, Any]] = {}
    reason_counter: Counter[str] = Counter()
    symbol_counter: dict[str, Counter[str]] = defaultdict(Counter)
    date_counter: dict[str, Counter[str]] = defaultdict(Counter)

    total_cells = int(len(qlib_frame) * len(CORE_FIELDS)) if not qlib_frame.empty else 0
    qlib_non_null_cells = int(qlib_frame[CORE_FIELDS].count().sum().sum()) if not qlib_frame.empty else 0
    eligible_cells = total_cells
    eligible_non_null_cells = qlib_non_null_cells
    excluded_pre_listing_cells = 0
    excluded_static_universe_cells = 0
    excluded_not_active_cells = 0
    excluded_suspended_cells = 0
    true_missing_cells = 0

    symbol_non_null = (
        qlib_frame.groupby("instrument")[CORE_FIELDS].count().sum(axis=1).to_dict() if not qlib_frame.empty else {}
    )
    date_non_null = qlib_frame.groupby("datetime")[CORE_FIELDS].count().sum(axis=1).to_dict() if not qlib_frame.empty else {}
    date_total_rows = qlib_frame.groupby("datetime").size().to_dict() if not qlib_frame.empty else {}
    symbol_stats = {
        symbol: {
            "symbol": symbol,
            "total_cells": int(len(qlib_groups.get(symbol, pd.DataFrame())) * len(CORE_FIELDS)),
            "qlib_non_null_cells": int(symbol_non_null.get(symbol, 0)),
            "eligible_cells": int(len(qlib_groups.get(symbol, pd.DataFrame())) * len(CORE_FIELDS)),
            "eligible_non_null_cells": int(symbol_non_null.get(symbol, 0)),
            "true_missing_cells": 0,
            "excluded_pre_listing_cells": 0,
            "excluded_static_universe_cells": 0,
            "excluded_not_active_cells": 0,
            "excluded_suspended_cells": 0,
        }
        for symbol in symbols
    }
    date_stats = {
        _date_text(date): {
            "date": _date_text(date),
            "total_cells": int(date_total_rows.get(date, 0) * len(CORE_FIELDS)),
            "qlib_non_null_cells": int(date_non_null.get(date, 0)),
            "eligible_cells": int(date_total_rows.get(date, 0) * len(CORE_FIELDS)),
            "eligible_non_null_cells": int(date_non_null.get(date, 0)),
            "true_missing_cells": 0,
            "excluded_pre_listing_cells": 0,
            "excluded_static_universe_cells": 0,
            "excluded_not_active_cells": 0,
            "excluded_suspended_cells": 0,
        }
        for date in calendar_dates
    }

    instrument_map = {
        str(row.instrument): {
            "start_date": _normalize_date(row.start_date),
            "end_date": _normalize_date(row.end_date),
        }
        for row in instrument_df.itertuples(index=False)
    }

    for symbol in symbols:
        raw_frame = raw_frames.get(symbol, pd.DataFrame(columns=["trade_date", *RAW_FIELD_MAP.keys(), "paused"]))
        raw_map = raw_frame.set_index("trade_date") if not raw_frame.empty else pd.DataFrame().set_index(pd.Index([], name="trade_date"))
        raw_first_date = raw_frame["trade_date"].min() if not raw_frame.empty else None
        raw_last_date = raw_frame["trade_date"].max() if not raw_frame.empty else None
        qlib_symbol_frame = qlib_groups.get(symbol, pd.DataFrame(columns=[*CORE_FIELDS]))
        qlib_dates = qlib_symbol_frame.index.tolist() if not qlib_symbol_frame.empty else []
        qlib_problem_dates = qlib_symbol_frame.index[qlib_symbol_frame[CORE_FIELDS].isna().any(axis=1)].tolist() if not qlib_symbol_frame.empty else []

        sample_stats[symbol] = {
            "raw_first_date": _date_text(raw_first_date),
            "raw_last_date": _date_text(raw_last_date),
            "qlib_row_count": int(len(qlib_symbol_frame)),
            "qlib_first_date": _date_text(min(qlib_dates)) if qlib_dates else None,
            "qlib_last_date": _date_text(max(qlib_dates)) if qlib_dates else None,
        }
        inst_start = instrument_map.get(symbol, {}).get("start_date")
        inst_end = instrument_map.get(symbol, {}).get("end_date")
        s_stats = symbol_stats[symbol]

        for date in qlib_problem_dates:
            date_text = _date_text(date)
            d_stats = date_stats[date_text]
            instrument_active = True
            listed_before_date = bool(raw_first_date is not None and raw_first_date <= date)
            raw_row = raw_map.loc[date] if not raw_frame.empty and date in raw_map.index else None
            if isinstance(raw_row, pd.DataFrame):
                raw_row = raw_row.iloc[-1]
            qlib_row = qlib_symbol_frame.loc[date] if not qlib_symbol_frame.empty and date in qlib_symbol_frame.index else None
            if isinstance(qlib_row, pd.DataFrame):
                qlib_row = qlib_row.iloc[-1]
            paused_value = None if raw_row is None else pd.to_numeric(pd.Series([raw_row.get("paused")]), errors="coerce").iloc[0]
            paused_value = None if pd.isna(paused_value) else float(paused_value)

            for field in CORE_FIELDS:
                qlib_available = qlib_row is not None
                qlib_value_non_null = bool(qlib_available and pd.notna(pd.to_numeric(pd.Series([qlib_row.get(field)]), errors="coerce").iloc[0]))
                if qlib_value_non_null:
                    continue
                raw_available = raw_row is not None
                raw_value_non_null = bool(raw_available and pd.notna(pd.to_numeric(pd.Series([raw_row.get(field)]), errors="coerce").iloc[0]))
                gap_reason = classify_gap_reason(
                    instrument_active=instrument_active,
                    listed_before_date=listed_before_date,
                    instrument_start_date=inst_start,
                    raw_first_date=raw_first_date,
                    raw_last_date=raw_last_date,
                    raw_available=raw_available,
                    raw_value_non_null=raw_value_non_null,
                    qlib_available=qlib_available,
                    qlib_value_non_null=qlib_value_non_null,
                    paused_value=paused_value,
                    date=date,
                )

                if gap_reason in EXCLUDED_GAP_REASONS:
                    eligible_cells -= 1
                    s_stats["eligible_cells"] -= 1
                    d_stats["eligible_cells"] -= 1
                    if gap_reason == "pre_listing_date":
                        excluded_pre_listing_cells += 1
                        s_stats["excluded_pre_listing_cells"] += 1
                        d_stats["excluded_pre_listing_cells"] += 1
                    elif gap_reason == "static_universe_denominator_issue":
                        excluded_static_universe_cells += 1
                        s_stats["excluded_static_universe_cells"] += 1
                        d_stats["excluded_static_universe_cells"] += 1
                    elif gap_reason == "not_in_instrument_active_range":
                        excluded_not_active_cells += 1
                        s_stats["excluded_not_active_cells"] += 1
                        d_stats["excluded_not_active_cells"] += 1
                    elif gap_reason == "suspended_or_no_trade":
                        excluded_suspended_cells += 1
                        s_stats["excluded_suspended_cells"] += 1
                        d_stats["excluded_suspended_cells"] += 1
                else:
                    true_missing_cells += 1
                    s_stats["true_missing_cells"] += 1
                    d_stats["true_missing_cells"] += 1

                reason_counter[gap_reason] += 1
                symbol_counter[symbol][gap_reason] += 1
                date_counter[date_text][gap_reason] += 1
                detail_rows.append(
                    {
                        "symbol": symbol,
                        "date": date_text,
                        "field": field,
                        "raw_available": raw_available,
                        "raw_value_non_null": raw_value_non_null,
                        "qlib_available": qlib_available,
                        "qlib_value_non_null": qlib_value_non_null,
                        "instrument_active": instrument_active,
                        "listed_before_date": listed_before_date,
                        "candidate_calendar_date": True,
                        "gap_reason": gap_reason,
                    }
                )

    by_symbol_rows = []
    for symbol in symbols:
        stats = symbol_stats[symbol]
        reason_counts = symbol_counter[symbol]
        top_gap_reason = reason_counts.most_common(1)[0][0] if reason_counts else "ok"
        by_symbol_rows.append(
            {
                **stats,
                "naive_coverage": _coverage(stats["qlib_non_null_cells"], stats["total_cells"]),
                "eligible_coverage": _coverage(stats["eligible_non_null_cells"], stats["eligible_cells"]),
                "top_gap_reason": top_gap_reason,
            }
        )

    by_date_rows = []
    for date_text in sorted(date_stats):
        stats = date_stats[date_text]
        reason_counts = date_counter[date_text]
        top_gap_reason = reason_counts.most_common(1)[0][0] if reason_counts else "ok"
        by_date_rows.append(
            {
                **stats,
                "naive_coverage": _coverage(stats["qlib_non_null_cells"], stats["total_cells"]),
                "eligible_coverage": _coverage(stats["eligible_non_null_cells"], stats["eligible_cells"]),
                "top_gap_reason": top_gap_reason,
            }
        )

    reason_rows = []
    for reason, cell_count in reason_counter.most_common():
        category = "excluded" if reason in EXCLUDED_GAP_REASONS else "true_missing"
        reason_rows.append({"gap_reason": reason, "cell_count": int(cell_count), "category": category})

    summary = {
        "candidate_qlib_path": candidate_qlib_path,
        "audit_start_date": start_date,
        "audit_end_date": end_date,
        "calendar_first_date": validation_summary.get("calendar_first_date") if validation_summary else _date_text(calendar_dates[0]) if calendar_dates else None,
        "calendar_last_date": validation_summary.get("calendar_last_date") if validation_summary else _date_text(calendar_dates[-1]) if calendar_dates else None,
        "symbol_count": len(symbols),
        "active_on_latest": validation_summary.get("active_on_calendar_last_date") if validation_summary else int(len(symbols)),
        "core_market_field_coverage": validation_summary.get("core_market_field_coverage_min") if validation_summary else None,
        "duplicate_date_count": validation_summary.get("duplicate_date_count") if validation_summary else None,
        "symbols_with_only_one_row": validation_summary.get("symbols_with_only_one_row") if validation_summary else None,
        "future_date_count": validation_summary.get("future_date_count") if validation_summary else None,
        "failed_symbol_count": validation_summary.get("failed_symbol_count") if validation_summary else None,
        "validator_observed_core_market_field_coverage": validation_summary.get("core_market_field_coverage_min") if validation_summary else None,
        "naive_core_market_field_coverage": _coverage(qlib_non_null_cells, total_cells),
        "eligible_core_market_field_coverage": _coverage(eligible_non_null_cells, eligible_cells),
        "excluded_pre_listing_cells": excluded_pre_listing_cells,
        "excluded_static_universe_cells": excluded_static_universe_cells,
        "excluded_not_active_cells": excluded_not_active_cells,
        "excluded_suspended_cells": excluded_suspended_cells,
        "true_missing_cells": true_missing_cells,
        "total_possible_cells": total_cells,
        "qlib_non_null_cells": qlib_non_null_cells,
        "eligible_cells": eligible_cells,
        "eligible_non_null_cells": eligible_non_null_cells,
        "worst_symbols_by_missing_cells": sorted(by_symbol_rows, key=lambda row: (-row["true_missing_cells"], row["symbol"]))[:20],
        "worst_dates_by_missing_cells": sorted(by_date_rows, key=lambda row: (-row["true_missing_cells"], row["date"]))[:20],
        "worst_fields_by_missing_cells": [
            {"field": field, "missing_cells": int(sum(1 for row in detail_rows if row["field"] == field and row["gap_reason"] not in EXCLUDED_GAP_REASONS))}
            for field in CORE_FIELDS
        ],
        "raw_first_last_by_symbol": sample_stats,
    }
    summary["worst_fields_by_missing_cells"] = sorted(summary["worst_fields_by_missing_cells"], key=lambda row: (-row["missing_cells"], row["field"]))[:20]
    recommendation = _build_recommendation(summary, reason_counter)

    return {
        "summary": summary,
        "detail_rows": detail_rows,
        "by_symbol_rows": by_symbol_rows,
        "by_date_rows": by_date_rows,
        "reason_rows": reason_rows,
        "recommendation": recommendation,
    }


def _pick_sample_symbols(by_symbol_rows: list[dict[str, Any]], raw_first_last_by_symbol: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    best = [row["symbol"] for row in sorted(by_symbol_rows, key=lambda row: (-row["eligible_coverage"], row["symbol"]))[:5]]
    worst = [row["symbol"] for row in sorted(by_symbol_rows, key=lambda row: (row["naive_coverage"], row["symbol"]))[:5]]
    new_listing = [
        symbol for symbol, _ in sorted(
            ((symbol, stats.get("raw_first_date")) for symbol, stats in raw_first_last_by_symbol.items() if stats.get("raw_first_date")),
            key=lambda item: item[1],
            reverse=True,
        )[:5]
    ]
    core_candidates = ["600519.SH", "300750.SZ", "601318.SH", "601398.SH", "600036.SH", "000001.SZ", "000333.SZ"]
    core = [symbol for symbol in core_candidates if symbol in raw_first_last_by_symbol][:5]

    ordered: list[tuple[str, str]] = []
    for bucket, symbols in [
        ("A_best", best),
        ("B_worst", worst),
        ("C_new_listing", new_listing),
        ("D_core_largecap", core),
    ]:
        for symbol in symbols:
            ordered.append((bucket, symbol))
    return ordered


def collect_sample_history_rows(
    *,
    adapter: QlibAdapter,
    by_symbol_rows: list[dict[str, Any]],
    raw_first_last_by_symbol: dict[str, dict[str, Any]],
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket, symbol in _pick_sample_symbols(by_symbol_rows, raw_first_last_by_symbol):
        frame = adapter.get_features([symbol], CORE_FIELDS, start_time=start_date, end_time=end_date)
        if frame is None or frame.empty:
            rows.append(
                {
                    "bucket": bucket,
                    "symbol": symbol,
                    "row_count": 0,
                    "first_date": None,
                    "last_date": None,
                    "non_null_ratio": 0.0,
                    "has_only_one_row": False,
                    "has_2025_history": False,
                    "has_2026_04_30": False,
                }
            )
            continue
        work = frame.reset_index().copy()
        work["datetime"] = pd.to_datetime(work["datetime"], errors="coerce").dt.normalize()
        non_null_ratio = float(work[CORE_FIELDS].apply(pd.to_numeric, errors="coerce").notna().sum().sum() / (len(work) * len(CORE_FIELDS))) if len(work) else 0.0
        rows.append(
            {
                "bucket": bucket,
                "symbol": symbol,
                "row_count": int(len(work)),
                "first_date": _date_text(work["datetime"].min()) if len(work) else None,
                "last_date": _date_text(work["datetime"].max()) if len(work) else None,
                "non_null_ratio": non_null_ratio,
                "has_only_one_row": bool(len(work) <= 1),
                "has_2025_history": bool((work["datetime"] < pd.Timestamp("2026-01-01")).any()),
                "has_2026_04_30": bool((work["datetime"] == pd.Timestamp("2026-04-30")).any()),
            }
        )
    return rows


def write_candidate_gap_artifacts(
    *,
    output_dir: Path,
    audit: dict[str, Any],
    sample_rows: list[dict[str, Any]],
) -> None:
    _write_json(output_dir / "coverage_gap_summary.json", audit["summary"])
    _write_csv(output_dir / "core_field_gap_by_symbol.csv", audit["by_symbol_rows"], BY_SYMBOL_FIELDS)
    _write_csv(output_dir / "core_field_gap_by_date.csv", audit["by_date_rows"], BY_DATE_FIELDS)
    _write_csv(output_dir / "core_field_gap_detail.csv", audit["detail_rows"], DETAIL_FIELDS)
    _write_csv(output_dir / "gap_reason_breakdown.csv", audit["reason_rows"], REASON_BREAKDOWN_FIELDS)
    _write_json(output_dir / "candidate_validation_recommendation.json", audit["recommendation"])
    _write_csv(output_dir / "sample_symbol_history.csv", sample_rows, SAMPLE_FIELDS)


def run_candidate_gap_audit(
    *,
    base_dir: str | Path,
    candidate_qlib_dir: str | Path,
    universe: str,
    start_date: str,
    end_date: str,
    output_dir: str | Path = "experiments/ops_diagnostics/csi800_candidate_gap_audit",
    validation_summary_path: str | Path | None = "experiments/ops_diagnostics/csi800_full_rebuild/candidate_validation_summary.json",
) -> dict[str, Any]:
    base_dir = Path(base_dir).resolve()
    candidate_dir = _normalize_path(base_dir, candidate_qlib_dir, default="data/qlib_bin_candidate_20260430")
    output_dir = _normalize_path(base_dir, output_dir, default="experiments/ops_diagnostics/csi800_candidate_gap_audit")
    validation_path = _normalize_path(base_dir, validation_summary_path, default="experiments/ops_diagnostics/csi800_full_rebuild/candidate_validation_summary.json") if validation_summary_path else None

    validation_summary = {}
    if validation_path and validation_path.exists():
        validation_summary = json.loads(validation_path.read_text(encoding="utf-8"))

    adapter = QlibAdapter(qlib_dir=candidate_dir)
    adapter.init_qlib()
    calendar_dates = _read_calendar_dates(candidate_dir)
    instrument_df = read_instrument_file(candidate_dir / "instruments" / f"{universe}.txt")
    symbols = sorted(instrument_df["instrument"].astype(str).unique().tolist())
    raw_frames = {symbol: _load_raw_frame(adapter.raw_dir, symbol) for symbol in symbols}
    qlib_frame = _load_qlib_frame(
        adapter,
        symbols,
        CORE_FIELDS,
        _date_text(calendar_dates[0]) if calendar_dates else start_date,
        _date_text(calendar_dates[-1]) if calendar_dates else end_date,
    )

    audit = build_candidate_gap_audit(
        calendar_dates=calendar_dates,
        instrument_df=instrument_df,
        raw_frames=raw_frames,
        qlib_frame=qlib_frame,
        validation_summary=validation_summary,
        candidate_qlib_path=str(candidate_dir),
        start_date=start_date,
        end_date=end_date,
    )
    sample_rows = collect_sample_history_rows(
        adapter=adapter,
        by_symbol_rows=audit["by_symbol_rows"],
        raw_first_last_by_symbol=audit["summary"]["raw_first_last_by_symbol"],
        start_date=start_date,
        end_date=end_date,
    )
    audit["summary"]["sample_symbol_history"] = sample_rows

    write_candidate_gap_artifacts(output_dir=output_dir, audit=audit, sample_rows=sample_rows)

    return {
        "coverage_gap_summary": audit["summary"],
        "candidate_validation_recommendation": audit["recommendation"],
        "sample_symbol_history": sample_rows,
    }
