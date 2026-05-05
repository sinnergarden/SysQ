#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops.full_universe_backfill import run_full_universe_backfill


def main() -> None:
    parser = argparse.ArgumentParser(description="Run resumable full-universe raw backfill and qlib repair")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Base directory for runs/ and data/")
    parser.add_argument("--target-date", default=None, help="Target trade date (YYYY-MM-DD); defaults to current qlib latest date")
    parser.add_argument("--batch-size", type=int, default=50, help="Symbols per collector batch")
    parser.add_argument("--max-batches", type=int, default=None, help="Only plan/apply the first N batches")
    parser.add_argument("--missing-start-date", default="2010-01-01", help="Backfill start date for symbols missing raw files")
    parser.add_argument("--stale-lookback-days", type=int, default=20, help="Backfill lookback for stale raw symbols")
    parser.add_argument("--triggered-by", default="manual", help="Trigger source label")
    parser.add_argument("--skip-qlib-refresh", action="store_true", help="Skip qlib selected-symbol refresh after raw batches")
    parser.add_argument("--dry-run", dest="apply", action="store_false", default=False, help="Plan only, do not mutate raw/qlib")
    parser.add_argument("--apply", dest="apply", action="store_true", help="Apply raw backfill batches and qlib repair")
    args = parser.parse_args()

    result = run_full_universe_backfill(
        args.base_dir,
        target_date=args.target_date,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        missing_start_date=args.missing_start_date,
        stale_lookback_days=args.stale_lookback_days,
        apply=args.apply,
        refresh_qlib=not args.skip_qlib_refresh,
        triggered_by=args.triggered_by,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
