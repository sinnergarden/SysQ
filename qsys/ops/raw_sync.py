from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from qsys.data.collector import TushareCollector
from qsys.data.storage import StockDataStore


RAW_PLAN_COLUMNS = [
    "symbol",
    "selected_for_apply",
    "raw_last_date_before",
    "raw_last_date_after",
    "target_start_date",
    "target_end_date",
    "attempt_started_at",
    "attempt_ended_at",
    "rows_before",
    "rows_after",
    "rows_added",
    "status",
    "error",
]


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


def _now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _normalize_date(value: object) -> str | None:
    if value is None or value == "":
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            ts = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _count_rows(store: StockDataStore, symbol: str) -> tuple[int, str | None]:
    existing = store.load_daily(symbol)
    if existing is None or existing.empty or "trade_date" not in existing.columns:
        return 0, None
    return int(len(existing)), _normalize_date(existing["trade_date"].max())


def build_raw_update_plan(
    *,
    store: StockDataStore,
    symbols: list[str],
    target_date: str,
    lookback_days: int,
    selected_symbols: set[str] | None = None,
    resume_success_symbols: set[str] | None = None,
) -> list[dict[str, Any]]:
    target_ts = pd.Timestamp(target_date)
    selected_symbols = selected_symbols or set(symbols)
    resume_success_symbols = resume_success_symbols or set()
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        rows_before, raw_last_date = _count_rows(store, symbol)
        start_ts = target_ts - pd.Timedelta(days=lookback_days)
        if raw_last_date:
            start_ts = min(target_ts, max(pd.Timestamp(raw_last_date), start_ts))
        selected_for_apply = symbol in selected_symbols
        status = "planned" if selected_for_apply else "skipped"
        error = ""
        if symbol in resume_success_symbols:
            selected_for_apply = False
            status = "skipped"
            error = "resume_skip_previous_success"
        rows.append(
            {
                "symbol": symbol,
                "selected_for_apply": selected_for_apply,
                "raw_last_date_before": raw_last_date,
                "raw_last_date_after": raw_last_date,
                "target_start_date": start_ts.strftime("%Y-%m-%d"),
                "target_end_date": target_date,
                "attempt_started_at": "",
                "attempt_ended_at": "",
                "rows_before": rows_before,
                "rows_after": rows_before,
                "rows_added": 0,
                "status": status,
                "error": error,
            }
        )
    return rows


def load_success_symbols_from_plan(plan_path: Path) -> set[str]:
    if not plan_path.exists():
        return set()
    with plan_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return {str(row.get("symbol", "")).strip() for row in rows if str(row.get("status", "")).strip() == "success"}


def run_targeted_raw_update(
    *,
    symbols: list[str],
    target_date: str,
    lookback_days: int,
    apply: bool,
    output_dir: Path,
    universe: str = "csi300",
    selected_symbols: set[str] | None = None,
    resume_success_symbols: set[str] | None = None,
) -> tuple[dict[str, Any], Path, Path, list[str]]:
    store = StockDataStore()
    plan_rows = build_raw_update_plan(
        store=store,
        symbols=symbols,
        target_date=target_date,
        lookback_days=lookback_days,
        selected_symbols=selected_symbols,
        resume_success_symbols=resume_success_symbols,
    )
    affected_symbols: list[str] = []
    selected_count = sum(1 for row in plan_rows if row["selected_for_apply"])

    if not apply:
        pass
    elif selected_count == 0:
        pass
    else:
        try:
            collector = TushareCollector()
        except Exception as exc:
            error_text = str(exc)
            for row in plan_rows:
                if row["selected_for_apply"]:
                    row["status"] = "failed"
                    row["error"] = error_text
                    row["attempt_started_at"] = _now_text()
                    row["attempt_ended_at"] = row["attempt_started_at"]
        else:
            for row in plan_rows:
                if not row["selected_for_apply"]:
                    continue
                symbol = str(row["symbol"])
                row["attempt_started_at"] = _now_text()
                try:
                    collector.update_universe_history(
                        universe=[symbol],
                        start_date=str(row["target_start_date"]).replace("-", ""),
                        end_date=str(row["target_end_date"]).replace("-", ""),
                    )
                    rows_after, raw_last_date_after = _count_rows(store, symbol)
                    row["rows_after"] = rows_after
                    row["raw_last_date_after"] = raw_last_date_after
                    row["rows_added"] = max(rows_after - int(row["rows_before"]), 0)
                    row["status"] = "success" if row["rows_added"] > 0 or (raw_last_date_after and raw_last_date_after >= target_date) else "unchanged"
                    if row["status"] == "success":
                        affected_symbols.append(symbol)
                except Exception as exc:
                    row["status"] = "failed"
                    row["error"] = str(exc)
                    rows_after, raw_last_date_after = _count_rows(store, symbol)
                    row["rows_after"] = rows_after
                    row["raw_last_date_after"] = raw_last_date_after
                    row["rows_added"] = max(rows_after - int(row["rows_before"]), 0)
                finally:
                    row["attempt_ended_at"] = _now_text()

    failed_count = sum(1 for row in plan_rows if row["status"] == "failed")
    success_count = sum(1 for row in plan_rows if row["status"] == "success")
    unchanged_count = sum(1 for row in plan_rows if row["status"] == "unchanged")
    raw_on_target = sum(1 for row in plan_rows if row["raw_last_date_after"] and str(row["raw_last_date_after"]) >= target_date)
    if not apply:
        status = "skipped"
    elif selected_count == 0:
        status = "skipped"
    elif failed_count == 0:
        status = "success"
    elif failed_count == selected_count:
        status = "failed"
    else:
        status = "partial"

    summary = {
        "universe": universe,
        "target_symbol_count": len(symbols),
        "selected_symbol_count": selected_count,
        "symbols_attempted": selected_count if apply else 0,
        "symbols_updated": success_count,
        "symbols_failed": failed_count,
        "symbols_unchanged": unchanged_count,
        "symbols_with_raw_on_target": raw_on_target,
        "status": status,
    }
    plan_path = _write_csv(output_dir / "raw_update_plan.csv", plan_rows, RAW_PLAN_COLUMNS)
    summary_path = _write_json(output_dir / "raw_update_summary.json", summary)
    return summary, plan_path, summary_path, sorted(set(affected_symbols))
