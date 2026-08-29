#!/usr/bin/env python3
"""
CSI800 / PIT CSI1800 daily incremental data sync — 每日数据闭环.

Flow:
  1. resolve target trade date
  2. resolve current constituents (CSI1800 uses an immutable PIT snapshot)
  3. pre-check: skip fetch if all stocks already have target date
  4. batch fetch raw data for missing stocks (single-pass)
  5. update index daily data (7 benchmark indices, OHLCV+volume)
  6. convert to qlib bin (incremental → fallback fix)
  7. refresh qlib instrument files
  8. comprehensive readiness check
  9. write structured audit record → data/audit/

Usage:
  # dry-run
  python scripts/ops/sync_csi800_daily.py

  # apply (real run)
  python scripts/ops/sync_csi800_daily.py --apply

  # specific date
  python scripts/ops/sync_csi800_daily.py --apply --target-date 2026-05-15
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, time as wall_time
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.data.collector import HISTORY_FIELD_ENDPOINTS, TushareCollector
from qsys.data.storage import StockDataStore
from qsys.data.source_audit import (
    LEGACY_UNTRUSTED,
    SourceAuditStore,
    TRUSTED,
    checkpoint_requested_scope,
    data_writer_lock,
    new_run_id,
    normalized_response_metadata,
    stable_scope_hash,
    validate_run_id,
)
from qsys.data.adapter import QlibAdapter
from qsys.ops.industry_sync import (
    fetch_audited_daily_industry,
    fetch_audited_history_industry,
)
from qsys.utils.logger import log


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _history_local_workers(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 8:
        raise argparse.ArgumentTypeError("must be between 1 and 8")
    return parsed


_DAILY_DATA_READY_CUTOFF = wall_time(18, 30)
TRUSTED_DAILY_FIELDS = ("open", "high", "low", "close", "volume", "factor")
TRUSTED_DAILY_FIELD_ENDPOINTS = {
    "open": "daily",
    "high": "daily",
    "low": "daily",
    "close": "daily",
    "volume": "daily",
    "factor": "adj_factor",
}
_CRASH_EVIDENCE: dict | None = None
_MAX_MUTATION_MISMATCH_SAMPLES = 100
_HISTORY_SUSPEND_FIELDS = "ts_code,trade_date,suspend_type"


def _load_csi1800_research_union(data_root: Path) -> tuple[list[str], dict]:
    """Load the immutable CSI1800 PIT research union for historical evidence."""

    artifact = data_root / "research" / "universes" / "csi1800_pit_v2"
    registry_path = artifact / "csi1800_pit_union.txt"
    manifest_path = artifact / "manifest.json"
    if not registry_path.is_file() or not manifest_path.is_file():
        raise RuntimeError(f"CSI1800 PIT research union is incomplete: {artifact}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(registry_path.read_bytes()).hexdigest()
    if digest != manifest.get("registry_sha256"):
        raise RuntimeError("CSI1800 PIT research union registry hash mismatch")
    rows = [line.split() for line in registry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(len(row) < 3 for row in rows):
        raise RuntimeError("CSI1800 PIT research union registry is malformed")
    codes = sorted({row[0].strip().upper() for row in rows})
    if not codes or len(codes) != int(manifest.get("n_registry_instruments") or 0):
        raise RuntimeError("CSI1800 PIT research union is empty or malformed")
    return codes, {
        "snapshot_semantics": "immutable_csi1800_pit_research_union",
        "artifact_dir": str(artifact),
        "registry_sha256": digest,
        "constituent_count": len(codes),
    }


def _resolve_target_date(
    end_date: str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Resolve the latest *completed and expected-ready* trading session.

    The timer runs at 19:00 Asia/Shanghai.  Before 18:30 on an open day, the
    current session is deliberately excluded so an early manual invocation
    cannot create a partial/future-dated daily run.  An explicit date remains
    an operator override.  Missing or malformed calendars fail closed instead
    of silently falling back to today's wall-clock date.
    """
    if end_date:
        return end_date.replace("-", "")

    current = now or datetime.now()
    today_str = current.strftime("%Y%m%d")
    inclusive_today = current.time() >= _DAILY_DATA_READY_CUTOFF

    # Use local trade_cal (data source ground truth, not qlib)
    try:
        cal = StockDataStore().get_calendar()
        if cal is not None and not cal.empty and "is_open" in cal.columns and "cal_date" in cal.columns:
            open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
            candidate = [
                d for d in open_days
                if d < today_str or (inclusive_today and d == today_str)
            ]
            if candidate:
                return candidate[-1]
    except Exception as e:
        log.warning(f"Failed to resolve target date via calendar: {e}")

    raise RuntimeError(
        "cannot resolve a completed daily target from trade_cal; "
        "refresh the calendar or pass --target-date explicitly"
    )


def _normalize_date_arg(value: str, *, name: str) -> str:
    """Normalize an operator date and reject ambiguous values."""

    normalized = str(value).strip().replace("-", "")
    if len(normalized) != 8 or not normalized.isdigit():
        raise ValueError(f"{name} must be YYYYMMDD")
    try:
        datetime.strptime(normalized, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid YYYYMMDD date") from exc
    return normalized


def _resolve_sync_window(
    target_dt: str,
    repair_start_date: str | None = None,
) -> dict[str, str]:
    """Resolve an explicit daily or operator-requested historical window.

    Normal daily runs are deliberately independent of Qlib's watermark.  A
    historical range is available only when an operator explicitly supplies
    ``--repair-start-date``.
    """

    target = _normalize_date_arg(target_dt, name="target date")
    if repair_start_date is None:
        start = target
        mode = "daily_single_day"
    else:
        start = _normalize_date_arg(repair_start_date, name="repair-start-date")
        if start > target:
            raise ValueError(
                "repair-start-date must be on or before target date "
                f"({start} > {target})"
            )
        mode = "explicit_historical_repair" if start < target else "daily_single_day"
    return {"mode": mode, "start_date": start, "target_date": target}


def _previous_open_session(store: StockDataStore, target_dt: str) -> str | None:
    if not hasattr(store, "get_calendar"):
        return None
    calendar = store.get_calendar()
    if calendar is None or calendar.empty or not {"cal_date", "is_open"}.issubset(calendar.columns):
        return None
    dates = sorted(
        str(value).replace("-", "")[:8]
        for value in calendar.loc[calendar["is_open"] == 1, "cal_date"].tolist()
        if str(value).replace("-", "")[:8] < target_dt
    )
    return dates[-1] if dates else None


def _check_stock_data_status(store: StockDataStore, codes: list[str], target_dt: str) -> dict:
    """
    Per-stock latest date check.

    Prefers meta.db ``data_latest`` table (one query) over scanning each
    feather file.  Falls back to feather scan when meta data is missing or
    incomplete for individual symbols.

    Returns: { 'have': [codes with target date], 'missing': [codes without],
               'source': 'meta_db' | 'feather_scan' }
    """
    # ── Fast path: meta.db data_latest table ──────────────────
    try:
        import sqlite3
        from qsys.config import cfg
        db_path = Path(str(cfg.get_path("root"))) / "meta.db"
        meta_conn = sqlite3.connect(str(db_path))
        meta_rows = meta_conn.execute(
            "SELECT ts_code, latest_date FROM data_latest"
        ).fetchall()
        meta_conn.close()
        meta_map = {str(row[0]): str(row[1] or "") for row in meta_rows}
        have = [c for c in codes if meta_map.get(c, "") >= target_dt]
        missing = [c for c in codes if c not in have]
        if not missing:
            return {
                "have": have, "missing": missing,
                "total": len(codes), "already_up_to_date": len(have),
                "need_fetch": 0, "source": "meta_db",
            }
    except Exception:
        have = []
        missing = list(codes)

    # ── Slow path: feather scan for remaining symbols ────────
    remaining = [c for c in missing]
    for code in remaining:
        df = store.load_daily(code)
        if df is not None and not df.empty:
            latest = str(df["trade_date"].max())
            if latest >= target_dt:
                have.append(code)
    missing = [c for c in codes if c not in have]
    return {
        "have": have, "missing": missing,
        "total": len(codes), "already_up_to_date": len(have),
        "need_fetch": len(missing), "source": "feather_scan",
    }


def _target_date_values(values: pd.Series) -> pd.Series:
    """Normalize canonical date values without integer-to-nanosecond coercion."""

    return (
        values.astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace("-", "", regex=False)
        .str.slice(0, 8)
    )


def _truthy_flags(values: pd.Series) -> pd.Series:
    """Normalize numeric and textual truthy flags without remote lookups."""

    numeric = pd.to_numeric(values, errors="coerce").fillna(0)
    text = values.astype(str).str.strip().str.lower()
    return numeric.ne(0) | text.isin({"true", "t", "yes", "y", "on"})


def _canonical_symbol_availability_on_date(
    store: StockDataStore,
    symbols: list[str],
    target_dt: str,
) -> tuple[set[str], list[dict[str, object]]]:
    """Return available symbols and auditable exclusions for ``target_dt``.

    The canonical store is the source of truth for raw availability.  Only a
    non-null numeric close is eligible for the same-date comparison. Explicit
    paused/suspended rows are excluded even when a carried-forward close is
    present; no per-symbol suspension API lookup is needed.
    """

    target_dt = str(target_dt).replace("-", "")[:8]
    available: set[str] = set()
    exclusions: list[dict[str, object]] = []
    for symbol in sorted(set(symbols)):
        try:
            frame = store.load_daily(symbol)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to inspect canonical data for {symbol} on {target_dt}: {exc}"
            ) from exc
        reasons: list[str] = []
        if frame is None or frame.empty or "trade_date" not in frame.columns:
            exclusions.append({"ts_code": symbol, "reasons": ["missing_target_row"]})
            continue
        target_rows = frame.loc[_target_date_values(frame["trade_date"]) == target_dt]
        if target_rows.empty:
            exclusions.append({"ts_code": symbol, "reasons": ["missing_target_row"]})
            continue
        if "close" not in target_rows.columns:
            exclusions.append({"ts_code": symbol, "reasons": ["missing_close_column"]})
            continue
        close = pd.to_numeric(target_rows["close"], errors="coerce")
        if close.isna().any():
            reasons.append("null_close")
        eligible = close.notna()
        for flag_column in ("paused", "is_suspended"):
            if flag_column in target_rows.columns:
                flagged = _truthy_flags(target_rows[flag_column])
                if flagged.any():
                    reasons.append(flag_column)
                eligible &= ~flagged
        if eligible.any():
            available.add(symbol)
        else:
            exclusions.append({"ts_code": symbol, "reasons": sorted(set(reasons))})
    return available, exclusions


def _canonical_symbols_with_data_on_date(
    store: StockDataStore,
    symbols: list[str],
    target_dt: str,
) -> set[str]:
    """Compatibility wrapper returning only the available symbol set."""

    available, _ = _canonical_symbol_availability_on_date(store, symbols, target_dt)
    return available


