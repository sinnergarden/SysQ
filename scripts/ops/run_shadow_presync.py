#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops.shadow_presync import run_shadow_presync


def main() -> None:
    parser = argparse.ArgumentParser(description="Run targeted CSI300 shadow pre-sync")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Base directory for runs/ and data/")
    parser.add_argument("--run-id", default=None, help="Optional run_id override")
    parser.add_argument("--universe", default="csi300", help="Universe to pre-sync; default csi300")
    parser.add_argument("--target-date", default=None, help="Requested trade date (YYYY-MM-DD)")
    parser.add_argument("--lookback-days", type=int, default=20, help="Lookback window for targeted raw update planning")
    parser.add_argument("--triggered-by", default="manual", help="Trigger source label")
    parser.add_argument("--max-symbols", type=int, default=None, help="Only process the first N selected symbols")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol allowlist")
    parser.add_argument("--symbols-file", default=None, help="Path to newline-delimited symbol allowlist")
    parser.add_argument("--raw-only", action="store_true", help="Run only raw update planning/apply")
    parser.add_argument("--qlib-only", action="store_true", help="Skip raw update and only stage qlib sync/audit")
    parser.add_argument("--skip-qlib-sync", action="store_true", help="Skip qlib sync even if raw update succeeded")
    parser.add_argument("--skip-instrument-repair", action="store_true", help="Skip safe instrument repair step")
    parser.add_argument("--resume", action="store_true", help="Skip symbols that succeeded in the previous presync raw plan")
    parser.add_argument("--dry-run", dest="apply", action="store_false", default=False, help="Plan only, do not mutate raw/qlib")
    parser.add_argument("--apply", dest="apply", action="store_true", help="Apply targeted raw update and try incremental qlib refresh")
    args = parser.parse_args()

    result = run_shadow_presync(
        args.base_dir,
        run_id=args.run_id,
        universe=args.universe,
        target_date=args.target_date,
        lookback_days=args.lookback_days,
        apply=args.apply,
        triggered_by=args.triggered_by,
        max_symbols=args.max_symbols,
        symbols=[item.strip() for item in str(args.symbols or "").split(",") if item.strip()] or None,
        symbols_file=args.symbols_file,
        raw_only=args.raw_only,
        qlib_only=args.qlib_only,
        skip_qlib_sync=args.skip_qlib_sync,
        skip_instrument_repair=args.skip_instrument_repair,
        resume=args.resume,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
