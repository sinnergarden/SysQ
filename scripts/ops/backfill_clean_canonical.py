#!/usr/bin/env python3
"""
One-time backfill: apply qsys.data.cleaner to all existing canonical feather files.

This closes the loop on the historical bug where close_x/close_y columns
were persisted into feather by _save_batch_results after merges.

Safe to run multiple times (idempotent).
"""

import warnings
warnings.warn(
    "DEPRECATED: backfill_clean_canonical.py is superseded by UC-standard entrypoints. Scheduled for removal.",
    DeprecationWarning, stacklevel=2,
)

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.config import cfg
from qsys.data.cleaner import DIRTY_SUFFIX_RE, coalesce_merge_suffix_columns, has_dirty_columns


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill-clean canonical feather files")
    parser.add_argument("--apply", action="store_true", help="Apply the cleaning (default is dry-run)")
    args = parser.parse_args()

    canonical = cfg.get_path("canonical_dir")
    if canonical is None or not canonical.exists():
        print(f"❌ Canonical dir not found: {canonical}")
        sys.exit(1)

    files = sorted(canonical.glob("*.feather"))
    cleaned = 0
    errors = 0

    for f in files:
        try:
            df = pd.read_feather(f)
            dirty = [c for c in df.columns if bool(DIRTY_SUFFIX_RE.search(c))]
            if not dirty:
                continue

            clean = coalesce_merge_suffix_columns(df)
            if args.apply:
                clean.to_feather(f)
            cleaned += 1
            if cleaned <= 5:
                print(f"  {f.name}: {dirty}")
        except Exception as e:
            print(f"  ❌ {f.name}: {e}")
            errors += 1

    print(f"\n{'✅ APPLIED' if args.apply else '🔶 DRY-RUN'}: {cleaned}/{len(files)} files {'cleaned' if args.apply else 'need cleaning'}")
    if errors:
        print(f"  ⚠ {errors} errors")
    if not args.apply and cleaned > 0:
        print(f"  Run with --apply to clean.")


if __name__ == "__main__":
    main()
