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
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.data.storage import StockDataStore
from qsys.data.adapter import QlibAdapter
from qsys.utils.logger import log


def _resolve_target_date(end_date: str | None) -> str:
    """Resolve target date: latest trading day up to today, or explicit date."""
    if end_date:
        return end_date.replace("-", "")

    today_str = datetime.now().strftime("%Y%m%d")

    # Use local trade_cal (data source ground truth, not qlib)
    try:
        cal = StockDataStore().get_calendar()
        if cal is not None and not cal.empty and "is_open" in cal.columns and "cal_date" in cal.columns:
            open_days = sorted(cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist())
            candidate = [d for d in open_days if d <= today_str]
            if candidate:
                latest = candidate[-1]
                #跨年: calendar has no entries for current year, use today
                if latest[:4] != today_str[:4]:
                    return today_str
                return latest
    except Exception as e:
        log.warning(f"Failed to resolve target date via calendar: {e}")

    # Fallback: today — pre_check will decide whether data needs fetching
    return today_str


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


def _canonical_symbols_with_data_on_date(
    store: StockDataStore,
    symbols: list[str],
    target_dt: str,
) -> set[str]:
    """Return symbols with an actual canonical row on ``target_dt``.

    The canonical store is the source of truth for raw availability.  Only a
    non-null numeric close is eligible for the same-date comparison. Explicit
    paused/suspended rows are excluded even when a carried-forward close is
    present; no per-symbol suspension API lookup is needed.
    """

    target_dt = str(target_dt).replace("-", "")[:8]
    available: set[str] = set()
    for symbol in sorted(set(symbols)):
        try:
            frame = store.load_daily(symbol)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to inspect canonical data for {symbol} on {target_dt}: {exc}"
            ) from exc
        if frame is None or frame.empty or "trade_date" not in frame.columns:
            continue
        target_rows = frame.loc[_target_date_values(frame["trade_date"]) == target_dt]
        if target_rows.empty:
            continue
        if "close" not in target_rows.columns:
            continue
        close = pd.to_numeric(target_rows["close"], errors="coerce")
        eligible = close.notna()
        for flag_column in ("paused", "is_suspended"):
            if flag_column in target_rows.columns:
                eligible &= ~_truthy_flags(target_rows[flag_column])
        if eligible.any():
            available.add(symbol)
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
) -> dict:
    """Repair and verify canonical-vs-Qlib same-date symbol gaps.

    This stage is intentionally fail-closed: a failed conversion or any
    residual gap after ``convert_fix_symbols`` is returned as ``failed`` and
    the caller must abort before readiness can be reported.
    """

    canonical = _canonical_symbols_with_data_on_date(store, symbols, target_dt)
    qlib_before = _qlib_symbols_with_data_on_date(adapter, symbols, target_dt)
    missing_before = sorted(canonical - qlib_before)
    summary = {
        "status": "success" if not missing_before else ("dry_run" if not apply else "pending"),
        "target_date": target_dt,
        "canonical_symbols_with_data_count": len(canonical),
        "qlib_symbols_with_data_before_count": len(qlib_before),
        "missing_symbols": missing_before,
        "missing_count": len(missing_before),
        "repaired_symbols": [],
        "qlib_symbols_with_data_after_count": len(qlib_before),
        "residual_symbols": missing_before,
        "residual_count": len(missing_before),
        "verified_no_gap": not missing_before,
    }
    if not missing_before or not apply:
        return summary

    try:
        result = adapter.convert_fix_symbols(missing_before, refresh_universes=[])
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