def _non_empty_feature_symbols(frame: pd.DataFrame, *, field: str = "$close") -> set[str]:
    """Extract instruments whose requested feature is non-null.

    Qlib normally returns a MultiIndex named ``(datetime, instrument)``.  The
    explicit column and plain-index branches keep the helper usable with
    lightweight adapters and test doubles without weakening the production
    MultiIndex path.
    """

    if frame is None or frame.empty or field not in frame.columns:
        return set()
    valid = frame[field].notna()
    if isinstance(frame.index, pd.MultiIndex):
        names = list(frame.index.names)
        if "instrument" in names:
            instrument_values = frame.index.get_level_values("instrument")
        elif "ts_code" in names:
            instrument_values = frame.index.get_level_values("ts_code")
        else:
            instrument_values = frame.index.get_level_values(-1)
    elif "instrument" in frame.columns:
        instrument_values = frame["instrument"]
    elif "ts_code" in frame.columns:
        instrument_values = frame["ts_code"]
    else:
        instrument_values = frame.index

    values = pd.Series(instrument_values, index=frame.index)
    return {
        str(value)
        for value in values.loc[valid].tolist()
        if pd.notna(value) and str(value).strip()
    }


def _qlib_symbols_with_data_on_date(
    adapter: QlibAdapter,
    symbols: list[str],
    target_dt: str,
) -> set[str]:
    """Return exact symbols with a non-empty Qlib ``$close`` on target date."""

    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:8]}"
    frame = adapter.get_features(
        sorted(set(symbols)),
        ["$close"],
        start_time=target_date,
        end_time=target_date,
    )
    return _non_empty_feature_symbols(frame, field="$close")


def _repair_same_date_qlib_gap(
    adapter: QlibAdapter,
    store: StockDataStore,
    symbols: list[str],
    *,
    universe: str,
    target_dt: str,
    apply: bool,
    qlib_max_workers: int | None = None,
) -> dict:
    """Repair and verify canonical-vs-Qlib same-date symbol gaps.

    This stage is intentionally fail-closed: a failed conversion or any
    residual gap after ``convert_fix_symbols`` is returned as ``failed`` and
    the caller must abort before readiness can be reported.
    """

    canonical, canonical_exclusions = _canonical_symbol_availability_on_date(
        store, symbols, target_dt
    )
    qlib_before = _qlib_symbols_with_data_on_date(adapter, symbols, target_dt)
    missing_before = sorted(canonical - qlib_before)
    summary = {
        "status": "success" if not missing_before else ("dry_run" if not apply else "pending"),
        "target_date": target_dt,
        "canonical_symbols_with_data_count": len(canonical),
        "qlib_symbols_with_data_before_count": len(qlib_before),
        "missing_symbols": missing_before,
        "missing_count": len(missing_before),
        "canonical_exclusions": canonical_exclusions,
        "canonical_exclusion_count": len(canonical_exclusions),
        "repaired_symbols": [],
        "qlib_symbols_with_data_after_count": len(qlib_before),
        "residual_symbols": missing_before,
        "residual_count": len(missing_before),
        "verified_no_gap": not missing_before,
    }
    if not missing_before or not apply:
        return summary

    try:
        refresh_kwargs = {"refresh_universes": []}
        if qlib_max_workers is not None:
            refresh_kwargs["max_workers"] = qlib_max_workers
        result = adapter.convert_fix_symbols(missing_before, **refresh_kwargs)
    except Exception as exc:
        summary.update({"status": "failed", "error": str(exc)})
        return summary
    if str(result.get("status", "success")) != "success":
        summary.update({
            "status": "failed",
            "error": f"convert_fix_symbols returned status={result.get('status')}",
        })
        return summary

    qlib_after = _qlib_symbols_with_data_on_date(adapter, symbols, target_dt)
    missing_after = sorted(canonical - qlib_after)
    summary.update({
        "status": "success" if not missing_after else "failed",
        "repaired_symbols": missing_before,
        "qlib_symbols_with_data_after_count": len(qlib_after),
        "residual_symbols": missing_after,
        "residual_count": len(missing_after),
        "verified_no_gap": not missing_after,
    })
    if missing_after:
        summary["error"] = "same-date Qlib gap remains after convert_fix_symbols"
    return summary


_CANONICAL_TO_QLIB_READBACK = {
    "open": "$open",
    "high": "$high",
    "low": "$low",
    "close": "$close",
    "factor": "$factor",
    "adj_factor": "$factor",
    "volume": "$volume",
    "vol": "$volume",
    "amount": "$amount",
    "turnover_rate": "$turnover_rate",
    "pe": "$pe",
    "pb": "$pb",
    "total_mv": "$total_mv",
    "circ_mv": "$circ_mv",
    "margin_balance": "$margin_balance",
    "margin_buy_amount": "$margin_buy_amount",
    "margin_repay_amount": "$margin_repay_amount",
    "net_income": "$net_income",
    "revenue": "$revenue",
    "total_assets": "$total_assets",
    "equity": "$equity",
    "op_cashflow": "$op_cashflow",
    "roe": "$roe",
    "grossprofit_margin": "$grossprofit_margin",
    "debt_to_assets": "$debt_to_assets",
    "current_ratio": "$current_ratio",
    "industry": "$industry",
}


def _expected_qlib_value(raw_field: str, value):
    expected = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(expected):
        return expected
    if raw_field in {"volume", "vol"}:
        return float(expected) * 100.0
    if raw_field in {"total_mv", "circ_mv"}:
        return float(expected) * 10000.0
    return expected


def _historical_mutation_readback(
    adapter,
    store,
    changed: list[dict],
    *,
    industry_map: dict[str, int] | None = None,
) -> dict:
    """Verify each exact historical mutation at its own date."""

    mismatches: list[dict[str, object]] = []
    verified = 0
    verified_fields: set[str] = set()
    if industry_map is None and any(
        "industry" in item.get("fields", []) for item in changed
    ):
        industry_map = adapter._load_industry_map(store.get_stock_list())
    for symbol in sorted({str(item["symbol"]) for item in changed}):
        items = [item for item in changed if str(item["symbol"]) == symbol]
        raw_fields = sorted({
            field for item in items for field in item.get("fields", [])
            if field in _CANONICAL_TO_QLIB_READBACK
        })
        if not raw_fields:
            continue
        if any(str(item.get("date_start")) != str(item.get("date_end")) for item in items):
            mismatches.append({"symbol": symbol, "field": "*", "reason": "mutation_scope_not_exact"})
            continue
        start = min(str(item["date_start"]) for item in items)
        end = max(str(item["date_end"]) for item in items)
        qlib_fields = sorted({_CANONICAL_TO_QLIB_READBACK[field] for field in raw_fields})
        verified_fields.update(qlib_fields)
        qlib_frame = adapter.get_features(
            [symbol], qlib_fields,
            start_time=f"{start[:4]}-{start[4:6]}-{start[6:8]}",
            end_time=f"{end[:4]}-{end[4:6]}-{end[6:8]}",
        )
        canonical = store.load_daily_window(
            symbol, start_date=start, end_date=end, columns=raw_fields,
        )
        if qlib_frame is None or qlib_frame.empty or canonical is None or canonical.empty:
            mismatches.append({"symbol": symbol, "field": "*", "reason": "readback_row_missing"})
            continue
        qlib_rows = qlib_frame.reset_index() if isinstance(qlib_frame.index, pd.MultiIndex) else qlib_frame.copy()
        date_column = next((name for name in ("datetime", "date", "trade_date") if name in qlib_rows), None)
        symbol_column = "instrument" if "instrument" in qlib_rows else "ts_code"
        if date_column is None or symbol_column not in qlib_rows:
            mismatches.append({"symbol": symbol, "field": "*", "reason": "readback_identity_missing"})
            continue
        qlib_dates = _target_date_values(qlib_rows[date_column])
        canonical_dates = _target_date_values(canonical["trade_date"])
        canonical_by_date = canonical.copy()
        canonical_by_date["_qsys_readback_date"] = canonical_dates.to_numpy()
        canonical_by_date = (
            canonical_by_date
            .drop_duplicates("_qsys_readback_date", keep="last")
            .set_index("_qsys_readback_date")
        )
        qlib_symbol_mask = qlib_rows[symbol_column].astype(str) == symbol
        qlib_by_date = qlib_rows.loc[qlib_symbol_mask].copy()
        qlib_by_date["_qsys_readback_date"] = qlib_dates.loc[qlib_symbol_mask].to_numpy()
        qlib_by_date = (
            qlib_by_date
            .drop_duplicates("_qsys_readback_date", keep="last")
            .set_index("_qsys_readback_date")
        )
        for item in items:
            mutation_date = str(item["date_start"])
            if mutation_date not in canonical_by_date.index or mutation_date not in qlib_by_date.index:
                mismatches.append({"symbol": symbol, "date": mutation_date, "field": "*", "reason": "readback_row_missing"})
                continue
            raw_row = canonical_by_date.loc[mutation_date]
            qlib_row = qlib_by_date.loc[mutation_date]
            by_qlib: dict[str, str] = {}
            for raw_field in item.get("fields", []):
                qlib_field = _CANONICAL_TO_QLIB_READBACK.get(raw_field)
                if qlib_field is None or raw_field not in raw_row:
                    continue
                previous = by_qlib.get(qlib_field)
                if previous is None or (pd.isna(raw_row[previous]) and pd.notna(raw_row[raw_field])):
                    by_qlib[qlib_field] = raw_field
            for qlib_field, raw_field in by_qlib.items():
                if qlib_field not in qlib_row:
                    mismatches.append({"symbol": symbol, "date": mutation_date, "field": qlib_field, "reason": "readback_field_missing"})
                    continue
                if raw_field == "industry":
                    expected = (industry_map or {}).get(str(raw_row[raw_field]).strip())
                    if expected is None:
                        mismatches.append({"symbol": symbol, "date": mutation_date, "field": qlib_field, "reason": "industry_mapping_missing"})
                        continue
                else:
                    expected = _expected_qlib_value(raw_field, raw_row[raw_field])
                actual = pd.to_numeric(pd.Series([qlib_row[qlib_field]]), errors="coerce").iloc[0]
                same = (pd.isna(expected) and pd.isna(actual)) or (
                    pd.notna(expected) and pd.notna(actual)
                    and bool(np.isclose(float(expected), float(actual), rtol=1e-10, atol=1e-12))
                )
                if same:
                    verified += 1
                else:
                    mismatches.append({"symbol": symbol, "date": mutation_date, "field": qlib_field, "reason": "value_mismatch"})
    return {
        "status": "failed" if mismatches else "success",
        "verified_fields": sorted(verified_fields),
        "verified_value_count": verified,
        "mismatches": mismatches,
        **({"error": "historical Qlib value readback mismatch"} if mismatches else {}),
    }


