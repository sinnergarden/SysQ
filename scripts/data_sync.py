#!/usr/bin/env python3
"""Data Sync CLI — UC-1.

Usage:
    python scripts/data_sync.py --config configs/data/csi800_daily_sync.yaml
    python scripts/data_sync.py --universe csi800 --target-date 2026-06-12
    python scripts/data_sync.py --universe csi1800 --target-date 2026-08-21 --apply
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PROJ = Path(__file__).resolve().parent.parent


def _require_exact_sync_target(
    resolved: dict,
    *,
    requested_target: str,
    universe: str,
) -> str:
    requested = datetime.strptime(
        str(requested_target).replace("-", ""), "%Y%m%d"
    ).strftime("%Y-%m-%d")
    actual = resolved.get("resolved_trade_date")
    if not actual or actual != requested or resolved.get("status") != "success":
        raise RuntimeError(
            f"cannot resolve exact synced {universe} target date: {resolved}"
        )
    return actual


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
        default=None,
        help=(
            "Minimum symbols with margin balance per session; defaults to 450 "
            "for CSI800 and 1300 for CSI1800"
        ),
    )
    p.add_argument(
        "--margin-min-exchange-coverage",
        type=float,
        default=None,
        help=(
            "Minimum per-exchange universe share with margin balance; defaults "
            "to 0.90 for CSI800 and 0.75 for CSI1800 because CSI1000 includes "
            "more non-margin-eligible names"
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
    if args.margin_lag_sessions < 1:
        p.error("--margin-lag-sessions must be at least 1 for post-close sync")
    universe = args.universe
    target_date = args.target_date
    do_apply = args.apply
    if args.config:
        import yaml
        c = yaml.safe_load(Path(args.config).read_text())
        target_date = str(c.get("date_range", {}).get("end_date", "")).strip() or None
        if target_date is None:
            from scripts.ops.sync_csi800_daily import _resolve_target_date

            target_date = _resolve_target_date(None)
        configured_universe = c.get("universe", universe)
        if isinstance(configured_universe, dict):
            universe = configured_universe.get("name", universe)
        else:
            universe = configured_universe
        universe = universe or "csi800"
        do_apply = c.get("execution", {}).get("apply", False) or do_apply
        if c.get("tasks", {}).get("qlib_bin", True):
            cmd = [
                sys.executable,
                str(PROJ / "scripts/ops/sync_csi800_daily.py"),
                "--universe",
                universe,
                "--target-date",
                target_date,
            ]
            if do_apply: cmd.append("--apply")
            subprocess.run(cmd, cwd=str(PROJ), check=True)
    elif universe in {"csi800", "csi1800"}:
        if target_date is None:
            from scripts.ops.sync_csi800_daily import _resolve_target_date

            target_date = _resolve_target_date(None)
        cmd = [
            sys.executable,
            str(PROJ / "scripts/ops/sync_csi800_daily.py"),
            "--universe",
            universe,
            "--target-date",
            target_date,
        ]
        if args.apply: cmd.append("--apply")
        subprocess.run(cmd, cwd=str(PROJ), check=True)
    else:
        print(
            "Specify --config or --universe csi800|csi1800",
            file=sys.stderr,
        )
        sys.exit(1)

    if not do_apply or universe not in {"csi800", "csi1800"}:
        return

    margin_min_active = (
        args.margin_min_active
        if args.margin_min_active is not None
        else (1300 if universe == "csi1800" else 450)
    )
    margin_min_exchange_coverage = (
        args.margin_min_exchange_coverage
        if args.margin_min_exchange_coverage is not None
        else (0.75 if universe == "csi1800" else 0.90)
    )

    from qsys.data.adapter import QlibAdapter
    from qsys.ops.instrument_coverage import read_instrument_file
    from qsys.data.storage import StockDataStore
    from qsys.ops.raw_sync import (
        resolve_margin_availability_date,
        run_margin_history_repair,
    )
    from qsys.ops.trade_date import resolve_daily_trade_date

    resolved = resolve_daily_trade_date(
        target_date,
        universe=universe,
        allow_fallback_to_latest=False,
    )
    resolved_target = _require_exact_sync_target(
        resolved,
        requested_target=target_date,
        universe=universe,
    )
    adapter = QlibAdapter()
    instruments = read_instrument_file(
        adapter.qlib_dir / "instruments" / f"{universe}.txt"
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
                f"{universe} universe feature-history catch-up failed: "
                f"{history_result['summary_path']}"
            )
        if universe == "csi1800":
            from qsys.ops.pit_universe_snapshot import write_current_qlib_registry

            report["csi1800_registry_after_history"] = write_current_qlib_registry(
                qlib_dir=adapter.qlib_dir,
                universe=universe,
                instruments=symbols,
                as_of_date=resolved_target,
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
            min_active=margin_min_active,
            min_exchange_coverage=margin_min_exchange_coverage,
            apply=True,
            output_dir=PROJ / "runs" / "data_sync" / run_id / "margin_repair",
            store=store,
            signal_date=resolved_target,
            availability_lag_sessions=args.margin_lag_sessions,
            universe=universe,
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
                f"{universe} margin history repair failed: "
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
                f"{universe} shareholder history repair failed: "
                f"{shareholder_result['summary_path']}"
            )

    from qsys.data.health import inspect_qlib_data_health

    final_readiness = inspect_qlib_data_health(
        resolved_target,
        ["$open", "$high", "$low", "$close", "$volume", "$factor"],
        universe=universe,
        min_active_instruments=1750 if universe == "csi1800" else 750,
    )
    report["final_readiness"] = final_readiness.to_dict()
    if final_readiness.blocking_issues:
        raise RuntimeError(
            f"{universe} final readiness failed: "
            + "; ".join(final_readiness.blocking_issues)
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
