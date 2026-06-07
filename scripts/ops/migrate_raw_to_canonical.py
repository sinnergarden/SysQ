#!/usr/bin/env python3
"""
Migrate data/raw/daily -> data/canonical/daily.

This script:
1. Verifies source directory exists and has feather files.
2. Creates target directory.
3. Moves files (atomic on same filesystem).
4. Leaves a marker file in data/raw/ so future readers know it's migrated.

Usage:
    python scripts/ops/migrate_raw_to_canonical.py     # dry-run
    python scripts/ops/migrate_raw_to_canonical.py --apply   # real run
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate data/raw/daily -> data/canonical/daily")
    parser.add_argument("--apply", action="store_true", help="Apply the migration (default is dry-run)")
    args = parser.parse_args()

    src = PROJECT_ROOT / "data" / "raw" / "daily"
    dst = PROJECT_ROOT / "data" / "canonical" / "daily"
    marker = PROJECT_ROOT / "data" / "raw" / "MIGRATED_TO_CANONICAL.md"

    if not src.exists():
        print(f"✅ Source {src} does not exist — nothing to migrate.")
        return

    feather_files = list(src.glob("*.feather"))
    print(f"Source: {src} ({len(feather_files)} feather files)")

    if not feather_files:
        print(f"⚠ No feather files in {src}, nothing to migrate.")
        return

    if dst.exists() and list(dst.glob("*.feather")):
        print(f"⚠ Destination {dst} already has feather files.")
        print("  Refusing to migrate — would risk overwrite.")
        print("  Remove or empty dst first if you want to re-run the migration.")
        sys.exit(1)

    total_size = sum(f.stat().st_size for f in feather_files)
    print(f"Total size: {total_size / 1024 / 1024:.1f} MB")

    if not args.apply:
        print(f"\n🔶 DRY-RUN. Would execute:")
        print(f"  1. Create {dst}")
        print(f"  2. Move {len(feather_files)} files from {src} to {dst}")
        print(f"  3. Write marker {marker}")
        print(f"  Run with --apply to execute.")
        return

    # Real run
    dst.mkdir(parents=True, exist_ok=True)

    moved = 0
    for f in feather_files:
        target = dst / f.name
        f.rename(target)
        moved += 1

    # Write migration marker
    dst.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "data/raw/daily has been migrated to data/canonical/daily.\n"
        "The canonical directory is the SOT for daily feather data.\n"
        "See docs/REPO_LAYOUT.md for the canonical data layout.\n"
        "Remove this marker and the data/raw/daily directory once\n"
        "all code references have been updated to use canonical_dir.\n"
    )

    print(f"✅ Migrated {moved}/{len(feather_files)} feather files to {dst}")
    print(f"   Marker written: {marker}")

    # Verify
    dst_files = list(dst.glob("*.feather"))
    src_after = list(src.glob("*.feather"))
    print(f"   Verification: dst={len(dst_files)} files, src_after={len(src_after)} files")

    if len(dst_files) == moved and len(src_after) == 0:
        print("✅ Migration verified — all files moved, source empty.")
    else:
        print(f"⚠ Incomplete: dst={len(dst_files)}, src_after={len(src_after)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