def _refresh_and_verify_changed_symbols(
    adapter: QlibAdapter,
    store: StockDataStore,
    mutations: list[dict],
    *,
    target_dt: str,
    apply: bool,
    history_mode: bool = False,
    qlib_max_workers: int | None = None,
) -> dict:
    """Drive Qlib dump_fix from exact mutations, then read back changed values."""

    changed = [item for item in mutations if item.get("mutation_type") in {"insert", "update"}]
    symbols = sorted({str(item["symbol"]) for item in changed})
    revision_symbols = sorted(
        {str(item["symbol"]) for item in changed if item.get("mutation_type") == "update"}
    )
    if not symbols:
        return {
            "status": "success",
            "mode": "noop",
            "changed_symbols": [],
            "verified_value_count": 0,
        }
    if not apply:
        return {"status": "dry_run", "changed_symbols": symbols, "verified_value_count": 0}

    refresh_kwargs = {"refresh_universes": []}
    if qlib_max_workers is not None:
        refresh_kwargs["max_workers"] = qlib_max_workers
    refresh = (
        adapter.convert_fix_symbols(revision_symbols, **refresh_kwargs)
        if revision_symbols
        else {"status": "skipped", "reason": "inserts_handled_by_incremental", "symbols_count": 0}
    )
    if revision_symbols and refresh.get("status") != "success":
        return {
            "status": "failed",
            "error": f"convert_fix_symbols returned status={refresh.get('status')}",
            "changed_symbols": symbols,
            "revision_symbols": revision_symbols,
            "refresh": refresh,
        }

    fields = sorted(
        {
            _CANONICAL_TO_QLIB_READBACK[field]
            for item in changed
            for field in item.get("fields", [])
            if field in _CANONICAL_TO_QLIB_READBACK
        }
    )
    if not fields:
        return {
            "status": "success",
            "mode": "mutation_fix",
            "changed_symbols": symbols,
            "revision_symbols": revision_symbols,
            "verified_value_count": 0,
            "verified_fields": [],
            "refresh": refresh,
        }

    if history_mode:
        verification = _historical_mutation_readback(adapter, store, changed)
        return {
            **verification,
            "mode": "historical_mutation_fix",
            "changed_symbols": symbols,
            "revision_symbols": revision_symbols,
            "refresh": refresh,
        }

    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:8]}"
    qlib_frame = adapter.get_features(
        symbols,
        fields,
        start_time=target_date,
        end_time=target_date,
    )
    if qlib_frame is None or qlib_frame.empty:
        return {
            "status": "failed",
            "error": "Qlib value readback returned no rows after mutation refresh",
            "changed_symbols": symbols,
            "refresh": refresh,
        }
    if isinstance(qlib_frame.index, pd.MultiIndex):
        qlib_rows = qlib_frame.reset_index()
    else:
        qlib_rows = qlib_frame.copy()
    symbol_column = "instrument" if "instrument" in qlib_rows.columns else "ts_code"
    mismatches: list[dict[str, object]] = []
    verified = 0
    for symbol in symbols:
        changed_fields = {
            field
            for item in changed
            if str(item["symbol"]) == symbol
            for field in item.get("fields", [])
        }
        read_columns = sorted({field for field in changed_fields if field in _CANONICAL_TO_QLIB_READBACK})
        if hasattr(store, "load_daily_window"):
            canonical = store.load_daily_window(
                symbol,
                start_date=target_dt,
                end_date=target_dt,
                columns=read_columns,
            )
        else:
            canonical = store.load_daily(symbol)
        if canonical is None or canonical.empty or "trade_date" not in canonical.columns:
            mismatches.append({"symbol": symbol, "field": "*", "reason": "canonical_row_missing"})
            continue
        canonical_rows = canonical.loc[_target_date_values(canonical["trade_date"]) == target_dt]
        qlib_symbol_rows = qlib_rows.loc[qlib_rows[symbol_column].astype(str) == symbol]
        if canonical_rows.empty or qlib_symbol_rows.empty:
            mismatches.append({"symbol": symbol, "field": "*", "reason": "readback_row_missing"})
            continue
        raw_row = canonical_rows.iloc[-1]
        qlib_row = qlib_symbol_rows.iloc[-1]
        by_qlib: dict[str, str] = {}
        for raw_field in (
            "open", "high", "low", "close", "factor", "adj_factor",
            "volume", "vol", "amount", "industry",
        ):
            qlib_field = _CANONICAL_TO_QLIB_READBACK[raw_field]
            if raw_field not in changed_fields or raw_field not in raw_row:
                continue
            # Multiple supplier aliases may map to one Qlib field. Prefer a
            # populated value, while retaining null only as a fallback when
            # every alias is null.
            previous = by_qlib.get(qlib_field)
            if previous is None or (pd.isna(raw_row[previous]) and pd.notna(raw_row[raw_field])):
                by_qlib[qlib_field] = raw_field
        for qlib_field, raw_field in by_qlib.items():
            if qlib_field not in qlib_row:
                if raw_field == "industry":
                    mismatches.append({"symbol": symbol, "field": qlib_field, "reason": "readback_field_missing"})
                continue
            if raw_field == "industry":
                industry_map = adapter._load_industry_map(store.get_stock_list())
                expected = industry_map.get(str(raw_row[raw_field]).strip())
                if expected is None:
                    mismatches.append({"symbol": symbol, "field": qlib_field, "reason": "industry_mapping_missing"})
                    continue
            else:
                expected = _expected_qlib_value(raw_field, raw_row[raw_field])
            actual = pd.to_numeric(pd.Series([qlib_row[qlib_field]]), errors="coerce").iloc[0]
            same = (pd.isna(expected) and pd.isna(actual)) or (
                pd.notna(expected) and pd.notna(actual) and bool(np.isclose(float(expected), float(actual), rtol=1e-10, atol=1e-12))
            )
            if not same:
                mismatches.append({"symbol": symbol, "field": qlib_field, "reason": "value_mismatch"})
            else:
                verified += 1
    return {
        "status": "failed" if mismatches else "success",
        "mode": "mutation_fix",
        "changed_symbols": symbols,
        "revision_symbols": revision_symbols,
        "verified_fields": fields,
        "verified_value_count": verified,
        "mismatches": mismatches,
        "refresh": refresh,
        **({"error": "Qlib value readback mismatch"} if mismatches else {}),
    }


def _refresh_and_verify_history_mutation_store(
    adapter: QlibAdapter,
    store: StockDataStore,
    audit_store,
    run_ids: list[str],
    *,
    apply: bool,
    require_pit_industry: bool = False,
    pit_industry_until_date: str | None = None,
    qlib_max_workers: int | None = None,
) -> dict:
    """Read and verify one symbol's historical mutations at a time."""

    run_ids = [validate_run_id(item) for item in run_ids]
    symbols = sorted({
        symbol
        for item in run_ids
        for symbol in audit_store.changed_mutation_symbols(item)
    })
    revision_symbols = sorted({
        symbol
        for item in run_ids
        for symbol in audit_store.changed_mutation_symbols(item, mutation_type="update")
    })
    if not symbols:
        return {
            "status": "success",
            "mode": "noop",
            "changed_symbols": [],
            "verified_value_count": 0,
        }
    if not apply:
        return {
            "status": "dry_run",
            "changed_symbols": symbols,
            "verified_value_count": 0,
        }

    refresh_kwargs = {"refresh_universes": []}
    if qlib_max_workers is not None:
        refresh_kwargs["max_workers"] = qlib_max_workers
    if require_pit_industry:
        refresh_kwargs["require_pit_industry"] = True
    if pit_industry_until_date is not None:
        refresh_kwargs["pit_industry_until_date"] = pit_industry_until_date
    refresh = (
        adapter.convert_fix_symbols(revision_symbols, **refresh_kwargs)
        if revision_symbols
        else {
            "status": "skipped",
            "reason": "inserts_handled_by_incremental",
            "symbols_count": 0,
        }
    )
    if revision_symbols and refresh.get("status") != "success":
        return {
            "status": "failed",
            "error": f"convert_fix_symbols returned status={refresh.get('status')}",
            "changed_symbols": symbols,
            "revision_symbols": revision_symbols,
            "refresh": refresh,
        }

    verified_fields: set[str] = set()
    verified_value_count = 0
    mismatch_count = 0
    mismatch_samples: list[dict[str, object]] = []
    industry_map: dict[str, int] | None = None
    for symbol in tqdm(
        symbols,
        desc="Qlib historical readback",
        unit="symbol",
        dynamic_ncols=True,
    ):
        symbol_mutations: list[dict] = []
        for item in run_ids:
            symbol_mutations.extend(
                audit_store.changed_mutations(item, symbol=symbol)
            )
        if industry_map is None and any(
            "industry" in item.get("fields", []) for item in symbol_mutations
        ):
            industry_map = adapter._load_industry_map(store.get_stock_list())
        verification_kwargs = (
            {"industry_map": industry_map} if industry_map is not None else {}
        )
        verification = _historical_mutation_readback(
            adapter,
            store,
            symbol_mutations,
            **verification_kwargs,
        )
        verified_fields.update(verification.get("verified_fields", []))
        verified_value_count += int(verification.get("verified_value_count", 0))
        current_mismatches = list(verification.get("mismatches", []))
        mismatch_count += len(current_mismatches)
        remaining = _MAX_MUTATION_MISMATCH_SAMPLES - len(mismatch_samples)
        if remaining > 0:
            mismatch_samples.extend(current_mismatches[:remaining])

    return {
        "status": "failed" if mismatch_count else "success",
        "mode": "historical_mutation_fix",
        "mutation_run_ids": run_ids,
        "changed_symbols": symbols,
        "revision_symbols": revision_symbols,
        "verified_fields": sorted(verified_fields),
        "verified_value_count": verified_value_count,
        "mismatch_count": mismatch_count,
        "mismatches": mismatch_samples,
        "refresh": refresh,
        **({"error": "historical Qlib value readback mismatch"} if mismatch_count else {}),
    }


# Index codes refreshed daily alongside stock data
_INDEX_CODES = [
    "000001.SH", "000300.SH", "000905.SH", "000852.SH",
    "000906.SH", "000688.SH", "399006.SZ",
]


