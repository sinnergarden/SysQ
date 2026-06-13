#!/usr/bin/env python3
"""Data Sync CLI — UC-1.

Unified entrypoint for syncing raw data, indices, constituents
and rebuilding Qlib binary data.  Delegates to existing sync
functions (sync_csi800_daily.py, run_update.py, etc.).

Usage::

    python scripts/data_sync.py --config configs/data/csi800_daily_sync.yaml
    python scripts/data_sync.py --universe csi800 --target-date 2026-06-12 --apply
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _sync_csi800(target_date: str, apply: bool, force_fetch: bool) -> None:
    """Delegate to existing sync_csi800_daily.py."""
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "ops" / "sync_csi800_daily.py"),
        "--target-date", target_date,
    ]
    if apply:
        cmd.append("--apply")
    if force_fetch:
        cmd.append("--force-fetch")
    print(f"[data_sync] {' '.join(str(p) for p in cmd)}")
    r = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if r.returncode != 0:
        raise RuntimeError(f"sync_csi800_daily failed (exit {r.returncode})")


def _update_instruments(universe: str) -> None:
    """Delegate to create_instrument_universe.py."""
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "create_instrument_universe.py"),
        "--universe", universe,
    ]
    print(f"[data_sync] {' '.join(str(p) for p in cmd)}")
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def _run_full_update() -> None:
    """Delegate to run_update.py for full data update."""
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_update.py"), "--init"]
    print(f"[data_sync] {' '.join(str(p) for p in cmd)}")
    subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Data Sync — UC-1")
    parser.add_argument("--config", default=None,
                        help="Path to data sync YAML config")
    parser.add_argument("--universe", default=None,
                        help="Universe (csi300/csi800/all)")
    parser.add_argument("--target-date", default=None,
                        help="Target date (YYYY-MM-DD)")
    parser.add_argument("--apply", action="store_true",
                        help="Apply changes (default dry-run)")
    parser.add_argument("--force-fetch", action="store_true",
                        help="Force fetch all stocks")
    args = parser.parse_args()

    if args.config:
        import yaml
        config = yaml.safe_load(Path(args.config).read_text())
        universe = str(config.get("universe", "csi800"))
        target_date = str(config.get("date_range", {}).get("end_date", "")) or datetime.now().strftime("%Y-%m-%d")
        if config.get("tasks", {}).get("qlib_bin", True):
            _sync_csi800(target_date, apply=True, force_fetch=False)
        if config.get("tasks", {}).get("index_constituents", False):
            _update_instruments(universe)
        return

    if args.universe == "csi800":
        target = args.target_date or datetime.now().strftime("%Y-%m-%d")
        _sync_csi800(target, apply=args.apply, force_fetch=args.force_fetch)
    elif args.universe == "all":
        _run_full_update()
    else:
        print("Specify --config or --universe (csi800/all)", file=sys.stderr)
        sys.exit(1)

    print("Done.")


if __name__ == "__main__":
    main()
