#!/usr/bin/env python3
"""
csi800 daily incremental data sync — 每日数据闭环.

Flow:
  1. resolve target trade date
  2. get latest csi800 constituents (via index_weight)
  3. pre-check: skip fetch if all stocks already have target date
  4. batch fetch raw data for missing stocks (single-pass)
  5. convert to qlib bin (incremental → fallback fix)
  6. refresh csi300 + csi800 instrument files
  7. comprehensive readiness check
  8. write structured audit record → data/audit/

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
                # 如果 calendar 中最晚日期明显早于 today（跨年情况），
                # 用 today 本身，让 pre_check 根据实际数据决定是否拉取
                if latest < today_str[:4]:
                    return today_str
                return latest
    except Exception as e:
        log.warning(f"Failed to resolve target date via calendar: {e}")

    # Fallback: today — pre_check will decide whether data needs fetching
    return today_str


def _check_stock_data_status(store: StockDataStore, codes: list[str], target_dt: str) -> dict:
    """
    Per-stock latest date check — only read trade_date column.
    Returns: { 'have': [codes with target date], 'missing': [codes without] }
    """
    have = []
    missing = []
    for code in codes:
        df = store.load_daily(code)
        if df is not None and not df.empty:
            latest = str(df["trade_date"].max())
            if latest >= target_dt:
                have.append(code)
                continue
        missing.append(code)
    return {"have": have, "missing": missing, "total": len(codes), "already_up_to_date": len(have), "need_fetch": len(missing)}


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


def _readiness_check(adapter: QlibAdapter, target_dt: str, min_active: int = 750) -> dict:
    """Run comprehensive readiness checks after sync."""
    from qlib.data import D

    checks: dict = {}
    all_passed = True

    target_date = f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:]}"

    # 1. Raw latest >= target_date
    try:
        raw_latest = StockDataStore().get_global_latest_date()
        raw_ok = raw_latest is not None and raw_latest >= target_dt
        checks["raw_latest"] = {"value": raw_latest, "target": target_dt, "passed": raw_ok}
        if not raw_ok:
            all_passed = False
    except Exception as e:
        checks["raw_latest"] = {"error": str(e), "passed": False}
        all_passed = False

    # 2. Qlib calendar latest >= target_date
    try:
        qlib_last = adapter.get_last_qlib_date()
        qlib_last_str = qlib_last.strftime("%Y-%m-%d") if qlib_last is not None else None
        qlib_ok = qlib_last_str is not None and qlib_last_str >= target_date
        checks["qlib_calendar"] = {"value": qlib_last_str, "target": target_date, "passed": qlib_ok}
        if not qlib_ok:
            all_passed = False
    except Exception as e:
        checks["qlib_calendar"] = {"error": str(e), "passed": False}
        all_passed = False

    # 3. all.txt end_date >= target_date
    try:
        all_end = adapter.get_instrument_latest_end_date("all")
        all_end_str = all_end.strftime("%Y-%m-%d") if all_end is not None else None
        all_ok = all_end_str is not None and all_end_str >= target_date
        checks["all_instruments"] = {"end_date": all_end_str, "target": target_date, "passed": all_ok}
        if not all_ok:
            all_passed = False
    except Exception as e:
        checks["all_instruments"] = {"error": str(e), "passed": False}
        all_passed = False

    # 4. csi800.txt end_date >= target_date
    try:
        csi800_end = adapter.get_instrument_latest_end_date("csi800")
        csi800_end_str = csi800_end.strftime("%Y-%m-%d") if csi800_end is not None else None
        csi800_ok = csi800_end_str is not None and csi800_end_str >= target_date
        checks["csi800_instruments"] = {"end_date": csi800_end_str, "target": target_date, "passed": csi800_ok}
        if not csi800_ok:
            all_passed = False
    except Exception as e:
        checks["csi800_instruments"] = {"error": str(e), "passed": False}
        all_passed = False

    # 5. Active instrument count on target_date
    try:
        inst_obj = D.instruments("csi800")
        cal = D.calendar(start_time=target_date, end_time=target_date)
        if len(cal) > 0:
            active = D.list_instruments(inst_obj, start_time=target_date, end_time=target_date)
            active_count = len(active)
        else:
            active_count = 0
        count_ok = active_count >= min_active
        checks["active_instruments"] = {"count": active_count, "min_required": min_active, "passed": count_ok}
        if not count_ok:
            all_passed = False
    except Exception as e:
        checks["active_instruments"] = {"error": str(e), "passed": False}
        all_passed = False

    # 6. Core field null rates
    core_fields = ["$open", "$high", "$low", "$close", "$volume", "$factor"]
    try:
        field_checks = {}
        for field in core_fields:
            data = adapter.get_features("csi800", [field], start_time=target_date, end_time=target_date)
            if data is not None and not data.empty:
                null_pct = float(data.isnull().sum().iloc[0] / len(data))
            else:
                null_pct = 1.0
            name = field.replace("$", "")
            field_ok = null_pct < 0.05
            field_checks[name] = {"null_pct": round(null_pct, 4), "passed": field_ok}
            if not field_ok:
                all_passed = False
        checks["field_null_rates"] = field_checks
    except Exception as e:
        checks["field_null_rates"] = {"error": str(e), "passed": False}
        all_passed = False

    checks["_summary"] = {"passed": all_passed}
    return checks


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

    # Count readiness checks excluding _summary
    readiness_detail = report.get("readiness", {})
    data_checks_passed = sum(
        1 for k, v in readiness_detail.items()
        if k != "_summary" and k != "field_null_rates"
        and isinstance(v, dict) and v.get("passed") is True
    )
    data_checks_total = sum(
        1 for k, v in readiness_detail.items()
        if k != "_summary" and k != "field_null_rates"
        and isinstance(v, dict) and "passed" in v
    )
    # Count core field checks inside field_null_rates
    field_rates = readiness_detail.get("field_null_rates", {})
    if isinstance(field_rates, dict) and "passed" not in field_rates:
        field_passed = sum(1 for v in field_rates.values()
                           if isinstance(v, dict) and v.get("passed") is True)
        field_total = sum(1 for v in field_rates.values()
                          if isinstance(v, dict) and "passed" in v)
    else:
        field_passed = field_total = 0

    lines = [
        f"Qsys CSI800 Daily Sync — {target_date}",
        f"Status: {status}",
        f"Constituents: {constituent_count} | Up-to-date: {up_to_date} | Fetched: {fetched}",
    ]
    if isinstance(qlib_elapsed, (int, float)):
        qlib_mode = qlib_convert.get("mode", "?")
        lines.append(f"Qlib convert ({qlib_mode}): {qlib_elapsed}s")
    if data_checks_total > 0:
        lines.append(f"Data checks: {data_checks_passed}/{data_checks_total} passed")
    if field_total > 0:
        lines.append(f"Core fields: {field_passed}/{field_total} passed")

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

    # Step 4: Qlib convert
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
    readiness = _readiness_check(adapter, target_dt)
    readiness_elapsed = round(time.time() - t0, 1)
    overall = "ready" if readiness["_summary"]["passed"] else "degraded"
    report["steps"]["readiness_check"] = {"elapsed_s": readiness_elapsed}
    report["readiness"] = readiness
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

    # Exit code for systemd
    if overall != "ready":
        sys.exit(2)


if __name__ == "__main__":
    main()