def _resolve_catchup_start(
    adapter: QlibAdapter,
    store: StockDataStore,
    target_dt: str,
) -> str:
    """Return the first open session missing from the qlib materialized view.

    The qlib calendar is the durable watermark for the last completed
    conversion.  The canonical trade calendar is used to step forward because
    a stale qlib calendar cannot resolve sessions that have not been converted
    yet.  If the two calendars cannot establish a safe interval, fail closed
    instead of guessing with weekday dates.
    """
    cal_path = adapter.qlib_dir / "calendars" / "day.txt"
    if not cal_path.exists():
        return target_dt

    qlib_dates = [
        line.strip().replace("-", "")
        for line in cal_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not qlib_dates:
        return target_dt

    qlib_latest = max(qlib_dates)
    if qlib_latest >= target_dt:
        return target_dt

    calendar = store.get_calendar()
    if calendar is None or calendar.empty:
        raise ValueError(
            "Cannot resolve catch-up window: canonical trade calendar is empty"
        )
    required = {"cal_date", "is_open"}
    if not required.issubset(calendar.columns):
        raise ValueError(
            "Cannot resolve catch-up window: canonical trade calendar lacks "
            f"columns {sorted(required - set(calendar.columns))}"
        )

    open_dates = sorted(
        calendar.loc[calendar["is_open"] == 1, "cal_date"]
        .astype(str)
        .str.replace("-", "", regex=False)
        .loc[lambda values: (values > qlib_latest) & (values <= target_dt)]
        .tolist()
    )
    if not open_dates:
        raise ValueError(
            "Cannot resolve catch-up window from canonical calendar: "
            f"qlib_latest={qlib_latest}, target={target_dt}"
        )
    return open_dates[0]


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


def _do_raw_fetch(
    collector: TushareCollector,
    codes: list[str],
    target_dt: str,
    *,
    since_date: str | None = None,
) -> dict:
    """
    Fetch raw data from ``since_date`` through the target date.
    Uses optimized path: batch-fetch daily/adj/moneyflow by date loop,
    per-stock loop for daily_basic/stk_limit/margin (Tushare API constraints).
    """
    if not codes:
        return {"status": "skipped", "reason": "all_stocks_already_up_to_date"}

    code_str = ",".join(codes)
    t0 = time.time()

    try:
        # Use update_universe_history in a targeted way.
        # The batch-size is set to 200 to maximize batch API efficiency.
        collector.update_universe_history(
            universe=codes,  # pass the list directly (get_universe handles list)
            start_date=since_date or target_dt,
            end_date=target_dt,
            incremental=False,
            batch_size=200,
            include_moneyflow=True,
            include_margin=True,
        )
        elapsed = time.time() - t0
        return {
            "status": "success",
            "codes_fetched": len(codes),
            "since_date": since_date or target_dt,
            "target_date": target_dt,
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
    date_str = report.get("target_date", "unknown")
    universe = str(report.get("universe") or "csi800")
    path = audit_dir / f"sync_{universe}_{date_str}.json"
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
) -> None:
    """Persist a failed-stage audit and stop before stale data can look ready."""

    if str(summary.get("status")) != "failed":
        return
    report["overall_status"] = "failed"
    report["failure_stage"] = stage
    report["ended_at"] = datetime.now().isoformat()
    if do_apply:
        _write_audit(audit_dir, report)
    raise RuntimeError(f"{stage} failed: {summary.get('error', 'unknown error')}")


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


def main() -> None:
    parser = argparse.ArgumentParser(description="CSI daily incremental data sync")
    parser.add_argument(
        "--universe",
        choices=("csi800", "csi1800"),
        default="csi800",
        help="CSI800 current constituents or immutable as-of CSI1800 snapshot",
    )
    parser.add_argument("--target-date", default=None, help="Target trade date (YYYY-MM-DD or YYYYMMDD)")
    parser.add_argument("--no-qlib-convert", action="store_true", help="Skip qlib conversion after raw fetch")
    parser.add_argument("--apply", action="store_true", help="Apply data changes (default is dry-run)")
    parser.add_argument("--force-fetch", action="store_true", help="Skip pre-check, force fetch all stocks")
    args = parser.parse_args()
    universe = args.universe

    # Resolve target date
    target_dt = _resolve_target_date(args.target_date)
    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:]}"
    do_apply = args.apply
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

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

    # Step 0: Initialize Qlib
    t0 = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()
    report["steps"]["init_qlib"] = {"elapsed_s": round(time.time() - t0, 1)}

    # Step 1: Resolve the target-date universe.
    t0 = time.time()
    collector = TushareCollector()
    store = StockDataStore()
    catchup_start = _resolve_catchup_start(adapter, store, target_dt)
    report["catchup_window"] = {
        "start_date": catchup_start,
        "target_date": target_dt,
        "is_catchup": catchup_start < target_dt,
    }
    if universe == "csi1800":
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
    if args.force_fetch:
        status_check = {"have": [], "missing": codes, "total": len(codes), "already_up_to_date": 0, "need_fetch": len(codes)}
        log.info("Force fetch: skipping pre-check, fetching all stocks")
    else:
        status_check = _check_stock_data_status(store, codes, target_dt)
        step2 = {"checked_count": len(codes), "already_up_to_date": status_check["already_up_to_date"],
                 "need_fetch": status_check["need_fetch"], "elapsed_s": round(time.time() - t0, 1)}
        report["steps"]["pre_check"] = step2
        log.info(f"Pre-check: {status_check['already_up_to_date']}/{status_check['total']} stocks already have {target_dt}")

    # Step 3: Raw data fetch
    t0 = time.time()
    raw_summary = {"skipped": True, "reason": "all_up_to_date", "elapsed_s": 0}
    if do_apply:
        fetch_codes = codes if catchup_start < target_dt else status_check["missing"]
        if not fetch_codes:
            log.info("All stocks up to date, skipping raw fetch.")
        else:
            if catchup_start < target_dt:
                log.info(
                    "Catch-up window detected: %s -> %s; fetching full universe",
                    catchup_start,
                    target_dt,
                )
            raw_summary = _do_raw_fetch(
                collector,
                fetch_codes,
                target_dt,
                since_date=catchup_start,
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
            f"{catchup_start[:4]}-{catchup_start[4:6]}-{catchup_start[6:]}"
        )
        try:
            t1 = time.time()
            adapter.convert_incremental(since)
            elapsed = round(time.time() - t1, 1)
            qlib_summary = {"mode": "incremental", "status": "success", "elapsed_s": elapsed}
            log.info(f"Qlib incremental: {elapsed}s")
        except Exception as e:
            log.warning(f"Incremental failed ({e}), trying fix mode...")
            try:
                t1 = time.time()
                adapter.convert_fix(since)
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
    )

    # Step 7: Refresh instrument files after same-date repair is verified.
    t0 = time.time()
    if do_apply:
        try:
            adapter._refresh_universe_instruments(universe="csi800")
            adapter._refresh_universe_instruments(universe="csi300")
            registry_result = None
            if universe == "csi1800":
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

    # Print JSON report to stdout (parsable by systemd/journald)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))

    # Write audit
    if do_apply:
        _write_audit(audit_dir, report)

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


if __name__ == "__main__":
    main()
