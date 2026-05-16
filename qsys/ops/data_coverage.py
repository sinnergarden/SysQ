from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
from qlib.data import D

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.ops.instrument_coverage import read_instrument_file

RAW_REQUIRED_FIELDS = ["open", "high", "low", "close", "volume", "amount"]
RAW_FIELD_ALIASES = {"volume": "vol"}
QLIB_REQUIRED_FIELDS = ["$open", "$high", "$low", "$close", "$volume", "$amount"]

LATEST_RAW_SYMBOL_FIELDS = [
    "symbol",
    "raw_row_count",
    "raw_first_date",
    "raw_last_date",
    "has_raw_on_last_qlib_date",
    "required_fields_present",
    "required_fields_non_null",
    "missing_required_fields",
    "in_all_instruments",
    "in_csi300_instruments",
]
LATEST_QLIB_SYMBOL_FIELDS = [
    "symbol",
    "qlib_first_date",
    "qlib_last_date",
    "has_qlib_on_last_qlib_date",
    "core_fields_available",
    "core_fields_non_null",
    "in_all_instruments",
    "in_csi300_instruments",
]
LATEST_GAP_FIELDS = [
    "symbol",
    "in_all_instruments",
    "in_csi300_instruments",
    "raw_last_date",
    "qlib_last_date",
    "instrument_end_date",
    "gap_type",
    "reason",
]
HISTORICAL_RAW_GAP_FIELDS = [
    "symbol",
    "date",
    "raw_available",
    "required_fields_available",
    "required_fields_non_null",
    "missing_fields",
    "gap_type",
    "reason",
]
HISTORICAL_QLIB_GAP_FIELDS = [
    "symbol",
    "date",
    "qlib_available",
    "core_fields_available",
    "core_fields_non_null",
    "missing_fields",
    "gap_type",
    "reason",
]
HISTORICAL_BACKFILL_PLAN_FIELDS = [
    "symbol",
    "gap_start",
    "gap_end",
    "raw_gap_days",
    "qlib_gap_days",
    "raw_has_gap",
    "qlib_has_gap",
    "recommended_action",
    "priority",
    "status",
]

RAW_ACTIONABLE_GAP_TYPES = {"raw_missing", "raw_field_missing", "raw_field_nan"}
QLIB_ACTIONABLE_GAP_TYPES = {"qlib_missing", "qlib_field_missing", "qlib_field_nan"}


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


def _coerce_numeric(value: Any) -> float | None:
    series = pd.to_numeric(pd.Series([value]), errors="coerce")
    item = series.iloc[0]
    return None if pd.isna(item) else float(item)


def _missing_raw_fields_from_row(row: pd.Series | None, columns: list[str]) -> tuple[list[str], bool, bool]:
    missing_fields: list[str] = []
    required_fields_available = True
    required_fields_non_null = row is not None
    for field in RAW_REQUIRED_FIELDS:
        actual_name = _field_name(columns, field)
        if actual_name is None:
            required_fields_available = False
            required_fields_non_null = False
            missing_fields.append(field)
            continue
        if row is None:
            continue
        value = _coerce_numeric(row.get(actual_name))
        if value is None:
            required_fields_non_null = False
            missing_fields.append(field)
    return missing_fields, required_fields_available, required_fields_non_null


def _missing_qlib_fields_from_row(row: pd.Series | None, columns: list[str]) -> tuple[list[str], bool, bool]:
    missing_fields: list[str] = []
    core_fields_available = all(field in columns for field in QLIB_REQUIRED_FIELDS)
    core_fields_non_null = row is not None and core_fields_available
    for field in QLIB_REQUIRED_FIELDS:
        if field not in columns:
            missing_fields.append(field)
            core_fields_non_null = False
            continue
        if row is None:
            continue
        value = _coerce_numeric(row.get(field))
        if value is None:
            core_fields_non_null = False
            missing_fields.append(field)
    return missing_fields, core_fields_available, core_fields_non_null


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


def load_universe_instruments(adapter: QlibAdapter, universe: str) -> tuple[set[str], pd.DataFrame]:
    instrument_df = read_instrument_file(adapter.qlib_dir / "instruments" / f"{universe}.txt")
    symbols = set(instrument_df["instrument"].astype(str).tolist()) if not instrument_df.empty else set()
    return symbols, instrument_df


def read_calendar_dates(adapter: QlibAdapter, *, start_date: str, end_date: str) -> list[pd.Timestamp]:
    cal_path = adapter.qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        return []
    df = pd.read_csv(cal_path, header=None, names=["date"])
    if df.empty:
        return []
    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    dates = dates[(dates >= start_ts) & (dates <= end_ts)].sort_values()
    return [pd.Timestamp(value) for value in dates.tolist()]


