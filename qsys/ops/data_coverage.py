from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
from qlib.data import D

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import read_instrument_file

RAW_REQUIRED_FIELDS = ["open", "high", "low", "close", "vol", "amount"]
RAW_FIELD_ALIASES = {"volume": "vol"}
QLIB_REQUIRED_FIELDS = ["$open", "$high", "$low", "$close", "$volume", "$amount"]


def _normalize_date(value: Any) -> str | None:
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        text = str(value).strip()
        if len(text) == 8 and text.isdigit():
            ts = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(ts):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def _field_present(columns: list[str], field: str) -> bool:
    if field in columns:
        return True
    alias = RAW_FIELD_ALIASES.get(field)
    return alias in columns if alias else False


def _field_name(columns: list[str], field: str) -> str | None:
    if field in columns:
        return field
    alias = RAW_FIELD_ALIASES.get(field)
    if alias in columns:
        return alias
    return None


def scan_raw_coverage(raw_dir: Path, *, latest_date: str, csi300_symbols: set[str], all_symbols: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    files = sorted(raw_dir.glob("*.feather"))
    for path in files:
        try:
            df = pd.read_feather(path)
        except Exception:
            df = pd.DataFrame()
        symbol = path.stem
        columns = list(df.columns)
        trade_dates = pd.to_datetime(df.get("trade_date"), errors="coerce") if "trade_date" in df.columns else pd.Series(dtype="datetime64[ns]")
        raw_row_count = int(len(df))
        raw_first_date = _normalize_date(trade_dates.min()) if not trade_dates.empty else None
        raw_last_date = _normalize_date(trade_dates.max()) if not trade_dates.empty else None
        required_fields_present = all(_field_present(columns, field) for field in RAW_REQUIRED_FIELDS)
        required_non_null = True
        missing_required_fields: list[str] = []
        for field in RAW_REQUIRED_FIELDS:
            actual = _field_name(columns, field)
            if actual is None:
                missing_required_fields.append(field)
                required_non_null = False
                continue
            if raw_row_count > 0 and pd.to_numeric(df[actual], errors="coerce").notna().sum() == 0:
                required_non_null = False
        rows.append(
            {
                "symbol": symbol,
                "raw_row_count": raw_row_count,
                "raw_first_date": raw_first_date,
                "raw_last_date": raw_last_date,
                "has_raw_on_last_qlib_date": raw_last_date is not None and raw_last_date >= latest_date,
                "required_fields_present": required_fields_present,
                "required_fields_non_null": required_non_null,
                "missing_required_fields": ",".join(missing_required_fields),
                "in_all_instruments": symbol in all_symbols,
                "in_csi300_instruments": symbol in csi300_symbols,
            }
        )
    summary = {
        "raw_file_count": len(files),
        "raw_symbol_count": len(rows),
        "raw_latest_date": max((row["raw_last_date"] for row in rows if row["raw_last_date"]), default=None),
        "symbols_with_raw_on_latest": int(sum(1 for row in rows if row["has_raw_on_last_qlib_date"])),
        "csi300_symbols_with_raw_on_latest": int(sum(1 for row in rows if row["has_raw_on_last_qlib_date"] and row["in_csi300_instruments"])),
        "all_symbols_with_raw_on_latest": int(sum(1 for row in rows if row["has_raw_on_last_qlib_date"] and row["in_all_instruments"])),
    }
    return rows, summary


def scan_qlib_coverage(adapter: QlibAdapter, *, latest_date: str, all_symbols: set[str], csi300_symbols: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    adapter.init_qlib()
    rows: list[dict[str, Any]] = []
    for symbol in sorted(all_symbols):
        try:
            frame = D.features([symbol], QLIB_REQUIRED_FIELDS, start_time="2010-01-01", end_time=latest_date)
        except Exception:
            frame = pd.DataFrame()
        if frame is None or frame.empty:
            qlib_first_date = None
            qlib_last_date = None
            has_latest = False
            core_available = False
            core_non_null = False
        else:
            valid = frame.dropna(how="all")
            if valid.empty:
                qlib_first_date = None
                qlib_last_date = None
            else:
                dt_index = valid.index.get_level_values("datetime") if isinstance(valid.index, pd.MultiIndex) and "datetime" in valid.index.names else valid.index.get_level_values(-1)
                qlib_first_date = _normalize_date(dt_index.min())
                qlib_last_date = _normalize_date(dt_index.max())
            latest_slice = frame.loc[(slice(None), pd.Timestamp(latest_date)), :] if isinstance(frame.index, pd.MultiIndex) and (pd.Timestamp(latest_date) in frame.index.get_level_values(-1)) else pd.DataFrame(columns=frame.columns)
            has_latest = qlib_last_date is not None and qlib_last_date >= latest_date
            core_available = all(field in frame.columns for field in QLIB_REQUIRED_FIELDS)
            core_non_null = core_available and all(pd.to_numeric(frame[field], errors="coerce").notna().sum() > 0 for field in QLIB_REQUIRED_FIELDS)
        rows.append(
            {
                "symbol": symbol,
                "qlib_first_date": qlib_first_date,
                "qlib_last_date": qlib_last_date,
                "has_qlib_on_last_qlib_date": has_latest,
                "core_fields_available": core_available,
                "core_fields_non_null": core_non_null,
                "in_all_instruments": symbol in all_symbols,
                "in_csi300_instruments": symbol in csi300_symbols,
            }
        )
    summary = {
        "qlib_calendar_last_date": latest_date,
        "qlib_symbol_count": len(rows),
        "symbols_with_qlib_on_latest": int(sum(1 for row in rows if row["has_qlib_on_last_qlib_date"])),
        "csi300_symbols_with_qlib_on_latest": int(sum(1 for row in rows if row["has_qlib_on_last_qlib_date"] and row["in_csi300_instruments"])),
        "all_symbols_with_qlib_on_latest": int(sum(1 for row in rows if row["has_qlib_on_last_qlib_date"] and row["in_all_instruments"])),
    }
    return rows, summary


def classify_gap(*, raw_last_date: str | None, qlib_last_date: str | None, instrument_end_date: str | None, last_qlib_date: str) -> tuple[str, str]:
    if raw_last_date is None:
        return "raw_missing", "raw feather missing"
    if raw_last_date < last_qlib_date:
        return "raw_stale", "raw data not updated to latest qlib date"
    if qlib_last_date is None:
        return "qlib_dump_missing", "raw exists but qlib feature rows missing"
    if qlib_last_date < last_qlib_date:
        return "qlib_stale", "raw is fresh but qlib dump did not reach latest qlib date"
    if instrument_end_date is not None and instrument_end_date < last_qlib_date:
        return "instrument_registry_stale", "raw and qlib are fresh but instrument registry end_date is stale"
    if raw_last_date >= last_qlib_date and qlib_last_date >= last_qlib_date:
        return "raw_and_qlib_aligned", "raw and qlib both reach latest qlib date"
    return "unknown", "coverage state does not match known patterns"


def build_gap_rows(
    *,
    raw_rows: list[dict[str, Any]],
    qlib_rows: list[dict[str, Any]],
    instrument_rows: pd.DataFrame,
    csi300_symbols: set[str],
    all_symbols: set[str],
    last_qlib_date: str,
) -> list[dict[str, Any]]:
    raw_map = {row["symbol"]: row for row in raw_rows}
    qlib_map = {row["symbol"]: row for row in qlib_rows}
    instrument_map = {}
    if not instrument_rows.empty:
        instrument_map = {
            str(row.instrument): _normalize_date(row.end_date)
            for row in instrument_rows.itertuples(index=False)
        }
    symbols = sorted(set(raw_map) | set(qlib_map) | set(all_symbols) | set(csi300_symbols))
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        raw_last_date = raw_map.get(symbol, {}).get("raw_last_date")
        qlib_last_date = qlib_map.get(symbol, {}).get("qlib_last_date")
        instrument_end_date = instrument_map.get(symbol)
        gap_type, reason = classify_gap(
            raw_last_date=raw_last_date,
            qlib_last_date=qlib_last_date,
            instrument_end_date=instrument_end_date,
            last_qlib_date=last_qlib_date,
        )
        rows.append(
            {
                "symbol": symbol,
                "in_all_instruments": symbol in all_symbols,
                "in_csi300_instruments": symbol in csi300_symbols,
                "raw_last_date": raw_last_date,
                "qlib_last_date": qlib_last_date,
                "instrument_end_date": instrument_end_date,
                "gap_type": gap_type,
                "reason": reason,
            }
        )
    return rows


def inspect_collector_status(*, project_root: Path, all_instrument_count: int, csi300_instrument_count: int, raw_symbol_count: int, raw_latest_count: int, qlib_latest_count: int) -> dict[str, Any]:
    configured_root = cfg.get_path("root")
    meta_db = (Path(str(configured_root)) if configured_root is not None else project_root) / "meta.db"
    stock_list_count = 0
    if meta_db.exists():
        with sqlite3.connect(meta_db) as conn:
            try:
                row = conn.execute("select count(*) from stock_basic").fetchone()
                stock_list_count = int(row[0]) if row else 0
            except Exception:
                stock_list_count = 0
    raw_update_partial = raw_symbol_count < stock_list_count and raw_symbol_count > 0
    qlib_dump_partial = raw_latest_count > qlib_latest_count
    stock_universe_incomplete = all_instrument_count < 1000
    if raw_update_partial:
        suspected_issue = "raw_update_partial"
        recommendation = "run targeted raw backfill for missing symbols/date range, then incremental qlib dump"
    elif qlib_dump_partial:
        suspected_issue = "qlib_dump_partial"
        recommendation = "rebuild qlib features from raw feather for affected symbols"
    elif stock_universe_incomplete:
        suspected_issue = "stock_list_incomplete"
        recommendation = "refresh stock list / index constituents before next qlib dump"
    else:
        suspected_issue = "unknown"
        recommendation = "inspect collector logs and raw store completeness before repair"
    return {
        "update_script": "scripts/update_data_all.py",
        "collector_mode": "by_symbol_batch_range",
        "raw_store_symbol_count": raw_symbol_count,
        "stock_list_count": stock_list_count,
        "all_instrument_count": all_instrument_count,
        "csi300_instrument_count": csi300_instrument_count,
        "suspected_issue": suspected_issue,
        "recommendation": recommendation,
        "warning": "all universe appears incomplete for A-share full universe" if stock_universe_incomplete else None,
    }


def decide_root_cause(*, raw_summary: dict[str, Any], qlib_summary: dict[str, Any], collector_summary: dict[str, Any]) -> dict[str, Any]:
    if raw_summary["raw_symbol_count"] < collector_summary["stock_list_count"] and raw_summary["symbols_with_raw_on_latest"] <= qlib_summary["symbols_with_qlib_on_latest"]:
        root_cause = "raw_update_partial"
        recommendation = "run targeted raw backfill for missing symbols from 2026-04-03 to latest, then incremental qlib dump"
    elif raw_summary["symbols_with_raw_on_latest"] > qlib_summary["symbols_with_qlib_on_latest"]:
        root_cause = "qlib_dump_partial"
        recommendation = "rebuild qlib features from fresh raw feather for affected symbols"
    elif collector_summary["all_instrument_count"] < 1000:
        root_cause = "stock_universe_incomplete"
        recommendation = "refresh stock list / index constituents before the next raw->qlib sync"
    else:
        root_cause = "unknown"
        recommendation = collector_summary["recommendation"]
    return {
        "root_cause": root_cause,
        "recommendation": recommendation,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def load_instrument_sets(adapter: QlibAdapter) -> tuple[set[str], set[str], pd.DataFrame, pd.DataFrame]:
    all_df = read_instrument_file(adapter.qlib_dir / "instruments" / "all.txt")
    csi300_df = read_instrument_file(adapter.qlib_dir / "instruments" / "csi300.txt")
    all_symbols = set(all_df["instrument"].astype(str).tolist()) if not all_df.empty else set()
    csi300_symbols = set(csi300_df["instrument"].astype(str).tolist()) if not csi300_df.empty else set()
    return all_symbols, csi300_symbols, all_df, csi300_df