def _update_index_daily(collector: TushareCollector, target_dt: str) -> dict:
    """Incremental update: fetch index daily data from last CSV date to target.

    Writes/updates CSV in ``data/raw/index/<ts_code>.csv``.
    Returns a summary dict per index.
    """
    index_dir = Path(cfg.get_path("root")) / "raw" / "index"
    index_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for code in _INDEX_CODES:
        csv_path = index_dir / f"{code}.csv"
        start_date = None

        if csv_path.exists():
            import pandas as pd
            existing = pd.read_csv(csv_path)
            if not existing.empty:
                # If existing file lacks OHLCV columns, re-fetch from 2010
                has_ohlcv = {"open", "high", "low", "vol"}.intersection(existing.columns)
                if not has_ohlcv:
                    log.info("%s: existing CSV is close-only, re-fetching full OHLCV from 2010", code)
                    start_date = "20100101"
                else:
                    last_date = str(existing["trade_date"].iloc[-1]).replace("-", "")
                    if last_date >= target_dt:
                        results[code] = {"status": "skipped", "reason": "already_up_to_date"}
                        continue
                    start_date = last_date
            else:
                start_date = None

        if start_date is None:
            # No existing data — fetch the full history from 2010
            start_date = "20100101"

        try:
            df = collector.get_index_daily(code, start_date=start_date, end_date=target_dt)
        except Exception as e:
            results[code] = {"status": "failed", "error": str(e)}
            continue

        if df is None or df.empty:
            results[code] = {"status": "skipped", "reason": "no_new_data"}
            continue

        if "ts_code" in df.columns and df["ts_code"].nunique() == 1:
            df = df.drop(columns=["ts_code"])

        df = df.sort_values("trade_date").reset_index(drop=True)

        if csv_path.exists():
            import pandas as pd
            existing = pd.read_csv(csv_path)
            combined = pd.concat([existing, df], ignore_index=True)
            combined["trade_date"] = combined["trade_date"].astype(str)
            combined = combined.drop_duplicates(subset=["trade_date"]).sort_values("trade_date").reset_index(drop=True)
            combined.to_csv(csv_path, index=False)
        else:
            df.to_csv(csv_path, index=False)

        results[code] = {
            "status": "success",
            "rows_added": len(df),
            "date_range": f"{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}",
        }

    return results


def _suspension_evidence_from_reused_frame(
    frame: pd.DataFrame,
    *,
    symbols: set[str],
    start_date: str,
    end_date: str,
) -> dict | None:
    """Rebuild the small suspension map from a verified raw supplier payload."""

    if frame is None or frame.empty:
        return {
            "status": "empty",
            "suspended_dates_by_symbol": {},
            "raw_frame": pd.DataFrame(columns=list(frame.columns) if frame is not None else []),
            "errors": [],
            "attempt_count": 0,
        }
    if not {"ts_code", "trade_date"}.issubset(frame.columns):
        return None
    allowed = {str(symbol) for symbol in symbols}
    response_symbols = frame["ts_code"].astype("string")
    if response_symbols.isna().any() or not set(response_symbols.astype(str)).issubset(allowed):
        return None
    date_text = (
        frame["trade_date"].astype(str).str.strip().str.replace("-", "", regex=False).str[:8]
    )
    normalized_dates = pd.to_datetime(date_text, format="%Y%m%d", errors="coerce")
    if normalized_dates.isna().any():
        return None
    start = pd.to_datetime(str(start_date).replace("-", ""), format="%Y%m%d")
    end = pd.to_datetime(str(end_date).replace("-", ""), format="%Y%m%d")
    if (normalized_dates < start).any() or (normalized_dates > end).any():
        return None
    suspended: dict[str, set[str]] = {}
    for symbol, date_value in zip(response_symbols.astype(str), normalized_dates):
        suspended.setdefault(symbol, set()).add(pd.Timestamp(date_value).strftime("%Y-%m-%d"))
    return {
        "status": "success",
        "suspended_dates_by_symbol": suspended,
        "raw_frame": frame,
        "errors": [],
        "attempt_count": 0,
    }


def _validate_history_suspension_response(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    """Return a receipt-safe failure detail for an out-of-scope response."""

    if frame.empty:
        return None
    missing_columns = sorted(
        {"ts_code", "trade_date", "suspend_type"} - set(frame.columns)
    )
    if missing_columns:
        return {
            "reason": "response_missing_columns",
            "missing_columns": missing_columns,
        }
    response_symbols = frame["ts_code"].astype("string")
    if response_symbols.isna().any() or not response_symbols.eq(symbol).all():
        return {"reason": "response_symbol_out_of_scope"}
    response_types = frame["suspend_type"].astype("string")
    if response_types.isna().any() or not response_types.eq("S").all():
        return {"reason": "response_suspend_type_out_of_scope"}
    date_text = (
        frame["trade_date"]
        .astype("string")
        .str.strip()
        .str.replace("-", "", regex=False)
    )
    response_dates = pd.to_datetime(date_text, format="%Y%m%d", errors="coerce")
    start_ts = pd.to_datetime(start_date, format="%Y%m%d")
    end_ts = pd.to_datetime(end_date, format="%Y%m%d")
    if (
        response_dates.isna().any()
        or (response_dates < start_ts).any()
        or (response_dates > end_ts).any()
    ):
        return {"reason": "response_date_out_of_scope"}
    return None


def _fetch_audited_history_suspensions(
    collector: TushareCollector,
    codes: list[str],
    start_date: str,
    end_date: str,
    *,
    is_history_repair: bool,
    run_id: str,
    audit_store: SourceAuditStore,
    resume_proof: dict | None,
    scope_key: str,
    universe: str,
) -> tuple[dict, list[str]]:
    """Receipt one exact full-range ``suspend_d`` shard per history symbol."""

    if universe != "csi1800" or not is_history_repair:
        return {"status": "not_required"}, []
    start = str(start_date).replace("-", "")
    end = str(end_date).replace("-", "")
    symbols = sorted({str(code).strip().upper() for code in codes if str(code).strip()})
    if (
        scope_key != "csi1800"
        or len(start) != 8
        or not start.isdigit()
        or len(end) != 8
        or not end.isdigit()
        or start > end
        or not symbols
    ):
        raise ValueError("history suspension evidence requires a bounded CSI1800 scope")

    reused_before = sum(
        1
        for event in audit_store.run_evidence_summary(run_id)["events"]
        if event["event_type"] == "fetch_shard_reused"
        and event["payload"].get("endpoint") == "suspend_d"
    )
    receipt_ids: list[str] = []
    success_count = 0
    empty_count = 0
    row_count = 0
    failure: dict | None = None

    for symbol in symbols:
        requested_scope = {
            "date_start": start,
            "date_end": end,
            "symbol_count": 1,
            "symbols": [symbol],
            "symbols_sha256": stable_scope_hash([symbol]),
        }
        try:
            frame, receipt_id = collector._fetch_daily_endpoint_with_receipt(
                "suspend_d",
                run_id=run_id,
                audit_store=audit_store,
                requested_scope=requested_scope,
                resume_proof=resume_proof,
                scope_key=scope_key,
                universe=universe,
                request_variant="history_suspend_s_v1",
                identity_columns=("ts_code", "trade_date", "suspend_type"),
                response_validator=lambda response, expected_symbol=symbol: (
                    _validate_history_suspension_response(
                        response,
                        symbol=expected_symbol,
                        start_date=start,
                        end_date=end,
                    )
                ),
                required_endpoint=True,
                supplier_call_delay_seconds=0.35,
                ts_code=symbol,
                start_date=start,
                end_date=end,
                suspend_type="S",
                fields=_HISTORY_SUSPEND_FIELDS,
            )
        except Exception as exc:
            failure = {
                "symbol": symbol,
                "reason": "supplier_failure",
                "error": str(exc),
            }
            break
        if receipt_id is None:
            failure = {"symbol": symbol, "reason": "receipt_missing"}
            break
        receipt_ids.append(str(receipt_id))
        receipt_check = audit_store.verify_fetch_receipt(
            run_id=run_id, receipt_id=str(receipt_id)
        )
        if receipt_check["status"] != "success":
            failure = {
                "symbol": symbol,
                "reason": str(receipt_check.get("reason") or "receipt_invalid"),
            }
            break
        frame = frame if frame is not None else pd.DataFrame()
        if frame.empty:
            empty_count += 1
            continue
        validation_failure = _validate_history_suspension_response(
            frame, symbol=symbol, start_date=start, end_date=end
        )
        if validation_failure is not None:
            failure = {"symbol": symbol, **validation_failure}
            break
        success_count += 1
        row_count += len(frame)

    reused_after = sum(
        1
        for event in audit_store.run_evidence_summary(run_id)["events"]
        if event["event_type"] == "fetch_shard_reused"
        and event["payload"].get("endpoint") == "suspend_d"
    )
    complete = (
        failure is None
        and len(receipt_ids) == len(symbols)
        and success_count + empty_count == len(symbols)
    )
    summary = {
        "status": "success" if complete else "failed",
        "date_start": start,
        "date_end": end,
        "symbol_count": len(symbols),
        "symbols_sha256": stable_scope_hash(symbols),
        "receipt_count": len(receipt_ids),
        "success_count": success_count,
        "empty_count": empty_count,
        "row_count": row_count,
        "reused_count": reused_after - reused_before,
    }
    if failure is not None:
        summary["failure"] = failure
    audit_store.append_event(run_id, "history_suspension_evidence", summary)
    return summary, receipt_ids


def _verify_history_suspension_receipts(
    audit_store: SourceAuditStore,
    *,
    run_id: str,
    summary: dict,
    receipt_ids: list[str],
) -> dict:
    """Recheck history suspension payload identity immediately before terminal trust."""

    failures = []
    for receipt_id in receipt_ids:
        result = audit_store.verify_fetch_receipt(
            run_id=run_id, receipt_id=receipt_id
        )
        if result["status"] != "success":
            failures.append({
                "receipt_id": receipt_id,
                "reason": str(result.get("reason") or "receipt_invalid"),
            })
    expected_count = int(summary.get("symbol_count") or 0)
    complete = (
        summary.get("status") == "success"
        and expected_count > 0
        and len(receipt_ids) == expected_count
        and not failures
    )
    result = {
        "status": "success" if complete else "failed",
        "expected_count": expected_count,
        "receipt_count": len(receipt_ids),
        "failure_count": len(failures),
        "failures": failures[:10],
    }
    audit_store.append_event(run_id, "history_suspension_terminal_check", result)
    return result


def _do_raw_fetch(
    collector: TushareCollector,
    codes: list[str],
    target_dt: str,
    *,
    since_date: str | None = None,
    run_id: str | None = None,
    audit_store: SourceAuditStore | None = None,
    resume_proof: dict | None = None,
    scope_key: str | None = None,
    universe: str | None = None,
    local_max_workers: int = 1,
) -> dict:
    """
    Fetch raw data from ``since_date`` through the target date.

    A true single-day repair uses ``update_daily``: all six market-wide
    trade-date endpoints are fetched once, and only the requested universe is
    written.  An explicitly requested multi-day repair deliberately keeps the
    historical path because it has different range and merge semantics.
    """
    if not codes:
        return {"status": "skipped", "reason": "all_stocks_already_up_to_date"}

    t0 = time.time()

    try:
        fetch_start = str(since_date or target_dt).replace("-", "")
        target_dt = str(target_dt).replace("-", "")
        if fetch_start == target_dt:
            evidence_kwargs = (
                {
                    "run_id": run_id,
                    "audit_store": audit_store,
                    "scope_key": str(scope_key or universe or "ad_hoc"),
                    "universe": str(universe or scope_key or "ad_hoc"),
                    **({"resume_proof": resume_proof} if resume_proof is not None else {}),
                }
                if run_id is not None and audit_store is not None
                else {}
            )
            collector_result = collector.update_daily(
                target_dt,
                codes=codes,
                include_financial=True,
                force=True,
                **evidence_kwargs,
            )
        else:
            # This path may fetch prior dates, and is only reachable for an
            # explicit historical repair window.
            collector_result = collector.update_universe_history(
                universe=codes,  # pass the list directly (get_universe handles list)
                start_date=fetch_start,
                end_date=target_dt,
                incremental=False,
                # Keep the collector's proven 50-symbol shard size.  Larger
                # quarter batches can hit supplier row limits and silently
                # turn a complete-looking receipt into truncated evidence.
                batch_size=50,
                include_moneyflow=True,
                include_margin=True,
                run_id=run_id,
                audit_store=audit_store,
                resume_proof=resume_proof,
                scope_key=str(scope_key or universe or "ad_hoc"),
                evidence_universe=str(universe or scope_key or "ad_hoc"),
                local_max_workers=local_max_workers,
            )
        elapsed = time.time() - t0
        mutation_count = 0
        if isinstance(collector_result, dict):
            recorded_count = collector_result.get("mutation_count")
            mutation_count = (
                int(recorded_count)
                if recorded_count is not None
                else len(collector_result.get("mutations") or [])
            )
        required_missing_by_endpoint = (
            collector_result.get("required_endpoint_missing_symbols", {})
            if isinstance(collector_result, dict)
            else {}
        )
        required_missing_by_endpoint = {
            str(endpoint): sorted({str(symbol) for symbol in symbols})
            for endpoint, symbols in required_missing_by_endpoint.items()
        }
        requested_missing = sorted(
            {symbol for symbols in required_missing_by_endpoint.values() for symbol in symbols}
        )
        required_field_missing = (
            collector_result.get("required_field_missing_symbols", {})
            if isinstance(collector_result, dict)
            else {}
        )
        required_field_missing = {
            str(endpoint): {
                str(field): sorted({str(symbol) for symbol in symbols})
                for field, symbols in fields.items()
            }
            for endpoint, fields in required_field_missing.items()
        }
        missing_required_values = sorted(
            {
                f"{endpoint}:{field}:{symbol}"
                for endpoint, fields in required_field_missing.items()
                for field, symbols in fields.items()
                for symbol in symbols
            }
        )
        suspended_missing: list[str] = []
        unexplained_missing = list(requested_missing)
        suspension_receipt_id = None
        suspension_query_status = "not_required"
        if requested_missing and fetch_start == target_dt:
            from qsys.ops.data_coverage import fetch_suspension_evidence

            suspension_scope = checkpoint_requested_scope(
                {
                    "date_start": target_dt,
                    "date_end": target_dt,
                    "symbol_count": len(requested_missing),
                    "symbols_sha256": stable_scope_hash(requested_missing),
                },
                source="tushare",
                endpoint="suspend_d",
                contract_version="1",
                scope_key=str(scope_key or universe or "ad_hoc"),
                universe=str(universe or scope_key or "ad_hoc"),
            )
            reused_suspension = None
            if resume_proof is not None and audit_store is not None and run_id is not None:
                reused_suspension = audit_store.reuse_fetch_shard(
                    run_id=run_id,
                    resume_proof=resume_proof,
                    source="tushare",
                    endpoint="suspend_d",
                    contract_version="1",
                    requested_scope=suspension_scope,
                )
            suspension_evidence = None
            if reused_suspension is not None:
                suspension_evidence = _suspension_evidence_from_reused_frame(
                    reused_suspension["frame"],
                    symbols=set(requested_missing),
                    start_date=target_dt,
                    end_date=target_dt,
                )
                if suspension_evidence is not None:
                    suspension_receipt_id = str(reused_suspension["receipt_id"])
            if suspension_evidence is None:
                suspension_evidence = fetch_suspension_evidence(
                    symbols=set(requested_missing), start_date=target_dt, end_date=target_dt
                )
            suspension_query_status = str(suspension_evidence["status"])
            raw_suspension_frame = suspension_evidence["raw_frame"]
            if (
                suspension_receipt_id is None
                and audit_store is not None
                and run_id is not None
            ):
                suspension_receipt_id = audit_store.record_fetch(
                    run_id=run_id,
                    source="tushare",
                    endpoint="suspend_d",
                    status=suspension_query_status,
                    requested_scope=suspension_scope,
                    returned_rows=len(raw_suspension_frame),
                    attempt_count=max(1, int(suspension_evidence["attempt_count"])),
                    payload_frame=(
                        raw_suspension_frame
                        if suspension_query_status in {"success", "partial"}
                        else None
                    ),
                    published_at=None,
                    error=suspension_evidence["errors"] or None,
                    **normalized_response_metadata(raw_suspension_frame),
                )
            suspended = suspension_evidence["suspended_dates_by_symbol"]
            target_display = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:8]}"
            if suspension_query_status in {"success", "empty"}:
                suspended_missing = sorted(
                    symbol for symbol in requested_missing if target_display in suspended.get(symbol, set())
                )
            unexplained_missing = sorted(set(requested_missing) - set(suspended_missing))
        scope_coverage = {
            "status": "success" if not unexplained_missing and not missing_required_values else "failed",
            "requested_count": len(codes),
            "missing_count": len(requested_missing),
            "suspended_exception_count": len(suspended_missing),
            "suspended_exceptions": suspended_missing,
            "unexplained_missing": unexplained_missing,
            "suspension_query_status": suspension_query_status,
            "suspension_receipt_id": suspension_receipt_id,
            "required_endpoint_missing_symbols": required_missing_by_endpoint,
            "required_field_missing_symbols": required_field_missing,
            "required_field_missing_count": len(missing_required_values),
            "suspended_exceptions_by_endpoint": {
                endpoint: sorted(set(symbols).intersection(suspended_missing))
                for endpoint, symbols in required_missing_by_endpoint.items()
            },
            "unexplained_missing_by_endpoint": {
                endpoint: sorted(set(symbols).intersection(unexplained_missing))
                for endpoint, symbols in required_missing_by_endpoint.items()
            },
        }
        if audit_store is not None and run_id is not None:
            collector_status = (
                collector_result.get("status") if isinstance(collector_result, dict) else "success"
            )
            audit_store.append_event(
                run_id,
                "canonical_commit",
                {
                    "status": (
                        "success"
                        if collector_status in {"success", "empty", "noop"}
                        else "failed"
                    ),
                    "mutation_count": mutation_count,
                },
            )
            audit_store.append_event(run_id, "source_scope_coverage", scope_coverage)
        return {
            "status": "success",
            "codes_fetched": len(codes),
            "since_date": fetch_start,
            "target_date": target_dt,
            "path": "single_day_trade_date" if fetch_start == target_dt else "history_range",
            "collector_status": (
                collector_result.get("status") if isinstance(collector_result, dict) else "success"
            ),
            "mutation_count": mutation_count,
            "evidence_field_endpoints": (
                collector_result.get("evidence_field_endpoints", {})
                if isinstance(collector_result, dict)
                else {}
            ),
            "history_scope_coverage": (
                collector_result.get("history_scope_coverage", {})
                if isinstance(collector_result, dict)
                else {}
            ),
            "source_scope_coverage": scope_coverage,
            "elapsed_s": round(elapsed, 1),
        }
    except Exception as e:
        elapsed = time.time() - t0
        log.error(f"Raw fetch failed: {e}")
        return {"status": "failed", "codes_fetched": 0, "elapsed_s": round(elapsed, 1), "error": str(e)}