def _instrument_window_map(instrument_rows: pd.DataFrame) -> dict[str, tuple[pd.Timestamp | None, pd.Timestamp | None]]:
    if instrument_rows.empty:
        return {}
    return {
        str(row.instrument): (
            pd.Timestamp(row.start_date) if pd.notna(row.start_date) else None,
            pd.Timestamp(row.end_date) if pd.notna(row.end_date) else None,
        )
        for row in instrument_rows.itertuples(index=False)
    }


def expected_calendar_dates_by_symbol(
    *,
    symbol: str,
    calendar_dates: list[pd.Timestamp],
    instrument_windows: dict[str, tuple[pd.Timestamp | None, pd.Timestamp | None]],
    start_date: str,
    end_date: str,
) -> list[pd.Timestamp]:
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    inst_start, inst_end = instrument_windows.get(symbol, (None, None))
    effective_start = max(start_ts, inst_start) if inst_start is not None else start_ts
    effective_end = min(end_ts, inst_end) if inst_end is not None else end_ts
    if effective_end < effective_start:
        return []
    return [date for date in calendar_dates if effective_start <= date <= effective_end]


def _prepare_raw_frame(df: pd.DataFrame, *, start_date: str, end_date: str) -> tuple[pd.DataFrame, list[str]]:
    columns = list(df.columns)
    if df.empty or "trade_date" not in df.columns:
        return pd.DataFrame(), columns
    frame = df.copy()
    frame["_trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["_trade_date"])
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    frame = frame[(frame["_trade_date"] >= start_ts) & (frame["_trade_date"] <= end_ts)]
    if frame.empty:
        return pd.DataFrame(), columns
    frame = frame.sort_values("_trade_date").drop_duplicates(subset=["_trade_date"], keep="last")
    frame = frame.set_index("_trade_date")
    return frame, columns


def _prepare_qlib_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=QLIB_REQUIRED_FIELDS)
    if not isinstance(frame.index, pd.MultiIndex):
        return pd.DataFrame(columns=list(frame.columns))
    idx_names = list(frame.index.names)
    if "datetime" in idx_names:
        prepared = frame.reset_index().set_index("datetime")
    else:
        prepared = frame.reset_index().rename(columns={idx_names[-1]: "datetime"}).set_index("datetime")
    prepared.index = pd.to_datetime(prepared.index, errors="coerce")
    prepared = prepared[~prepared.index.isna()]
    if prepared.empty:
        return pd.DataFrame(columns=list(frame.columns))
    prepared = prepared.sort_index().drop_duplicates(keep="last")
    return prepared


