from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.utils.json_io import write_csv, write_json

import pandas as pd

from qsys.data.adapter import QlibAdapter
from qsys.data.collector import TushareCollector
from qsys.data.storage import StockDataStore
from qsys.ops.qlib_sync import refresh_selected_symbols_from_raw

RAW_STATUS_COLUMNS = [
    "symbol",
    "list_date",
    "raw_exists",
    "raw_last_date",
    "needs_backfill",
    "backfill_reason",
]

BATCH_PLAN_COLUMNS = [
    "batch_id",
    "batch_type",
    "symbol_count",
    "start_date",
    "end_date",
    "status",
    "error",
    "symbols",
]


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



def _scan_raw_status(store: StockDataStore, symbols: list[str], *, target_date: str, list_date_map: dict[str, str | None]) -> list[dict[str, Any]]:
    target_ts = pd.Timestamp(target_date)
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        frame = store.load_daily(symbol)
        raw_last_date = None
        raw_exists = frame is not None and not frame.empty and "trade_date" in frame.columns
        if raw_exists:
            raw_last_date = _normalize_date(frame["trade_date"].max())
        needs_backfill = raw_last_date is None or pd.Timestamp(raw_last_date) < target_ts
        if raw_last_date is None:
            reason = "raw_missing"
        elif pd.Timestamp(raw_last_date) < target_ts:
            reason = "raw_stale"
        else:
            reason = "up_to_date"
        rows.append(
            {
                "symbol": symbol,
                "list_date": list_date_map.get(symbol),
                "raw_exists": raw_exists,
                "raw_last_date": raw_last_date,
                "needs_backfill": needs_backfill,
                "backfill_reason": reason,
            }
        )
    return rows