def _readiness_check(
    adapter: QlibAdapter,
    target_dt: str,
    *,
    universe: str,
    min_active: int,
) -> DataHealthReport:
    """Run comprehensive readiness checks after sync, using the unified health system.

    Returns a ``DataHealthReport`` with separate *blocking* and *warnings* lists.
    """
    from qsys.data.health import inspect_qlib_data_health

    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:]}"

    report = inspect_qlib_data_health(
        target_date,
        feature_fields=["$open", "$high", "$low", "$close", "$volume", "$factor"],
        universe=universe,
        min_active_instruments=min_active,
    )
    return report


def _write_audit(audit_dir: Path, report: dict):
    """Write per-day audit record as JSON."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    report = {**report, "trust_state": LEGACY_UNTRUSTED}
    date_str = report.get("target_date", "unknown")
    universe = str(report.get("universe") or "csi800")
    run_suffix = f"_{report['run_id']}" if report.get("run_id") else ""
    path = audit_dir / f"sync_{universe}_{date_str}{run_suffix}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info(f"Audit: {path}")
    return path


def _abort_if_stage_failed(
    report: dict,
    *,
    stage: str,
    summary: dict,
    do_apply: bool,
    audit_dir: Path = Path("data/audit"),
    evidence: dict | None = None,
    outer_owned_terminal: bool = False,
) -> None:
    """Persist a failed-stage audit and stop before stale data can look ready."""

    if str(summary.get("status")) != "failed":
        return
    report["overall_status"] = "failed"
    report["failure_stage"] = stage
    report["ended_at"] = datetime.now().isoformat()
    if do_apply:
        _write_audit(audit_dir, report)
        if evidence:
            evidence["store"].append_event(
                evidence["run_id"],
                "inner_stage_failed",
                {"stage": stage, "summary": summary},
            )
            if not outer_owned_terminal:
                evidence["store"].finalize_run(
                    run_id=evidence["run_id"],
                    source="tushare",
                    scope_key=evidence["universe"],
                    range_start=evidence["target_date"],
                    range_end=evidence["target_date"],
                    fields=["*"],
                    gates={
                        "fetch": False,
                        "raw_payloads": False,
                        "canonical_commit": False,
                        "qlib_readback": False,
                        "readiness": False,
                        "contiguous_range": False,
                    },
                    receipt_root=evidence["receipt_root"],
                    trust_state="untrusted",
                )
    raise RuntimeError(f"{stage} failed: {summary.get('error', 'unknown error')}")


def _finalize_market_evidence(
    *,
    audit_store: SourceAuditStore,
    run_id: str,
    universe: str,
    range_start: str,
    range_end: str,
    gates: dict,
    receipt_root: Path,
    prior_trusted: bool,
    unchanged: bool,
    previous_open_session: str | None,
    allow_trusted: bool,
    fields: tuple[str, ...] = TRUSTED_DAILY_FIELDS,
    allow_initial_history: bool = False,
    field_range_starts: dict[str, str] | None = None,
) -> dict:
    if unchanged:
        return audit_store.finalize_unchanged(
            run_id=run_id,
            gates=gates,
            receipt_root=receipt_root,
            prior_trusted=allow_trusted and prior_trusted and all(gates.values()),
        )
    return audit_store.finalize_run(
        run_id=run_id,
        source="tushare",
        scope_key=universe,
        range_start=range_start,
        range_end=range_end,
        fields=fields,
        gates=gates,
        receipt_root=receipt_root,
        trust_state=TRUSTED if allow_trusted and all(gates.values()) else "untrusted",
        previous_open_session=previous_open_session,
        allow_initial_history=allow_initial_history,
        field_range_starts=field_range_starts,
    )


def _publish_wrapper_terminal_gates(
    *, audit_store: SourceAuditStore, run_id: str, payload: dict
) -> None:
    """Publish inner gates without taking terminal receipt ownership."""

    audit_store.append_event(run_id, "inner_terminal_gates", payload)
    gates = dict(payload.get("gates") or {})
    passed = all(gates.values()) and (
        bool(payload.get("prior_trusted")) if payload.get("mode") == "unchanged" else True
    )
    if passed:
        return
    audit_store.append_event(
        run_id,
        "inner_terminal_gate_failed",
        {"gates": gates, "outer_owned_terminal": True},
    )
    raise SystemExit(2)


def _fetch_daily_industry_after_precheck(
    collector,
    codes: list[str],
    target_date: str,
    *,
    precheck_noop: bool,
    prior_core_trusted: bool,
    prior_industry_trusted: bool,
    run_id: str,
    audit_store: SourceAuditStore,
    resume_proof: dict | None,
    scope_key: str,
    universe: str,
) -> tuple[dict, list[str]]:
    """Avoid supplier calls for a completed target and block untrusted repair."""

    if universe == "csi1800" and precheck_noop:
        if prior_core_trusted and prior_industry_trusted:
            summary = {
                "status": "not_required",
                "reason": "trusted_target_already_complete",
                "target_date": target_date,
                "supplier_calls": 0,
            }
        else:
            summary = {
                "status": "failed",
                "error": (
                    "REPAIR_REQUIRED: canonical target rows preexist without trusted "
                    "core+industry evidence; run explicit CSI1800 history repair"
                ),
                "target_date": target_date,
                "prior_core_trusted": prior_core_trusted,
                "prior_industry_trusted": prior_industry_trusted,
                "supplier_calls": 0,
            }
        audit_store.append_event(run_id, "daily_industry_evidence", summary)
        return summary, []
    return fetch_audited_daily_industry(
        collector, codes, target_date, run_id=run_id, audit_store=audit_store,
        resume_proof=resume_proof, scope_key=scope_key, universe=universe,
    )


def _load_last_audit(audit_dir: Path, universe: str = "csi800") -> dict | None:
    """Load the latest audit record, for incremental skip detection."""
    if not audit_dir.exists():
        return None
    records = sorted(audit_dir.glob(f"sync_{universe}_*.json"))
    if not records:
        return None
    try:
        return json.loads(records[-1].read_text(encoding="utf-8"))
    except Exception:
        return None


def _notify_telegram(report: dict) -> None:
    """Send sync summary to Telegram channel. Non-blocking: failures are logged only."""
    try:
        from qsys.ops.telegram import send_telegram_message
    except Exception as exc:
        log.warning(f"Telegram notify skipped (import failed): {exc}")
        return

    target_date = report.get("target_date_display", report.get("target_date", "?"))
    status = report.get("overall_status", "unknown")

    steps = report.get("steps", {})
    universe = steps.get("get_universe", {})
    pre_check = steps.get("pre_check", {})
    raw_fetch = steps.get("raw_fetch", {})
    qlib_convert = steps.get("qlib_convert", {})

    constituent_count = universe.get("constituent_count", "?")
    up_to_date = pre_check.get("already_up_to_date", 0)
    fetched = raw_fetch.get("codes_fetched", raw_fetch.get("would_fetch", 0))
    qlib_elapsed = qlib_convert.get("elapsed_s", "?")

    # Count readiness checks
    readiness_detail = report.get("readiness", {})
    blocking = readiness_detail.get("blocking", [])
    warnings = readiness_detail.get("warnings", [])

    lines = [
        f"Qsys {str(report.get('universe') or 'csi800').upper()} Daily Sync — {target_date}",
        f"Status: {status}",
        f"Constituents: {constituent_count} | Up-to-date: {up_to_date} | Fetched: {fetched}",
    ]
    if isinstance(qlib_elapsed, (int, float)):
        qlib_mode = qlib_convert.get("mode", "?")
        lines.append(f"Qlib convert ({qlib_mode}): {qlib_elapsed}s")

    if blocking:
        lines.append(f"Blocking ({len(blocking)}):")
        for b in blocking[:3]:
            lines.append(f"  ⛔ {b}")
        if len(blocking) > 3:
            lines.append(f"  ... +{len(blocking)-3} more")
    if warnings:
        lines.append(f"Warnings ({len(warnings)}):")
        for w in warnings[:3]:
            lines.append(f"  ⚠ {w}")
        if len(warnings) > 3:
            lines.append(f"  ... +{len(warnings)-3} more")
    if not blocking and not warnings:
        lines.append("✅ All checks passed")

    text = "\n".join(lines)

    try:
        result = send_telegram_message(text)
        if result.get("status") == "success":
            log.info("Telegram notification sent")
        else:
            log.warning(f"Telegram notification failed: {result.get('error')}")
    except Exception as exc:
        log.warning(f"Telegram notification failed (exception): {exc}")


def _main_under_writer_lock(writer_lock: data_writer_lock) -> None:
    parser = argparse.ArgumentParser(description="CSI daily incremental data sync")
    parser.add_argument(
        "--universe",
        choices=("csi800", "csi1800"),
        default="csi800",
        help="CSI800 current constituents or immutable as-of CSI1800 snapshot",
    )
    parser.add_argument("--target-date", default=None, help="Target trade date (YYYY-MM-DD or YYYYMMDD)")
    parser.add_argument(
        "--repair-start-date",
        default=None,
        help="Explicit historical repair start (YYYY-MM-DD or YYYYMMDD); must be <= target date",
    )
    parser.add_argument("--no-qlib-convert", action="store_true", help="Skip qlib conversion after raw fetch")
    parser.add_argument("--apply", action="store_true", help="Apply data changes (default is dry-run)")
    parser.add_argument("--force-fetch", action="store_true", help="Skip pre-check, force fetch all stocks")
    parser.add_argument(
        "--qlib-max-workers",
        type=_positive_int,
        default=None,
        help="Maximum workers for the Qlib dump process pool",
    )
    parser.add_argument(
        "--history-local-workers",
        type=_history_local_workers,
        default=min(8, max(1, (os.cpu_count() or 2) // 2)),
        help="Bounded local workers for immutable history checkpoint verification",
    )
    parser.add_argument("--run-id", default=None, help="Explicit shared run identity from the canonical wrapper")
    parser.add_argument("--resume-from-run-id", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--resume-from-receipt-sha256", default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--wrapper-managed-finalize",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    if bool(args.resume_from_run_id) != bool(args.resume_from_receipt_sha256):
        parser.error(
            "--resume-from-run-id requires --resume-from-receipt-sha256 from the wrapper"
        )
    if args.wrapper_managed_finalize and not writer_lock.inherited:
        parser.error("--wrapper-managed-finalize requires a verified inherited writer lock fd")
    if args.resume_from_run_id and not args.wrapper_managed_finalize:
        parser.error("--resume-from-run-id requires --wrapper-managed-finalize")
    if args.resume_from_run_id and not args.apply:
        parser.error("--resume-from-run-id requires --apply")
    if args.resume_from_run_id and args.force_fetch:
        parser.error("--resume-from-run-id and --force-fetch are mutually exclusive")
    universe = args.universe

    # Resolve target date
    target_dt = _resolve_target_date(args.target_date)
    sync_window = _resolve_sync_window(target_dt, args.repair_start_date)
    sync_start = sync_window["start_date"]
    is_history_repair = sync_window["mode"] == "explicit_historical_repair"
    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:]}"
    do_apply = args.apply
    run_id = validate_run_id(args.run_id or new_run_id("data_sync"))

    log.info("=" * 60)
    log.info(
        "%s Daily Sync — target=%s, apply=%s, run_id=%s",
        universe.upper(),
        target_date,
        do_apply,
        run_id,
    )
    log.info("=" * 60)

    report = {
        "run_id": run_id,
        "universe": universe,
        "target_date": target_dt,
        "target_date_display": target_date,
        "applied": do_apply,
        "started_at": datetime.now().isoformat(),
        "steps": {},
        "overall_status": "unknown",
    }
    audit_dir = Path(cfg.get_path("root")) / "audit"
    source_audit = SourceAuditStore(audit_dir / "audit.db") if do_apply else None
    receipt_root = audit_dir / "source_runs"
    evidence = (
        {
            "store": source_audit,
            "run_id": run_id,
            "universe": universe,
            "target_date": target_dt,
            "receipt_root": receipt_root,
        }
        if source_audit is not None
        else None
    )
    if source_audit is not None:
        inner_lineage = {
            "entrypoint": "scripts/ops/sync_csi800_daily.py",
            "universe": universe,
            "target_date": target_dt,
        }
        if sync_start != target_dt:
            inner_lineage["range_start"] = sync_start
        source_audit.append_event(
            run_id,
            "run_started",
            inner_lineage,
        )
        global _CRASH_EVIDENCE
        _CRASH_EVIDENCE = {
            "store": source_audit,
            "run_id": run_id,
            "receipt_root": receipt_root,
            "entrypoint": "scripts/ops/sync_csi800_daily.py",
        }
    resume_proof = None
    if args.resume_from_run_id:
        if source_audit is None:
            parser.error("--resume-from-run-id requires --apply")
        resume_proof = source_audit.validate_resume_run(
            resume_from_run_id=args.resume_from_run_id,
            expected_entrypoint="scripts/data_sync.py",
            universe=universe,
            target_date=target_dt,
            range_start=sync_start if sync_start != target_dt else None,
            expected_receipt_sha256=args.resume_from_receipt_sha256,
        )
        source_audit.append_event(
            run_id,
            "resume_from_run_validated",
            {
                "resume_from_run_id": resume_proof["resume_from_run_id"],
                "source_receipt_sha256": resume_proof["receipt_sha256"],
                "universe": universe,
                "target_date": target_dt,
            },
        )

    # Step 0: Initialize Qlib
    t0 = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()
    report["steps"]["init_qlib"] = {"elapsed_s": round(time.time() - t0, 1)}

    # Step 1: Resolve the target-date universe.
    t0 = time.time()
    collector = TushareCollector()
    store = StockDataStore()
    report["sync_window"] = dict(sync_window)
    if universe == "csi1800" and is_history_repair:
        codes, step1 = _load_csi1800_research_union(Path(cfg.get_path("root")))
        step1["elapsed_s"] = round(time.time() - t0, 1)
    elif universe == "csi1800":
        from qsys.ops.pit_universe_snapshot import resolve_csi1800_pit_snapshot

        pit_snapshot = resolve_csi1800_pit_snapshot(
            collector,
            as_of_date=target_dt,
            data_root=Path(cfg.get_path("root")),
            apply=do_apply,
        )
        codes = list(pit_snapshot.instruments)
        step1 = {
            **pit_snapshot.to_dict(),
            "elapsed_s": round(time.time() - t0, 1),
        }
    else:
        codes = collector.get_universe("csi800")
        step1 = {
            "constituent_count": len(codes),
            "snapshot_semantics": "current_constituents",
            "elapsed_s": round(time.time() - t0, 1),
        }
    report["steps"]["get_universe"] = step1
    log.info("%s constituents: %s", universe.upper(), len(codes))

    if not codes:
        log.error("Empty %s universe, aborting.", universe)
        report["overall_status"] = "failed"
        report["ended_at"] = datetime.now().isoformat()
        _write_audit(audit_dir, report)
        sys.exit(1)

    # Step 2: Pre-check — which stocks already have target date?
    t0 = time.time()
    if args.force_fetch or resume_proof is not None:
        status_check = {"have": [], "missing": codes, "total": len(codes), "already_up_to_date": 0, "need_fetch": len(codes)}
        if args.force_fetch:
            log.info("Force fetch: skipping pre-check, fetching all stocks")
        else:
            log.info("Resume: running full target universe so verified shards clone into the fresh run")
    else:
        status_check = _check_stock_data_status(store, codes, target_dt)
        step2 = {"checked_count": len(codes), "already_up_to_date": status_check["already_up_to_date"],
                 "need_fetch": status_check["need_fetch"], "elapsed_s": round(time.time() - t0, 1)}
        report["steps"]["pre_check"] = step2
        log.info(f"Pre-check: {status_check['already_up_to_date']}/{status_check['total']} stocks already have {target_dt}")

    prior_scope_trusted_at_start = bool(
        source_audit
        and source_audit.has_trusted_range(
            source="tushare",
            scope_key=universe,
            range_start=target_dt,
            range_end=target_dt,
            fields=TRUSTED_DAILY_FIELDS,
        )
    )
    prior_industry_trusted_at_start = bool(
        source_audit
        and universe == "csi1800"
        and source_audit.has_trusted_range(
            source="tushare",
            scope_key=universe,
            range_start=target_dt,
            range_end=target_dt,
            fields=("industry",),
        )
    )
    untrusted_preexisting_symbols = (
        []
        if args.force_fetch or prior_scope_trusted_at_start or sync_start < target_dt
        else sorted(status_check["have"])
    )
    if source_audit is not None and untrusted_preexisting_symbols:
        source_audit.append_event(
            run_id,
            "preexisting_untrusted_scope",
            {
                "symbol_count": len(untrusted_preexisting_symbols),
                "symbols_sha256": stable_scope_hash(untrusted_preexisting_symbols),
                "recovery": "rerun_with_force_fetch",
            },
        )

    # Step 3: Raw data fetch
    t0 = time.time()
    raw_summary = {"skipped": True, "reason": "all_up_to_date", "elapsed_s": 0}
    precheck_noop = False
    if do_apply:
        fetch_codes = codes if sync_start < target_dt else status_check["missing"]
        if not fetch_codes:
            precheck_noop = True
            log.info("All stocks up to date, skipping raw fetch.")
            if source_audit is not None:
                source_audit.append_event(
                    run_id,
                    "precheck_noop",
                    {
                        "target_date": target_dt,
                        "reason": "canonical_precheck_already_up_to_date",
                    },
                )
        else:
            if sync_start < target_dt:
                log.info(
                    "Explicit historical repair: %s -> %s; fetching full universe",
                    sync_start,
                    target_dt,
                )
            raw_summary = _do_raw_fetch(
                collector,
                fetch_codes,
                target_dt,
                since_date=sync_start,
                run_id=run_id,
                audit_store=source_audit,
                resume_proof=resume_proof,
                scope_key=universe,
                universe=universe,
                local_max_workers=args.history_local_workers,
            )
        report["steps"]["raw_fetch"] = raw_summary
    else:
        if status_check["need_fetch"] > 0:
            log.info(f"DRY RUN — would fetch {status_check['need_fetch']} stocks for {target_dt}")
            raw_summary = {"dry_run": True, "would_fetch": status_check["need_fetch"], "elapsed_s": round(time.time() - t0, 1)}
        report["steps"]["raw_fetch"] = raw_summary
    _abort_if_stage_failed(
        report,
        stage="raw_fetch",
        summary=raw_summary,
        do_apply=do_apply,
        audit_dir=audit_dir,
        evidence=evidence,
        outer_owned_terminal=args.wrapper_managed_finalize,
    )

    history_suspension_summary: dict = {"status": "not_required"}
    history_suspension_receipt_ids: list[str] = []
    if do_apply:
        (
            history_suspension_summary,
            history_suspension_receipt_ids,
        ) = _fetch_audited_history_suspensions(
            collector,
            codes,
            sync_start,
            target_dt,
            is_history_repair=is_history_repair,
            run_id=run_id,
            audit_store=source_audit,
            resume_proof=resume_proof,
            scope_key=universe,
            universe=universe,
        )
        if history_suspension_summary["status"] != "not_required":
            report["steps"]["history_suspension_evidence"] = history_suspension_summary
            _abort_if_stage_failed(
                report,
                stage="history_suspension_evidence",
                summary=history_suspension_summary,
                do_apply=do_apply,
                audit_dir=audit_dir,
                evidence=evidence,
                outer_owned_terminal=args.wrapper_managed_finalize,
            )

    history_industry_summary: dict = {"status": "not_required"}
    history_industry_receipt_ids: list[str] = []
    if do_apply:
        history_industry_summary, history_industry_receipt_ids = fetch_audited_history_industry(
            collector, codes, target_dt,
            is_history_repair=is_history_repair,
            run_id=run_id, audit_store=source_audit,
            resume_proof=resume_proof, scope_key=universe, universe=universe,
        )
        if history_industry_summary["status"] != "not_required":
            report["steps"]["history_industry_evidence"] = history_industry_summary
            _abort_if_stage_failed(
                report, stage="history_industry_evidence",
                summary=history_industry_summary, do_apply=do_apply,
                audit_dir=audit_dir, evidence=evidence,
                outer_owned_terminal=args.wrapper_managed_finalize,
            )

    daily_industry_summary: dict = {"status": "not_required"}
    daily_industry_receipt_ids: list[str] = []
    if do_apply and not is_history_repair:
        daily_industry_summary, daily_industry_receipt_ids = _fetch_daily_industry_after_precheck(
            collector, codes, target_dt,
            precheck_noop=precheck_noop,
            prior_core_trusted=prior_scope_trusted_at_start,
            prior_industry_trusted=prior_industry_trusted_at_start,
            run_id=run_id, audit_store=source_audit,
            resume_proof=resume_proof, scope_key=universe, universe=universe,
        )
        if daily_industry_summary["status"] != "not_required":
            report["steps"]["daily_industry_evidence"] = daily_industry_summary
            _abort_if_stage_failed(
                report, stage="daily_industry_evidence",
                summary=daily_industry_summary, do_apply=do_apply,
                audit_dir=audit_dir, evidence=evidence,
                outer_owned_terminal=args.wrapper_managed_finalize,
            )

    # Step 4: Index daily update (always applies when do_apply, no separate dry-run for this)
    if do_apply:
        t0 = time.time()
        index_result = _update_index_daily(collector, target_dt)
        report["steps"]["index_daily"] = {
            "indices": index_result,
            "elapsed_s": round(time.time() - t0, 1),
        }

    # Step 5: Qlib convert
    qlib_summary = {"mode": "skipped", "status": "skipped"}
    if do_apply and not args.no_qlib_convert:
        since = (
            f"{sync_start[:4]}-{sync_start[4:6]}-{sync_start[6:]}"
        )
        try:
            t1 = time.time()
            if args.qlib_max_workers is None:
                adapter.convert_incremental(since)
            else:
                adapter.convert_incremental(since, max_workers=args.qlib_max_workers)
            elapsed = round(time.time() - t1, 1)
            qlib_summary = {"mode": "incremental", "status": "success", "elapsed_s": elapsed}
            log.info(f"Qlib incremental: {elapsed}s")
        except Exception as e:
            log.warning(f"Incremental failed ({e}), trying fix mode...")
            try:
                t1 = time.time()
                if args.qlib_max_workers is None:
                    adapter.convert_fix(since)
                else:
                    adapter.convert_fix(since, max_workers=args.qlib_max_workers)
                elapsed = round(time.time() - t1, 1)
                qlib_summary = {"mode": "fix", "status": "success", "elapsed_s": elapsed}
                log.info(f"Qlib fix: {elapsed}s")
            except Exception as e2:
                log.error(f"Qlib convert failed: {e2}")
                qlib_summary = {"mode": "failed", "status": "failed", "error": str(e2)}
    report["steps"]["qlib_convert"] = qlib_summary
    _abort_if_stage_failed(
        report,
        stage="qlib_convert",
        summary=qlib_summary,
        do_apply=do_apply,
        audit_dir=audit_dir,
        evidence=evidence,
        outer_owned_terminal=args.wrapper_managed_finalize,
    )

    # Exact insert/update receipts, rather than a date watermark, select the
    # symbols that require dump_fix and value readback.  This is what makes a
    # same-key source revision visible in Qlib.
    history_mutation_run_ids = (
        source_audit.resume_lineage_run_ids(run_id)
        if source_audit is not None and is_history_repair
        else []
    )
    history_mutation_symbols = sorted({
        symbol
        for item in history_mutation_run_ids
        for symbol in source_audit.changed_mutation_symbols(item)
    }) if source_audit is not None else []
    mutations = (
        source_audit.changed_mutations(run_id)
        if source_audit is not None and not is_history_repair
        else []
    )
    if args.no_qlib_convert and (mutations or history_mutation_symbols):
        mutation_refresh = {
            "status": "skipped",
            "reason": "qlib conversion disabled by operator",
            "changed_symbols": (
                history_mutation_symbols
                if is_history_repair
                else sorted({str(item["symbol"]) for item in mutations})
            ),
            "verified_value_count": 0,
        }
    elif source_audit is not None and is_history_repair:
        mutation_refresh = _refresh_and_verify_history_mutation_store(
            adapter,
            store,
            source_audit,
            history_mutation_run_ids,
            apply=do_apply,
            require_pit_industry=(universe == "csi1800"),
            pit_industry_until_date=target_dt,
            qlib_max_workers=args.qlib_max_workers,
        )
    else:
        mutation_refresh = _refresh_and_verify_changed_symbols(
            adapter,
            store,
            mutations,
            target_dt=target_dt,
            apply=do_apply,
            history_mode=False,
            qlib_max_workers=args.qlib_max_workers,
        )
    report["steps"]["mutation_qlib_refresh"] = mutation_refresh
    if source_audit is not None:
        source_audit.append_event(run_id, "qlib_readback", mutation_refresh)
    _abort_if_stage_failed(
        report,
        stage="mutation_qlib_refresh",
        summary=mutation_refresh,
        do_apply=do_apply,
        audit_dir=audit_dir,
        evidence=evidence,
        outer_owned_terminal=args.wrapper_managed_finalize,
    )

    # Step 6: Reconcile same-date canonical rows against non-empty Qlib rows.
    # This catches the case where dump_update advanced the global calendar but
    # silently omitted one or more symbols on that same trading day.
    try:
        same_date_summary = _repair_same_date_qlib_gap(
            adapter,
            store,
            codes,
            universe=universe,
            target_dt=target_dt,
            apply=do_apply,
            qlib_max_workers=args.qlib_max_workers,
        )
    except Exception as exc:
        same_date_summary = {
            "status": "failed",
            "target_date": target_dt,
            "error": str(exc),
            "verified_no_gap": False,
        }
    report["steps"]["same_date_qlib_repair"] = same_date_summary
    _abort_if_stage_failed(
        report,
        stage="same_date_qlib_repair",
        summary=same_date_summary,
        do_apply=do_apply,
        audit_dir=audit_dir,
        evidence=evidence,
        outer_owned_terminal=args.wrapper_managed_finalize,
    )

    # Step 7: Refresh instrument files after same-date repair is verified.
    t0 = time.time()
    if do_apply:
        try:
            adapter._refresh_universe_instruments(universe="csi800")
            adapter._refresh_universe_instruments(universe="csi300")
            registry_result = None
            if universe == "csi1800" and not is_history_repair:
                from qsys.ops.pit_universe_snapshot import write_current_qlib_registry

                registry_result = write_current_qlib_registry(
                    qlib_dir=adapter.qlib_dir,
                    universe=universe,
                    instruments=codes,
                    as_of_date=target_dt,
                )
            refresh_summary = {
                "status": "success",
                "operational_registry": registry_result,
                "elapsed_s": round(time.time() - t0, 1),
            }
        except Exception as exc:
            refresh_summary = {
                "status": "failed",
                "error": str(exc),
                "elapsed_s": round(time.time() - t0, 1),
            }
        report["steps"]["refresh_instruments"] = refresh_summary
    else:
        refresh_summary = {"status": "dry_run"}
        report["steps"]["refresh_instruments"] = refresh_summary
    _abort_if_stage_failed(
        report,
        stage="refresh_instruments",
        summary=refresh_summary,
        do_apply=do_apply,
        audit_dir=audit_dir,
        evidence=evidence,
        outer_owned_terminal=args.wrapper_managed_finalize,
    )

    # Step 8: Readiness check
    t0 = time.time()
    readiness_report = _readiness_check(
        adapter,
        target_dt,
        universe=universe,
        min_active=1750 if universe == "csi1800" else 750,
    )
    readiness_elapsed = round(time.time() - t0, 1)
    overall = "ready" if readiness_report.ok else "degraded"
    report["steps"]["readiness_check"] = {"elapsed_s": readiness_elapsed}
    report["readiness"] = {
        "blocking": list(readiness_report.blocking_issues),
        "warnings": list(readiness_report.warnings),
        "overall": overall,
    }
    report["overall_status"] = overall
    report["ended_at"] = datetime.now().isoformat()
    if source_audit is not None:
        source_audit.append_event(
            run_id,
            "daily_readiness",
            {"status": "success" if not readiness_report.blocking_issues else "failed"},
        )

    # Print JSON report to stdout (parsable by systemd/journald)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))

    # Write audit
    if do_apply:
        _write_audit(audit_dir, report)

    evidence_result = None
    if source_audit is not None:
        evidence_summary = source_audit.run_evidence_summary(run_id)
        history_mode = is_history_repair
        history_suspension_required = history_mode and universe == "csi1800"
        evidence_field_endpoints = (
            dict(raw_summary.get("evidence_field_endpoints") or HISTORY_FIELD_ENDPOINTS)
            if history_mode
            else dict(TRUSTED_DAILY_FIELD_ENDPOINTS)
        )
        if not history_mode and universe == "csi1800":
            evidence_field_endpoints["industry"] = "bak_basic"
        field_receipts = (
            source_audit.evaluate_history_field_receipts(
                run_id=run_id, field_endpoints=evidence_field_endpoints
            )
            if history_mode
            else source_audit.evaluate_field_receipts(
                run_id=run_id, field_endpoints=evidence_field_endpoints
            )
        )
        history_scope_checkpoints = (
            source_audit.evaluate_history_scope_checkpoints(
                run_id=run_id,
                coverage=raw_summary.get("history_scope_coverage") or {},
            )
            if history_mode
            else {"status": "not_required"}
        )
        canonical_events = [
            event
            for event in evidence_summary["events"]
            if event["event_type"] == "canonical_commit"
        ]
        scope_events = [
            event
            for event in evidence_summary["events"]
            if event["event_type"] == "source_scope_coverage"
        ]
        scope_payload = scope_events[-1]["payload"] if scope_events else {}
        suspension_receipt_id = scope_payload.get("suspension_receipt_id")
        suspension_evidence_ok = (
            not scope_payload.get("missing_count")
            if not suspension_receipt_id
            else source_audit.verify_fetch_receipt(
                run_id=run_id, receipt_id=str(suspension_receipt_id)
            )["status"] == "success"
        )
        history_suspension_terminal = (
            _verify_history_suspension_receipts(
                source_audit,
                run_id=run_id,
                summary=history_suspension_summary,
                receipt_ids=history_suspension_receipt_ids,
            )
            if history_suspension_required
            else {"status": "not_required"}
        )
        history_suspension_ok = (
            not history_suspension_required
            or history_suspension_terminal["status"] == "success"
        )
        history_industry_required = history_mode and universe == "csi1800"
        history_industry_ok = (
            not history_industry_required
            or (
                history_industry_summary.get("status") == "success"
                and int(history_industry_summary.get("receipt_count") or 0)
                == int(history_industry_summary.get("symbol_count") or -1)
                and len(history_industry_receipt_ids)
                == int(history_industry_summary.get("symbol_count") or -1)
                and all(
                    source_audit.verify_fetch_receipt(run_id=run_id, receipt_id=item)["status"] == "success"
                    for item in history_industry_receipt_ids
                )
            )
        )
        source_scope_ok = (
            bool(scope_events)
            and scope_payload.get("status") == "success"
            and suspension_evidence_ok
            and history_suspension_ok
            and history_industry_ok
            and history_scope_checkpoints["status"] in {"success", "not_required"}
            and not untrusted_preexisting_symbols
        )
        evidence_fields = tuple(evidence_field_endpoints)
        field_range_starts = (
            {"industry": max(sync_start, "20180313")}
            if history_mode and universe == "csi1800"
            else {}
        )
        prior_trusted = source_audit.has_trusted_range(
            source="tushare",
            scope_key=universe,
            range_start=target_dt,
            range_end=target_dt,
            fields=evidence_fields,
        )
        gates = {
            "fetch": prior_trusted if precheck_noop else field_receipts["status"] == "success"
            and source_scope_ok,
            "raw_payloads": prior_trusted if precheck_noop else field_receipts["status"] == "success"
            and history_suspension_ok
            and history_scope_checkpoints["status"] in {"success", "not_required"},
            "canonical_commit": prior_trusted if precheck_noop else bool(canonical_events)
            and canonical_events[-1]["payload"].get("status") == "success",
            "qlib_readback": mutation_refresh.get("status") == "success",
            "readiness": not readiness_report.blocking_issues,
            "contiguous_range": True,
        }
        previous_open_session = _previous_open_session(store, target_dt)
        if not precheck_noop and not history_mode:
            gates["contiguous_range"] = bool(gates["contiguous_range"]) and source_audit.can_advance_contiguous(
                source="tushare",
                scope_key=universe,
                range_start=sync_start,
                target_date=target_dt,
                fields=evidence_fields,
                previous_open_session=previous_open_session,
            )
        if args.wrapper_managed_finalize:
            try:
                _publish_wrapper_terminal_gates(
                    audit_store=source_audit,
                    run_id=run_id,
                    payload={
                        "mode": "unchanged" if precheck_noop else "advance",
                        "gates": gates,
                        "prior_trusted": prior_trusted,
                        "source": "tushare",
                        "scope_key": universe,
                        "range_start": sync_start,
                        "range_end": target_dt,
                        "fields": list(evidence_fields),
                        "previous_open_session": previous_open_session,
                        "allow_initial_history": history_mode,
                        "field_range_starts": field_range_starts,
                    },
                )
            except SystemExit:
                log.error("Inner market evidence gates failed; wrapper owns crash receipt")
                raise
            log.info("Inner market evidence gates passed; wrapper owns terminal finalize")
        else:
            evidence_result = _finalize_market_evidence(
                audit_store=source_audit,
                run_id=run_id,
                universe=universe,
                range_start=sync_start,
                range_end=target_dt,
                gates=gates,
                receipt_root=receipt_root,
                prior_trusted=prior_trusted,
                unchanged=precheck_noop,
                previous_open_session=previous_open_session,
                allow_trusted=True,
                fields=evidence_fields,
                allow_initial_history=history_mode,
                field_range_starts=field_range_starts,
            )
        if evidence_result is not None:
            log.info("Source evidence: %s", evidence_result)
        if evidence_result is not None and evidence_result.get("trust_state") not in {TRUSTED, "trusted_unchanged"}:
            log.error("Core market evidence did not pass terminal trust gates")
            sys.exit(2)

    # Step 9: Telegram notification (non-blocking, apply only)
    if do_apply:
        _notify_telegram(report)

    log.info(f"Done — status={overall}")

    # Exit code for systemd: blocking → exit 2, only warnings → exit 0
    if readiness_report.blocking_issues:
        log.warning(f"Blocking issues ({len(readiness_report.blocking_issues)}), exiting 2")
        sys.exit(2)
    elif readiness_report.warnings:
        log.info(f"Warnings only ({len(readiness_report.warnings)}), exiting 0")


def main() -> None:
    data_root = Path(cfg.get_path("root"))
    global _CRASH_EVIDENCE
    _CRASH_EVIDENCE = None
    with data_writer_lock.from_environment(data_root) as writer_lock:
        try:
            _main_under_writer_lock(writer_lock)
        except Exception as exc:
            # An inherited, inode-verified writer lock means data_sync.py owns
            # the sole terminal receipt for this run.  The child must leave
            # only journal evidence for the parent crash handler.
            if _CRASH_EVIDENCE is not None and not writer_lock.inherited:
                try:
                    _CRASH_EVIDENCE["store"].record_crash_receipt(
                        run_id=_CRASH_EVIDENCE["run_id"],
                        receipt_root=_CRASH_EVIDENCE["receipt_root"],
                        entrypoint=_CRASH_EVIDENCE["entrypoint"],
                        error=repr(exc),
                    )
                except Exception as receipt_exc:
                    log.error("Failed to persist crash receipt: %s", receipt_exc)
            raise


if __name__ == "__main__":
    main()
