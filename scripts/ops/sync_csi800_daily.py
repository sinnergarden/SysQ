#!/usr/bin/env python3
"""
csi800 daily incremental data sync — 每日数据闭环.

Flow:
  1. resolve target trade date
  2. get latest csi800 constituents (via index_weight)
  3. pre-check: skip fetch if all stocks already have target date
  4. batch fetch raw data for missing stocks (single-pass)
  5. update index daily data (7 benchmark indices, OHLCV+volume)
  6. convert to qlib bin (incremental → fallback fix)
  7. refresh csi300 + csi800 instrument files
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
    index_dir = cfg.project_root / "data" / "raw" / "index"
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


def _do_raw_fetch(collector: TushareCollector, codes: list[str], target_dt: str) -> dict:
    """
    Fetch raw data for target date for the given codes.
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
            start_date=target_dt,
            end_date=target_dt,
            incremental=False,
            batch_size=200,
            include_moneyflow=True,
            include_margin=True,
        )
        elapsed = time.time() - t0
        return {"status": "success", "codes_fetched": len(codes), "elapsed_s": round(elapsed, 1)}
    except Exception as e:
        elapsed = time.time() - t0
        log.error(f"Raw fetch failed: {e}")
        return {"status": "failed", "codes_fetched": 0, "elapsed_s": round(elapsed, 1), "error": str(e)}


def _readiness_check(adapter: QlibAdapter, target_dt: str, min_active: int = 750) -> DataHealthReport:
    """Run comprehensive readiness checks after sync, using the unified health system.

    Returns a ``DataHealthReport`` with separate *blocking* and *warnings* lists.
    """
    from qsys.data.health import inspect_qlib_data_health

    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:]}"

    report = inspect_qlib_data_health(
        target_date,
        feature_fields=["$open", "$high", "$low", "$close", "$volume", "$factor"],
        universe="csi800",
        min_active_instruments=min_active,
    )
    return report


def _write_audit(audit_dir: Path, report: dict):
    """Write per-day audit record as JSON."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    date_str = report.get("target_date", "unknown")
    path = audit_dir / f"sync_csi800_{date_str}.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    log.info(f"Audit: {path}")
    return path


def _load_last_audit(audit_dir: Path) -> dict | None:
    """Load the latest audit record, for incremental skip detection."""
    if not audit_dir.exists():
        return None
    records = sorted(audit_dir.glob("sync_csi800_*.json"))
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
        f"Qsys CSI800 Daily Sync — {target_date}",
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
    parser = argparse.ArgumentParser(description="csi800 daily incremental data sync")
    parser.add_argument("--target-date", default=None, help="Target trade date (YYYY-MM-DD or YYYYMMDD)")
    parser.add_argument("--no-qlib-convert", action="store_true", help="Skip qlib conversion after raw fetch")
    parser.add_argument("--apply", action="store_true", help="Apply data changes (default is dry-run)")
    parser.add_argument("--force-fetch", action="store_true", help="Skip pre-check, force fetch all stocks")
    args = parser.parse_args()

    # Resolve target date
    target_dt = _resolve_target_date(args.target_date)
    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:]}"
    do_apply = args.apply
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    log.info("=" * 60)
    log.info(f"CSI800 Daily Sync — target={target_date}, apply={do_apply}, run_id={run_id}")
    log.info("=" * 60)

    report = {
        "run_id": run_id,
        "target_date": target_dt,
        "target_date_display": target_date,
        "applied": do_apply,
        "started_at": datetime.now().isoformat(),
        "steps": {},
        "overall_status": "unknown",
    }

    # Step 0: Initialize Qlib
    t0 = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()
    report["steps"]["init_qlib"] = {"elapsed_s": round(time.time() - t0, 1)}

    # Step 1: Get CSI800 constituents
    t0 = time.time()
    collector = TushareCollector()
    store = StockDataStore()
    codes = collector.get_universe("csi800")
    step1 = {"constituent_count": len(codes), "elapsed_s": round(time.time() - t0, 1)}
    report["steps"]["get_universe"] = step1
    log.info(f"CSI800 constituents: {len(codes)}")

    if not codes:
        log.error("Empty csi800 universe, aborting.")
        report["overall_status"] = "failed"
        report["ended_at"] = datetime.now().isoformat()
        _write_audit(Path("data/audit"), report)
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
        if not status_check["missing"]:
            log.info("All stocks up to date, skipping raw fetch.")
        else:
            raw_summary = _do_raw_fetch(collector, status_check["missing"], target_dt)
        report["steps"]["raw_fetch"] = raw_summary
    else:
        if status_check["need_fetch"] > 0:
            log.info(f"DRY RUN — would fetch {status_check['need_fetch']} stocks for {target_dt}")
            raw_summary = {"dry_run": True, "would_fetch": status_check["need_fetch"], "elapsed_s": round(time.time() - t0, 1)}
        report["steps"]["raw_fetch"] = raw_summary

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
        since = target_date
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

    # Step 5: Refresh instrument files
    t0 = time.time()
    if do_apply:
        adapter._refresh_universe_instruments(universe="csi800")
        adapter._refresh_universe_instruments(universe="csi300")
        report["steps"]["refresh_instruments"] = {"status": "done", "elapsed_s": round(time.time() - t0, 1)}
    else:
        report["steps"]["refresh_instruments"] = {"status": "dry_run"}

    # Step 6: Readiness check
    t0 = time.time()
    readiness_report = _readiness_check(adapter, target_dt, min_active=750)
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
        _write_audit(Path("data/audit"), report)

    # Step 7: Telegram notification (non-blocking, apply only)
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
