#!/usr/bin/env python3
"""Data Sync CLI — UC-1.

Usage:
    python scripts/data_sync.py --config configs/data/csi800_daily_sync.yaml
    python scripts/data_sync.py --universe csi800 --target-date 2026-06-12
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PROJ = Path(__file__).resolve().parent.parent

def main():
    p = argparse.ArgumentParser(description="Data Sync — UC-1")
    p.add_argument("--config", default=None)
    p.add_argument("--universe", default=None)
    p.add_argument("--target-date", default=None)
    p.add_argument("--apply", action="store_true")
    p.add_argument(
        "--skip-margin-repair",
        action="store_true",
        help="Skip financial_rc margin-history coverage repair after csi800 sync",
    )
    p.add_argument(
        "--margin-lookback-days",
        type=int,
        default=120,
        help="Calendar-day history required for 60-session margin features",
    )
    p.add_argument(
        "--margin-min-active",
        type=int,
        default=450,
        help="Minimum CSI800 symbols with margin balance on every open session",
    )
    args = p.parse_args()
    universe = args.universe
    target_date = args.target_date
    do_apply = args.apply
    if args.config:
        import yaml
        c = yaml.safe_load(Path(args.config).read_text())
        target_date = str(c.get("date_range", {}).get("end_date", "")) or datetime.now().strftime("%Y-%m-%d")
        configured_universe = c.get("universe", universe)
        if isinstance(configured_universe, dict):
            universe = configured_universe.get("name", universe)
        else:
            universe = configured_universe
        universe = universe or "csi800"
        do_apply = c.get("execution", {}).get("apply", False) or do_apply
        if c.get("tasks", {}).get("qlib_bin", True):
            cmd = [sys.executable, str(PROJ / "scripts/ops/sync_csi800_daily.py"), "--target-date", target_date]
            if do_apply: cmd.append("--apply")
            subprocess.run(cmd, cwd=str(PROJ), check=True)
    elif universe == "csi800":
        target_date = target_date or datetime.now().strftime("%Y-%m-%d")
        cmd = [sys.executable, str(PROJ / "scripts/ops/sync_csi800_daily.py"), "--target-date", target_date]
        if args.apply: cmd.append("--apply")
        subprocess.run(cmd, cwd=str(PROJ), check=True)
    else:
        print("Specify --config or --universe csi800", file=sys.stderr); sys.exit(1)

    if not do_apply or universe != "csi800" or args.skip_margin_repair:
        return

    from qsys.data.adapter import QlibAdapter
    from qsys.ops.instrument_coverage import read_instrument_file
    from qsys.ops.raw_sync import run_margin_history_repair
    from qsys.ops.trade_date import resolve_daily_trade_date

    resolved = resolve_daily_trade_date(target_date, universe="csi800")
    resolved_target = resolved.get("resolved_trade_date")
    if not resolved_target:
        raise RuntimeError(f"cannot resolve synced csi800 target date: {resolved}")
    adapter = QlibAdapter()
    instruments = read_instrument_file(
        adapter.qlib_dir / "instruments" / "csi800.txt"
    )
    target_ts = datetime.strptime(resolved_target, "%Y-%m-%d")
    active = instruments[
        (instruments["start_date"] <= target_ts)
        & (instruments["end_date"] >= target_ts)
    ]
    symbols = sorted(active["instrument"].astype(str).unique().tolist())
    repair_start = (
        target_ts - timedelta(days=max(args.margin_lookback_days, 90))
    ).strftime("%Y-%m-%d")
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    result = run_margin_history_repair(
        symbols=symbols,
        start_date=repair_start,
        end_date=resolved_target,
        min_active=args.margin_min_active,
        apply=True,
        output_dir=PROJ / "runs" / "data_sync" / run_id / "margin_repair",
    )
    print(json.dumps({"margin_repair": result}, indent=2, sort_keys=True))
    if result["status"] not in {"healthy", "success"}:
        raise RuntimeError(
            f"csi800 margin history repair failed: {result['summary_path']}"
        )

if __name__ == "__main__":
    main()
