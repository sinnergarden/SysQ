#!/usr/bin/env python3
"""Data Sync CLI — UC-1.

Usage:
    python scripts/data_sync.py --config configs/data/csi800_daily_sync.yaml
    python scripts/data_sync.py --universe csi800 --target-date 2026-06-12
"""
import argparse, subprocess, sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PROJ = Path(__file__).resolve().parent.parent

def main():
    p = argparse.ArgumentParser(description="Data Sync — UC-1")
    p.add_argument("--config", default=None); p.add_argument("--universe", default=None)
    p.add_argument("--target-date", default=None); p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.config:
        import yaml
        c = yaml.safe_load(Path(args.config).read_text())
        td = str(c.get("date_range", {}).get("end_date", "")) or datetime.now().strftime("%Y-%m-%d")
        do_apply = c.get("execution", {}).get("apply", False) or args.apply
        if c.get("tasks", {}).get("qlib_bin", True):
            cmd = [sys.executable, str(PROJ / "scripts/ops/sync_csi800_daily.py"), "--target-date", td]
            if do_apply: cmd.append("--apply")
            subprocess.run(cmd, cwd=str(PROJ), check=True)
        return
    if args.universe == "csi800":
        cmd = [sys.executable, str(PROJ / "scripts/ops/sync_csi800_daily.py"), "--target-date", args.target_date or datetime.now().strftime("%Y-%m-%d")]
        if args.apply: cmd.append("--apply")
        subprocess.run(cmd, cwd=str(PROJ), check=True)
    else:
        print("Specify --config or --universe csi800", file=sys.stderr); sys.exit(1)

if __name__ == "__main__":
    main()