def build_full_universe_backfill_plan(
    *,
    store: StockDataStore,
    target_date: str,
    batch_size: int,
    missing_start_date: str = "2010-01-01",
    stale_lookback_days: int = 20,
    max_batches: int | None = None,
    symbols: list[str] | None = None,
    full_backfill: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Optional explicit symbol set (e.g. a PIT universe registry) restricts the
    # plan to exactly those symbols; otherwise the full stock list is used.
    stock_df = store.get_stock_list()
    if symbols is not None:
        symbols = sorted({str(s).strip().upper() for s in symbols if str(s).strip()})
    else:
        if stock_df is None or stock_df.empty or "ts_code" not in stock_df.columns:
            return [], []
        symbols = sorted(stock_df["ts_code"].dropna().astype(str).unique().tolist())

    list_date_map = {}
    if stock_df is not None and not stock_df.empty and "list_date" in stock_df.columns:
        list_date_map = {
            str(row["ts_code"]): _normalize_date(row.get("list_date"))
            for _, row in stock_df[["ts_code", "list_date"]].drop_duplicates(subset=["ts_code"]).iterrows()
        }

    status_rows = _scan_raw_status(store, symbols, target_date=target_date, list_date_map=list_date_map)
    missing_rows = [row for row in status_rows if row["backfill_reason"] == "raw_missing"]
    stale_rows = [row for row in status_rows if row["backfill_reason"] == "raw_stale"]

    # full_backfill: every symbol (missing OR stale) is fetched from
    # missing_start_date and written non-incrementally — used when the
    # existing "raw" is a stub/placeholder (e.g. a single sync row) rather
    # than genuine history that only needs a short catch-up.
    backfill_rows = status_rows if full_backfill else missing_rows
    stale_shortfall_rows = [] if full_backfill else stale_rows

    plan_rows: list[dict[str, Any]] = []
    batch_id = 0

    for start in range(0, len(backfill_rows), batch_size):
        chunk = backfill_rows[start : start + batch_size]
        if not chunk:
            continue
        batch_id += 1
        plan_rows.append(
            {
                "batch_id": batch_id,
                "batch_type": "full_backfill" if full_backfill else "missing_raw",
                "symbol_count": len(chunk),
                "start_date": missing_start_date,
                "end_date": target_date,
                "status": "planned",
                "error": "",
                "symbols": ",".join(row["symbol"] for row in chunk),
            }
        )
        if max_batches is not None and len(plan_rows) >= max_batches:
            return status_rows, plan_rows

    target_ts = pd.Timestamp(target_date)
    for start in range(0, len(stale_shortfall_rows), batch_size):
        chunk = stale_shortfall_rows[start : start + batch_size]
        if not chunk:
            continue
        batch_id += 1
        chunk_starts = []
        for row in chunk:
            raw_last = row.get("raw_last_date")
            if raw_last:
                chunk_starts.append(max(pd.Timestamp(raw_last), target_ts - pd.Timedelta(days=stale_lookback_days)))
            else:
                chunk_starts.append(target_ts - pd.Timedelta(days=stale_lookback_days))
        start_date = min(chunk_starts).strftime("%Y-%m-%d") if chunk_starts else target_date
        plan_rows.append(
            {
                "batch_id": batch_id,
                "batch_type": "stale_raw",
                "symbol_count": len(chunk),
                "start_date": start_date,
                "end_date": target_date,
                "status": "planned",
                "error": "",
                "symbols": ",".join(row["symbol"] for row in chunk),
            }
        )
        if max_batches is not None and len(plan_rows) >= max_batches:
            return status_rows, plan_rows

    return status_rows, plan_rows



def run_full_universe_backfill(
    base_dir: str | Path,
    *,
    target_date: str | None = None,
    batch_size: int = 50,
    max_batches: int | None = None,
    missing_start_date: str = "2010-01-01",
    stale_lookback_days: int = 20,
    apply: bool = False,
    refresh_qlib: bool = True,
    triggered_by: str = "manual",
    symbols: list[str] | None = None,
    full_backfill: bool = False,
) -> dict[str, Any]:
    base_dir = Path(base_dir)
    now = datetime.now().replace(microsecond=0)
    run_id = now.strftime("full_backfill_%Y%m%d_%H%M%S")
    output_dir = base_dir / "runs" / "full_universe_backfill" / run_id

    adapter = QlibAdapter()
    store = StockDataStore()

    qlib_last_ts = adapter.get_last_qlib_date()
    resolved_target_date = target_date or (qlib_last_ts.strftime("%Y-%m-%d") if qlib_last_ts is not None else None)
    if resolved_target_date is None:
        latest = store.get_global_latest_date()
        resolved_target_date = _normalize_date(latest) or now.strftime("%Y-%m-%d")

    status_rows, plan_rows = build_full_universe_backfill_plan(
        store=store,
        target_date=resolved_target_date,
        batch_size=batch_size,
        missing_start_date=missing_start_date,
        stale_lookback_days=stale_lookback_days,
        max_batches=max_batches,
        symbols=symbols,
        full_backfill=full_backfill,
    )

    status_path = write_csv(output_dir / "raw_status.csv", status_rows, RAW_STATUS_COLUMNS)
    plan_path = write_csv(output_dir / "batch_plan.csv", plan_rows, BATCH_PLAN_COLUMNS)

    affected_symbols: list[str] = []
    batch_failures = 0
    collector_error = ""

    if apply and plan_rows:
        collector = TushareCollector()
        for row in plan_rows:
            try:
                collector.update_universe_history(
                    universe=str(row["symbols"]),
                    start_date=str(row["start_date"]).replace("-", ""),
                    end_date=str(row["end_date"]).replace("-", ""),
                    incremental=not full_backfill,
                )
                row["status"] = "success"
                affected_symbols.extend([item for item in str(row["symbols"]).split(",") if item])
            except Exception as exc:
                row["status"] = "failed"
                row["error"] = str(exc)
                collector_error = str(exc)
                batch_failures += 1
        plan_path = write_csv(output_dir / "batch_plan.csv", plan_rows, BATCH_PLAN_COLUMNS)

    qlib_refresh_result: dict[str, Any] = {
        "status": "skipped",
        "reason": "no affected symbols or apply disabled",
    }
    if apply and refresh_qlib and affected_symbols:
        current_qlib_last_ts = adapter.get_last_qlib_date()
        qlib_target_date = resolved_target_date
        if current_qlib_last_ts is not None and pd.Timestamp(qlib_target_date) <= current_qlib_last_ts:
            qlib_refresh_result = refresh_selected_symbols_from_raw(
                base_dir,
                sorted(set(affected_symbols)),
                target_date=qlib_target_date,
                apply=True,
                output_dir=output_dir / "qlib_refresh",
            )
        else:
            try:
                adapter.refresh_qlib_date()
                qlib_refresh_result = {
                    "status": "success",
                    "reason": "global incremental qlib refresh executed",
                }
            except Exception as exc:
                qlib_refresh_result = {
                    "status": "failed",
                    "reason": str(exc),
                }

    needs_backfill_count = sum(1 for row in status_rows if row["needs_backfill"])
    missing_count = sum(1 for row in status_rows if row["backfill_reason"] == "raw_missing")
    stale_count = sum(1 for row in status_rows if row["backfill_reason"] == "raw_stale")
    success_batches = sum(1 for row in plan_rows if row["status"] == "success")

    summary = {
        "run_id": run_id,
        "triggered_by": triggered_by,
        "target_date": resolved_target_date,
        "stock_list_symbol_count": len(status_rows),
        "needs_backfill_count": needs_backfill_count,
        "missing_raw_count": missing_count,
        "stale_raw_count": stale_count,
        "planned_batch_count": len(plan_rows),
        "successful_batch_count": success_batches,
        "failed_batch_count": batch_failures,
        "affected_symbol_count": len(sorted(set(affected_symbols))),
        "apply": apply,
        "collector_error": collector_error,
        "status": "planned" if not apply else ("success" if batch_failures == 0 else "partial" if success_batches > 0 else "failed"),
        "artifacts": {
            "raw_status_path": str(status_path),
            "batch_plan_path": str(plan_path),
        },
        "qlib_refresh": qlib_refresh_result,
    }
    summary_path = write_json(output_dir / "summary.json", summary)
    latest_path = base_dir / "runs" / "latest_full_universe_backfill.json"
    write_json(latest_path, {**summary, "summary_path": str(summary_path)})
    return {"summary": summary, "summary_path": str(summary_path)}
