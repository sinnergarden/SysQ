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
    parser.add_argument("--symbols-file", default=None, help="File with one symbol per line; restricts backfill to exactly these symbols (e.g. a PIT universe registry)")
    parser.add_argument("--full-backfill", action="store_true", help="Fetch every symbol from missing-start-date and write non-incrementally (for stub/placeholder raw that lacks genuine history)")
    parser.add_argument("--missing-start-date", default="2010-01-01", help="Backfill start date for symbols missing raw files")
    parser.add_argument("--stale-lookback-days", type=int, default=20, help="Backfill lookback for stale raw symbols")
    parser.add_argument("--triggered-by", default="manual", help="Trigger source label")
    parser.add_argument("--skip-qlib-refresh", action="store_true", help="Skip qlib selected-symbol refresh after raw batches")
    parser.add_argument("--skip-basic", action="store_true", help="Skip daily_basic fetch (pe/pb/market-cap fields)")
    parser.add_argument("--skip-limit", action="store_true", help="Skip stk_limit fetch (high_limit/low_limit)")
    parser.add_argument("--skip-moneyflow", action="store_true", help="Skip moneyflow fetch (buy/sell amounts, net inflow)")
    parser.add_argument("--skip-margin", action="store_true", help="Skip margin fetch (margin_balance / rzye / rqye)")
    parser.add_argument("--refresh-only", action="store_true", help="Only rebuild qlib bins for --symbols-file symbols; skip raw fetch")
    parser.add_argument("--dry-run", dest="apply", action="store_false", default=False, help="Plan only, do not mutate raw/qlib")
    parser.add_argument("--apply", dest="apply", action="store_true", help="Apply raw backfill batches and qlib repair")
    args = parser.parse_args()

    symbols = None
    if args.symbols_file:
        symbols = [
            line.strip()
            for line in Path(args.symbols_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(f"Restricting backfill to {len(symbols)} symbols from {args.symbols_file}")

    if args.refresh_only:
        from qsys.ops.qlib_sync import refresh_selected_symbols_from_raw
        if not symbols:
            raise SystemExit("--refresh-only requires --symbols-file")
        target = args.target_date or "2026-08-21"
        out_dir = Path(args.base_dir) / "runs" / "full_universe_backfill" / "refresh_only"
        result = refresh_selected_symbols_from_raw(
            Path(args.base_dir), symbols,
            target_date=target, apply=args.apply, output_dir=out_dir,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return

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
        symbols=symbols,
        full_backfill=args.full_backfill,
        include_basic=not args.skip_basic,
        include_limit=not args.skip_limit,
        include_moneyflow=not args.skip_moneyflow,
        include_margin=not args.skip_margin,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