def scan_historical_raw_gaps(
    raw_dir: Path,
    *,
    symbols: set[str],
    instrument_rows: pd.DataFrame,
    calendar_dates: list[pd.Timestamp],
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    instrument_windows = _instrument_window_map(instrument_rows)
    for symbol in sorted(symbols):
        path = raw_dir / f"{symbol}.feather"
        try:
            df = pd.read_feather(path) if path.exists() else pd.DataFrame()
        except Exception:
            df = pd.DataFrame()
        prepared, columns = _prepare_raw_frame(df, start_date=start_date, end_date=end_date)
        expected_dates = expected_calendar_dates_by_symbol(
            symbol=symbol,
            calendar_dates=calendar_dates,
            instrument_windows=instrument_windows,
            start_date=start_date,
            end_date=end_date,
        )
        for date in expected_dates:
            row = prepared.loc[date] if not prepared.empty and date in prepared.index else None
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            missing_fields, required_fields_available, required_fields_non_null = _missing_raw_fields_from_row(row, columns)
            raw_available = row is not None
            if not raw_available:
                gap_type = "raw_missing"
                reason = "raw row missing on expected trading date"
            elif not required_fields_available:
                gap_type = "raw_field_missing"
                reason = "raw row present but required fields are missing"
            elif not required_fields_non_null:
                gap_type = "raw_field_nan"
                reason = "raw row present but required fields contain null values"
            else:
                gap_type = "raw_ok"
                reason = "ok"
            rows.append(
                {
                    "symbol": symbol,
                    "date": date.strftime("%Y-%m-%d"),
                    "raw_available": raw_available,
                    "required_fields_available": required_fields_available,
                    "required_fields_non_null": required_fields_non_null,
                    "missing_fields": ",".join(missing_fields),
                    "gap_type": gap_type,
                    "reason": reason,
                }
            )
    summary = {
        "raw_missing_count": int(sum(1 for row in rows if row["gap_type"] == "raw_missing")),
        "raw_field_issue_count": int(sum(1 for row in rows if row["gap_type"] in {"raw_field_missing", "raw_field_nan"})),
        "raw_ok_count": int(sum(1 for row in rows if row["gap_type"] == "raw_ok")),
    }
    return rows, summary


def scan_historical_qlib_gaps(
    adapter: QlibAdapter,
    *,
    symbols: set[str],
    instrument_rows: pd.DataFrame,
    calendar_dates: list[pd.Timestamp],
    start_date: str,
    end_date: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    adapter.init_qlib()
    rows: list[dict[str, Any]] = []
    instrument_windows = _instrument_window_map(instrument_rows)
    for symbol in sorted(symbols):
        expected_dates = expected_calendar_dates_by_symbol(
            symbol=symbol,
            calendar_dates=calendar_dates,
            instrument_windows=instrument_windows,
            start_date=start_date,
            end_date=end_date,
        )
        try:
            frame = D.features([symbol], QLIB_REQUIRED_FIELDS, start_time=start_date, end_time=end_date)
        except Exception:
            frame = pd.DataFrame()
        prepared = _prepare_qlib_frame(frame)
        columns = list(prepared.columns)
        for date in expected_dates:
            row = prepared.loc[date] if not prepared.empty and date in prepared.index else None
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            missing_fields, core_fields_available, core_fields_non_null = _missing_qlib_fields_from_row(row, columns)
            qlib_available = row is not None and any(_coerce_numeric(row.get(field)) is not None for field in QLIB_REQUIRED_FIELDS if field in columns)
            if not qlib_available:
                gap_type = "qlib_missing"
                reason = "qlib row missing on expected trading date"
            elif not core_fields_available:
                gap_type = "qlib_field_missing"
                reason = "qlib row present but required fields are missing"
            elif not core_fields_non_null:
                gap_type = "qlib_field_nan"
                reason = "qlib row present but required fields contain null values"
            else:
                gap_type = "qlib_ok"
                reason = "ok"
            rows.append(
                {
                    "symbol": symbol,
                    "date": date.strftime("%Y-%m-%d"),
                    "qlib_available": qlib_available,
                    "core_fields_available": core_fields_available,
                    "core_fields_non_null": core_fields_non_null,
                    "missing_fields": ",".join(missing_fields),
                    "gap_type": gap_type,
                    "reason": reason,
                }
            )
    summary = {
        "qlib_missing_count": int(sum(1 for row in rows if row["gap_type"] == "qlib_missing")),
        "qlib_field_issue_count": int(sum(1 for row in rows if row["gap_type"] in {"qlib_field_missing", "qlib_field_nan"})),
        "qlib_ok_count": int(sum(1 for row in rows if row["gap_type"] == "qlib_ok")),
        "qlib_audit_mode": "full_symbol_scan",
    }
    return rows, summary


def classify_historical_recommended_action(*, raw_has_gap: bool, qlib_has_gap: bool) -> str:
    if raw_has_gap and qlib_has_gap:
        return "raw_backfill_then_qlib_refresh"
    if raw_has_gap and not qlib_has_gap:
        return "manual_investigation"
    if (not raw_has_gap) and qlib_has_gap:
        return "qlib_refresh"
    return "none"


def fetch_suspended_dates_by_symbol(*, symbols: set[str], start_date: str, end_date: str) -> dict[str, set[str]]:
    if not symbols:
        return {}
    try:
        from qsys.data.collector import TushareCollector

        collector = TushareCollector()
    except Exception:
        return {}

    suspended: dict[str, set[str]] = defaultdict(set)
    start_text = start_date.replace("-", "")
    end_text = end_date.replace("-", "")
    for symbol in sorted(symbols):
        try:
            frame = collector.pro.suspend_d(ts_code=symbol, start_date=start_text, end_date=end_text)
        except Exception:
            continue
        if frame is None or frame.empty or "trade_date" not in frame.columns:
            continue
        for value in frame["trade_date"].tolist():
            date_text = _normalize_date(value)
            if date_text is not None:
                suspended[symbol].add(date_text)
    return {symbol: dates for symbol, dates in suspended.items() if dates}


def apply_suspension_overrides(
    *,
    raw_gap_rows: list[dict[str, Any]],
    qlib_gap_rows: list[dict[str, Any]],
    suspended_dates_by_symbol: dict[str, set[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not suspended_dates_by_symbol:
        return raw_gap_rows, qlib_gap_rows

    for row in raw_gap_rows:
        if row.get("gap_type") != "raw_missing":
            continue
        if row["date"] in suspended_dates_by_symbol.get(row["symbol"], set()):
            row["gap_type"] = "raw_suspended"
            row["reason"] = "expected suspension date; no raw bar required"

    for row in qlib_gap_rows:
        if row.get("gap_type") != "qlib_missing":
            continue
        if row["date"] in suspended_dates_by_symbol.get(row["symbol"], set()):
            row["gap_type"] = "qlib_suspended"
            row["reason"] = "expected suspension date; no qlib bar required"

    return raw_gap_rows, qlib_gap_rows


def historical_action_priority(recommended_action: str) -> str:
    if recommended_action in {"raw_backfill_then_qlib_refresh", "manual_investigation"}:
        return "high"
    if recommended_action == "qlib_refresh":
        return "medium"
    if recommended_action == "raw_backfill":
        return "medium"
    return "low"


def _build_gap_sets(rows: list[dict[str, Any]], actionable_types: set[str]) -> dict[str, set[str]]:
    gap_sets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["gap_type"] in actionable_types:
            gap_sets[row["symbol"]].add(row["date"])
    return gap_sets


def _merge_contiguous_dates(dates: list[str], calendar_index: dict[str, int]) -> list[tuple[str, str, list[str]]]:
    if not dates:
        return []
    ordered = sorted(dates, key=lambda item: calendar_index[item])
    groups: list[tuple[str, str, list[str]]] = []
    current = [ordered[0]]
    for date in ordered[1:]:
        prev = current[-1]
        if calendar_index[date] == calendar_index[prev] + 1:
            current.append(date)
            continue
        groups.append((current[0], current[-1], list(current)))
        current = [date]
    groups.append((current[0], current[-1], list(current)))
    return groups


def build_historical_backfill_plan(
    *,
    symbols: set[str],
    calendar_dates: list[pd.Timestamp],
    raw_gap_rows: list[dict[str, Any]],
    qlib_gap_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    calendar_keys = [date.strftime("%Y-%m-%d") for date in calendar_dates]
    calendar_index = {date: idx for idx, date in enumerate(calendar_keys)}
    raw_gap_sets = _build_gap_sets(raw_gap_rows, actionable_types=RAW_ACTIONABLE_GAP_TYPES)
    qlib_gap_sets = _build_gap_sets(qlib_gap_rows, actionable_types=QLIB_ACTIONABLE_GAP_TYPES)
    rows: list[dict[str, Any]] = []
    for symbol in sorted(symbols):
        raw_dates = raw_gap_sets.get(symbol, set())
        qlib_dates = qlib_gap_sets.get(symbol, set())
        problem_dates = sorted(raw_dates | qlib_dates, key=lambda item: calendar_index[item]) if (raw_dates or qlib_dates) else []
        if not problem_dates:
            rows.append(
                {
                    "symbol": symbol,
                    "gap_start": None,
                    "gap_end": None,
                    "raw_gap_days": 0,
                    "qlib_gap_days": 0,
                    "raw_has_gap": False,
                    "qlib_has_gap": False,
                    "recommended_action": "none",
                    "priority": "low",
                    "status": "aligned",
                }
            )
            continue
        for gap_start, gap_end, segment_dates in _merge_contiguous_dates(problem_dates, calendar_index):
            raw_gap_days = sum(1 for date in segment_dates if date in raw_dates)
            qlib_gap_days = sum(1 for date in segment_dates if date in qlib_dates)
            raw_has_gap = raw_gap_days > 0
            qlib_has_gap = qlib_gap_days > 0
            recommended_action = classify_historical_recommended_action(raw_has_gap=raw_has_gap, qlib_has_gap=qlib_has_gap)
            rows.append(
                {
                    "symbol": symbol,
                    "gap_start": gap_start,
                    "gap_end": gap_end,
                    "raw_gap_days": raw_gap_days,
                    "qlib_gap_days": qlib_gap_days,
                    "raw_has_gap": raw_has_gap,
                    "qlib_has_gap": qlib_has_gap,
                    "recommended_action": recommended_action,
                    "priority": historical_action_priority(recommended_action),
                    "status": "planned" if recommended_action != "none" else "aligned",
                }
            )
    return rows


def build_historical_gap_summary(
    *,
    universe: str,
    start_date: str,
    end_date: str,
    symbols: set[str],
    calendar_dates: list[pd.Timestamp],
    raw_gap_rows: list[dict[str, Any]],
    qlib_gap_rows: list[dict[str, Any]],
    backfill_plan_rows: list[dict[str, Any]],
    qlib_audit_mode: str,
) -> dict[str, Any]:
    raw_missing_count = int(sum(1 for row in raw_gap_rows if row["gap_type"] == "raw_missing"))
    raw_field_issue_count = int(sum(1 for row in raw_gap_rows if row["gap_type"] in {"raw_field_missing", "raw_field_nan"}))
    qlib_missing_count = int(sum(1 for row in qlib_gap_rows if row["gap_type"] == "qlib_missing"))
    qlib_field_issue_count = int(sum(1 for row in qlib_gap_rows if row["gap_type"] in {"qlib_field_missing", "qlib_field_nan"}))
    raw_map = {(row["symbol"], row["date"]): row for row in raw_gap_rows}
    qlib_map = {(row["symbol"], row["date"]): row for row in qlib_gap_rows}
    suspended_count = int(
        sum(
            1
            for key in {(row["symbol"], row["date"]) for row in raw_gap_rows if row["gap_type"] == "raw_suspended"}
            if qlib_map.get(key, {}).get("gap_type") == "qlib_suspended"
        )
    )
    keys = sorted(set(raw_map) | set(qlib_map))
    aligned_ok_count = int(
        sum(
            1
            for key in keys
            if (
                raw_map.get(key, {}).get("gap_type") == "raw_ok" and qlib_map.get(key, {}).get("gap_type") == "qlib_ok"
            )
            or (
                raw_map.get(key, {}).get("gap_type") == "raw_suspended"
                and qlib_map.get(key, {}).get("gap_type") == "qlib_suspended"
            )
        )
    )
    symbol_issue_counts: Counter[str] = Counter()
    date_issue_counts: Counter[str] = Counter()
    for symbol, date in keys:
        raw_type = raw_map.get((symbol, date), {}).get("gap_type")
        qlib_type = qlib_map.get((symbol, date), {}).get("gap_type")
        is_ok = raw_type == "raw_ok" and qlib_type == "qlib_ok"
        is_suspended = raw_type == "raw_suspended" and qlib_type == "qlib_suspended"
        if not is_ok and not is_suspended:
            symbol_issue_counts[symbol] += 1
            date_issue_counts[date] += 1
    worst_symbols = [
        {"symbol": symbol, "issue_count": count}
        for symbol, count in symbol_issue_counts.most_common(10)
    ]
    worst_dates = [
        {"date": date, "issue_count": count}
        for date, count in date_issue_counts.most_common(10)
    ]
    if raw_missing_count == 0 and raw_field_issue_count == 0 and qlib_missing_count == 0 and qlib_field_issue_count == 0:
        root_cause = "clean"
        recommendation = "No historical apply needed; keep audit artifacts for evidence."
    elif (raw_missing_count + raw_field_issue_count) > 0 and (qlib_missing_count + qlib_field_issue_count) == 0:
        root_cause = "raw_gap"
        recommendation = "Investigate raw history gaps first; do not apply until symbol/date windows are confirmed."
    elif (raw_missing_count + raw_field_issue_count) == 0 and (qlib_missing_count + qlib_field_issue_count) > 0:
        root_cause = "qlib_gap"
        recommendation = "Historical apply is not yet needed; a targeted qlib refresh would be the next candidate after review."
    elif (raw_missing_count + raw_field_issue_count) > 0 and (qlib_missing_count + qlib_field_issue_count) > 0:
        root_cause = "mixed"
        recommendation = "Gaps span both raw and qlib; review the backfill plan before any historical apply."
    else:
        root_cause = "unknown"
        recommendation = "Inspect per-date audit rows before deciding whether historical apply is warranted."
    return {
        "universe": universe,
        "start_date": start_date,
        "end_date": end_date,
        "symbol_count": len(symbols),
        "trading_date_count": len(calendar_dates),
        "expected_symbol_date_count": len(keys),
        "raw_missing_count": raw_missing_count,
        "raw_field_issue_count": raw_field_issue_count,
        "qlib_missing_count": qlib_missing_count,
        "qlib_field_issue_count": qlib_field_issue_count,
        "aligned_ok_count": aligned_ok_count,
        "suspended_count": suspended_count,
        "worst_symbols": worst_symbols,
        "worst_dates": worst_dates,
        "root_cause": root_cause,
        "recommendation": recommendation,
        "qlib_audit_mode": qlib_audit_mode,
        "recommended_action_counts": dict(sorted(Counter(row["recommended_action"] for row in backfill_plan_rows).items())),
    }
