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
        "--rebuild-pit-universes-v2",
        action="store_true",
        help=(
            "Rebuild corrected csi800/csi1800 PIT v2 artifacts from the "
            "existing immutable v1 raw snapshots, then exit"
        ),
    )
    p.add_argument(
        "--overwrite-pit-universes",
        action="store_true",
        help="Replace existing PIT v2 targets transactionally",
    )
    p.add_argument(
        "--skip-margin-repair",
        action="store_true",
        help="Skip financial_rc margin-history coverage repair after csi800 sync",
    )
    p.add_argument(
        "--skip-shareholder-repair",
        action="store_true",
        help="Skip PIT shareholder sidecar catch-up and freshness validation",
    )
    p.add_argument(
        "--skip-universe-history-catchup",
        action="store_true",
        help="Skip feature-lookback backfill for newly added universe members",
    )
    p.add_argument(
        "--universe-history-lookback-days",
        type=int,
        default=1461,
        help="Canonical history required for long-window semantic features",
    )
    p.add_argument(
        "--shareholder-start-date",
        default=None,
        help="Force a bounded shareholder announcement-date backfill start",
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
    p.add_argument(
        "--margin-min-exchange-coverage",
        type=float,
        default=0.90,
        help=(
            "Minimum per-exchange share of CSI800 symbols with margin balance "
            "on every repaired open session"
        ),
    )
    p.add_argument(
        "--margin-lag-sessions",
        type=int,
        default=1,
        help=(
            "Margin publication lag in open sessions. Daily post-close sync "
            "defaults to repairing through the previous open session."
        ),
    )
    args = p.parse_args()
    if args.rebuild_pit_universes_v2:
        from qsys.research.pit_universe import rebuild_pit_universes_v2

        result = rebuild_pit_universes_v2(
            PROJ,
            overwrite=args.overwrite_pit_universes,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if args.margin_lag_sessions < 1:
        p.error("--margin-lag-sessions must be at least 1 for post-close sync")
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

    if not do_apply or universe != "csi800":
        return

    from qsys.data.adapter import QlibAdapter
    from qsys.ops.instrument_coverage import read_instrument_file
    from qsys.data.storage import StockDataStore
    from qsys.ops.raw_sync import (
        resolve_margin_availability_date,
        run_margin_history_repair,
    )
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
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {}
    if not args.skip_universe_history_catchup:
        from qsys.ops.universe_history import run_universe_history_catchup

        history_result = run_universe_history_catchup(
            project_root=PROJ,
            symbols=symbols,
            as_of_date=resolved_target,
            lookback_calendar_days=args.universe_history_lookback_days,
            output_dir=(
                PROJ / "runs" / "data_sync" / run_id / "universe_history"
            ),
            apply=True,
        )
        report["universe_history"] = history_result
        if history_result["status"] not in {"healthy", "success"}:
            raise RuntimeError(
                "csi800 universe feature-history catch-up failed: "
                f"{history_result['summary_path']}"
            )
    if not args.skip_margin_repair:
        store = StockDataStore()
        margin_asof_date = resolve_margin_availability_date(
            store,
            signal_date=resolved_target,
            lag_sessions=args.margin_lag_sessions,
        )
        margin_asof_ts = datetime.strptime(margin_asof_date, "%Y-%m-%d")
        repair_start = (
            margin_asof_ts - timedelta(days=max(args.margin_lookback_days, 90))
        ).strftime("%Y-%m-%d")
        margin_result = run_margin_history_repair(
            symbols=symbols,
            start_date=repair_start,
            end_date=margin_asof_date,
            min_active=args.margin_min_active,
            min_exchange_coverage=args.margin_min_exchange_coverage,
            apply=True,
            output_dir=PROJ / "runs" / "data_sync" / run_id / "margin_repair",
            store=store,
            signal_date=resolved_target,
            availability_lag_sessions=args.margin_lag_sessions,
        )
        report["margin_availability"] = {
            "signal_date": resolved_target,
            "as_of_date": margin_asof_date,
            "lag_sessions": args.margin_lag_sessions,
            "source": "tushare.margin_detail",
        }
        report["margin_repair"] = margin_result
        if margin_result["status"] not in {"healthy", "success"}:
            raise RuntimeError(
                "csi800 margin history repair failed: "
                f"{margin_result['summary_path']}"
            )

    if not args.skip_shareholder_repair:
        from qsys.common.config import load_strategy_config
        from qsys.feature.freshness import normalise_shareholder_freshness
        from qsys.ops.shareholder_sync import run_shareholder_history_repair

        financial_config = load_strategy_config("financial_rc", PROJ)
        freshness = normalise_shareholder_freshness(
            financial_config.get("feature_freshness", {}).get("shareholder")
        )
        shareholder_result = run_shareholder_history_repair(
            project_root=PROJ,
            symbols=symbols,
            end_date=resolved_target,
            contract=freshness,
            apply=True,
            output_dir=(
                PROJ / "runs" / "data_sync" / run_id / "shareholder_repair"
            ),
            start_date=args.shareholder_start_date,
        )
        report["shareholder_repair"] = shareholder_result
        if shareholder_result["status"] not in {"healthy", "success"}:
            raise RuntimeError(
                "csi800 shareholder history repair failed: "
                f"{shareholder_result['summary_path']}"
            )

    print(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
        )
    )

if __name__ == "__main__":
    main()
