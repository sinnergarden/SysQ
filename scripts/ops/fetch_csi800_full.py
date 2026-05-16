#!/usr/bin/env python3
"""
csi800 全量拉取脚本 — 从零拉取所有数据，含两融 + 龙虎榜.

Usage:
  python scripts/ops/fetch_csi800_full.py
  python scripts/ops/fetch_csi800_full.py --batch-size 200
  python scripts/ops/fetch_csi800_full.py --skip-dragon-tiger --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.collector import TushareCollector
from qsys.data.adapter import QlibAdapter
from qsys.utils.logger import log


def _fetch_dragon_tiger(collector, start_date: str, end_date: str, rate_limit_s: float = 0.35):
    """Fetch dragon-tiger (top_inst + top_list) for all trading days in range, store to SQLite."""
    cal = collector.store.get_calendar()
    if cal is None or cal.empty:
        log.warning("Trading calendar not available, skipping dragon-tiger fetch")
        return {"status": "skipped", "reason": "no calendar"}

    cal_open = cal[(cal["is_open"] == 1) & (cal["cal_date"] >= start_date) & (cal["cal_date"] <= end_date)]
    trade_dates = sorted(cal_open["cal_date"].astype(str).tolist())
    log.info(f"Dragon-tiger: {len(trade_dates)} trading days to fetch")

    total_inst = 0
    total_list = 0

    for i, trade_date in enumerate(trade_dates):
        df_inst = collector.get_top_inst(trade_date)
        if df_inst is not None and not df_inst.empty:
            collector.store.save_top_inst(df_inst, trade_date)
            total_inst += len(df_inst)
        time.sleep(rate_limit_s)

        df_list = collector.get_top_list(trade_date)
        if df_list is not None and not df_list.empty:
            collector.store.save_top_list(df_list, trade_date)
            total_list += len(df_list)
        time.sleep(rate_limit_s)

        if (i + 1) % 200 == 0:
            log.info(f"Dragon-tiger progress: {i+1}/{len(trade_dates)} days, inst={total_inst}, list={total_list}")

    log.info(f"Dragon-tiger done: {total_inst} top_inst, {total_list} top_list records")
    return {"status": "done", "trade_dates": len(trade_dates), "top_inst_records": total_inst, "top_list_records": total_list}


def main() -> None:
    parser = argparse.ArgumentParser(description="csi800 full fetch and qlib rebuild")
    parser.add_argument("--start-date", default="20100101")
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--batch-size", type=int, default=200, help="Stocks per Tushare batch (default 200)")
    parser.add_argument("--skip-margin", action="store_true", help="Skip margin (两融) data")
    parser.add_argument("--skip-moneyflow", action="store_true", help="Skip moneyflow data")
    parser.add_argument("--skip-dragon-tiger", action="store_true", help="Skip dragon-tiger (龙虎榜) data")
    parser.add_argument("--skip-qlib", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    end_date = args.end_date or datetime.now().strftime("%Y%m%d")
    log.info(f"CSI800 full fetch: {args.start_date} → {end_date}")
    log.info(f"Config: batch_size={args.batch_size}, margin={not args.skip_margin}, "
             f"moneyflow={not args.skip_moneyflow}, dragon_tiger={not args.skip_dragon_tiger}")

    collector = TushareCollector()
    codes = collector.get_universe("csi800")
    log.info(f"CSI800 constituents: {len(codes)} stocks")

    if args.dry_run:
        from math import ceil
        n_batches = max(1, ceil(len(codes) / args.batch_size))
        log.info(f"DRY RUN — would fetch in {n_batches} batch(es) of ≤{args.batch_size}")
        return

    log.info("=" * 60)
    log.info("STEP 1/4: Raw data fetch (daily, financials, margin, moneyflow...)")
    log.info("=" * 60)

    t_start = datetime.now()

    collector.update_universe_history(
        universe="csi800",
        start_date=args.start_date,
        end_date=end_date,
        incremental=False,
        batch_size=args.batch_size,
        include_moneyflow=not args.skip_moneyflow,
        include_margin=not args.skip_margin,
    )
    raw_elapsed = (datetime.now() - t_start).total_seconds()
    log.info(f"Step 1 done in {raw_elapsed:.1f}s")

    # Step 2: Dragon-tiger
    dt_summary: dict = {"status": "skipped"}
    if not args.skip_dragon_tiger:
        log.info("=" * 60)
        log.info("STEP 2/4: Dragon-tiger (龙虎榜) data fetch")
        log.info("=" * 60)
        t0 = datetime.now()
        dt_summary = _fetch_dragon_tiger(collector, args.start_date, end_date)
        dt_elapsed = (datetime.now() - t0).total_seconds()
        log.info(f"Step 2 done in {dt_elapsed:.1f}s")

    # Step 3: Qlib rebuild
    qlib_summary: dict = {"mode": "skipped"}
    if not args.skip_qlib:
        log.info("=" * 60)
        log.info("STEP 3/4: Full Qlib rebuild")
        log.info("=" * 60)
        adapter = QlibAdapter()
        adapter.init_qlib()
        t0 = datetime.now()
        adapter.convert_all()
        rebuild_elapsed = (datetime.now() - t0).total_seconds()
        qlib_summary = {"mode": "full_rebuild", "elapsed_s": round(rebuild_elapsed, 1)}
        log.info(f"Step 3 done in {rebuild_elapsed:.1f}s")

        log.info("=" * 60)
        log.info("STEP 4/4: Instrument refresh")
        log.info("=" * 60)
        adapter._refresh_universe_instruments(universe="csi800")
        adapter._refresh_universe_instruments(universe="csi300")

    total_elapsed = (datetime.now() - t_start).total_seconds()

    report = {
        "status": "done",
        "total_stocks": len(codes),
        "raw_fetch_seconds": round(raw_elapsed, 1),
        "dragon_tiger": dt_summary,
        "qlib_rebuild": qlib_summary,
        "total_seconds": round(total_elapsed, 1),
    }
    print(json.dumps(report, indent=2))
    log.info(f"Total: {total_elapsed:.1f}s")


if __name__ == "__main__":
    main()
