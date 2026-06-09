#!/usr/bin/env python3
"""One-time backfill: pull full index OHLCV from Tushare (2010 → now).

Usage
-----
  # dry-run (show what would be fetched per index)
  python scripts/ops/backfill_index_daily.py

  # apply — write/overwrite CSV files
  python scripts/ops/backfill_index_daily.py --apply

Output: ``data/raw/index/<ts_code>.csv`` — one CSV per index, full history.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.utils.logger import log

INDEX_CODES = [
    "000001.SH",  # 上证综指
    "000300.SH",  # 沪深300
    "000905.SH",  # 中证500
    "000852.SH",  # 中证1000
    "000906.SH",  # 中证800
    "000688.SH",  # 科创50
    "399006.SZ",  # 创业板指
]

BACKFILL_START = "20100101"


def _now_text() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill index daily data from 2010")
    parser.add_argument("--apply", action="store_true", help="Write CSV files")
    parser.add_argument(
        "--start-date", default=BACKFILL_START,
        help="Backfill start date (YYYYMMDD, default 20100101)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch even when CSV already exists",
    )
    args = parser.parse_args()

    output_dir = cfg.project_root / "data" / "raw" / "index"
    output_dir.mkdir(parents=True, exist_ok=True)

    collector = TushareCollector()
    end_dt = datetime.now().strftime("%Y%m%d")

    summary = {
        "started_at": _now_text(),
        "start_date": args.start_date,
        "end_date": end_dt,
        "applied": args.apply,
        "indices": {},
    }

    for code in INDEX_CODES:
        start = args.start_date
        out_path = output_dir / f"{code}.csv"

        if out_path.exists() and not args.force:
            # Show existing date range
            import pandas as pd
            existing = pd.read_csv(out_path)
            if not existing.empty:
                existing_dates = existing["trade_date"].tolist()
                log.info(
                    "%s: already exists — %s ~ %s (%d rows)",
                    code, existing_dates[0], existing_dates[-1], len(existing),
                )
                if args.apply:
                    log.info("%s: skipping (exists, use --force to re-fetch)", code)
                    summary["indices"][code] = {"status": "skipped", "reason": "already_exists"}
                    continue
            else:
                log.info("%s: exists but empty, will re-fetch", code)
        elif out_path.exists() and args.force:
            log.info("%s: --force, will re-fetch", code)

        log.info("%s: fetching %s → %s ...", code, start, end_dt)
        try:
            df = collector.get_index_daily(code, start_date=start, end_date=end_dt)
        except Exception as e:
            log.error("%s: fetch failed: %s", code, e)
            summary["indices"][code] = {"status": "failed", "error": str(e)}
            continue

        if df is None or df.empty:
            log.warning("%s: no data returned", code)
            summary["indices"][code] = {"status": "empty"}
            continue

        # ts_code is uniform — drop for cleaner CSV (file name tells us)
        if "ts_code" in df.columns and df["ts_code"].nunique() == 1:
            df = df.drop(columns=["ts_code"])

        df = df.sort_values("trade_date").reset_index(drop=True)

        if args.apply:
            df.to_csv(out_path, index=False)
            log.info("%s: wrote %d rows → %s", code, len(df), out_path)
            summary["indices"][code] = {
                "status": "success",
                "rows": len(df),
                "date_range": f"{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}",
            }
        else:
            date_range = f"{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}"
            log.info("%s: DRY-RUN — would write %d rows (%s)", code, len(df), date_range)
            summary["indices"][code] = {
                "status": "dry_run",
                "rows": len(df),
                "date_range": date_range,
            }

    summary["ended_at"] = _now_text()
    import json
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
