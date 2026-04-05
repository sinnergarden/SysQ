"""
Primary daily ops entrypoint (post-close).

Purpose:
- sync broker-exported real account state
- reconcile real vs shadow
- emit structured post-close report

Typical usage:
- python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/real_sync_2026-03-20.csv
- python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/miniqmt_readback_2026-03-20.json

Key args:
- --date: trading date being reconciled
- --real_sync: broker/account CSV or MiniQMT readback JSON after market close
- --db_path / --output_dir / --report_dir / --plan_dir: redirect operational artifacts outside SysQ/data
- --execution_date: optional execution date override
- --no_report: skip JSON run report
"""

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.data.adapter import QlibAdapter
from qsys.live.account import RealAccount
from qsys.live.daily_artifacts import archive_daily_artifacts, build_daily_summary_bundle, extract_account_snapshot
from qsys.live.ops_manifest import update_manifest
from qsys.live.ops_paths import build_stage_paths, find_plan_path_for_execution_date, resolve_account_db_path
from qsys.live.reconciliation import (
    build_reconciliation_result,
    reconciliation_to_markdown,
    sync_real_account_from_csv,
    write_reconciliation_outputs,
)
from qsys.live.signal_monitoring import collect_signal_quality_snapshot, write_signal_quality_outputs
from qsys.live.signal_monitoring import build_signal_quality_blockers
from qsys.reports.daily import DailyOpsReport
from qsys.utils.logger import log


def _resolve_cli_path(path_str: str) -> str:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return str(path)


def _resolve_ops_paths(
    *,
    execution_date: str,
    output_dir: str | None,
    report_dir: str | None,
    plan_dir: str | None,
    db_path: str | None,
) -> dict[str, str]:
    daily_root = project_root / "daily"
    post_close_paths = build_stage_paths(execution_date, stage="post_close", daily_root=daily_root)
    pre_open_paths = build_stage_paths(execution_date, stage="pre_open", daily_root=daily_root)

    if db_path is None:
        resolved_db_path = str(resolve_account_db_path(project_root=project_root))
    else:
        resolved_db_path = _resolve_cli_path(db_path)

    resolved_output_dir = str(post_close_paths.root) if output_dir is None else _resolve_cli_path(output_dir)
    resolved_report_dir = str(post_close_paths.reports_dir) if report_dir is None else _resolve_cli_path(report_dir)
    resolved_manifest_dir = str(post_close_paths.manifests_dir) if report_dir is None else resolved_report_dir
    resolved_plan_dir = str(pre_open_paths.plans_dir) if plan_dir is None else _resolve_cli_path(plan_dir)

    return {
        "daily_root": str(daily_root),
        "output_dir": resolved_output_dir,
        "report_dir": resolved_report_dir,
        "manifest_dir": resolved_manifest_dir,
        "plan_dir": resolved_plan_dir,
        "db_path": resolved_db_path,
    }


def main():
    start_time = time.time()
    blockers = []
    
    parser = argparse.ArgumentParser(
        description="Post-close workflow: sync real account CSV and reconcile against shadow account"
    )
    parser.add_argument("--date", required=True, help="Trading date to reconcile (YYYY-MM-DD)")
    parser.add_argument("--real_sync", required=True, help="Broker/account CSV or MiniQMT readback JSON exported after market close")
    parser.add_argument("--db_path", help="SQLite account database path (default: data/meta/real_account.db with legacy fallback to data/real_account.db)")
    parser.add_argument(
        "--output_dir",
        help="Directory to write post-close artifacts (default: daily/<execution_date>/post_close)",
    )
    parser.add_argument(
        "--plan_dir",
        help="Directory containing pre-open plan CSV artifacts (default: daily/<execution_date>/pre_open/plans)",
    )
    parser.add_argument(
        "--report_dir",
        help="Directory to write structured JSON reports (default: daily/<execution_date>/post_close/reports)",
    )
    parser.add_argument("--real_account_name", default="real", help="Account name for real broker state")
    parser.add_argument("--shadow_account_name", default="shadow", help="Account name for shadow simulation state")
    parser.add_argument("--execution_date", type=str, help="Execution date (defaults to args.date)")
    parser.add_argument("--no_report", action="store_true", help="Skip generating the structured report")
    args = parser.parse_args()
    execution_date = args.execution_date or args.date
    resolved_paths = _resolve_ops_paths(
        execution_date=execution_date,
        output_dir=args.output_dir,
        report_dir=args.report_dir,
        plan_dir=args.plan_dir,
        db_path=args.db_path,
    )
    daily_root = resolved_paths["daily_root"]
    args.db_path = resolved_paths["db_path"]
    args.output_dir = resolved_paths["output_dir"]
    args.report_dir = resolved_paths["report_dir"]
    manifest_dir = resolved_paths["manifest_dir"]
    args.plan_dir = resolved_paths["plan_dir"]
    args.real_sync = _resolve_cli_path(args.real_sync)
    
    # Get data status for report
    try:
        adapter = QlibAdapter()
        adapter.init_qlib()
        status_report = adapter.get_data_status_report()
        data_status = {
            "raw_latest": status_report.get("raw_latest"),
            "qlib_latest": status_report.get("qlib_latest"),
            "aligned": status_report.get("aligned", False),
            "health_ok": True,
        }
    except Exception as e:
        log.warning(f"Could not get data status: {e}")
        data_status = {"health_ok": False}
    
    # Model info (read from manifest if available)
    model_info = {}
    try:
        from qsys.live.scheduler import ModelScheduler
        prod_model = ModelScheduler.resolve_production_model()
        model_info = {"model_path": str(prod_model), "model_name": prod_model.name}
    except Exception as e:
        log.warning(f"Could not resolve production model: {e}")

    account = RealAccount(db_path=args.db_path, account_name=args.real_account_name)
    account_snapshots = {}
    
    # Try to get signal_date from plan file
    signal_date = args.date
    plan_path = find_plan_path_for_execution_date(
        execution_date=execution_date,
        account_name=args.shadow_account_name,
        plan_dir=args.plan_dir,
        daily_root=daily_root,
        legacy_root=project_root / "data",
    )
    if plan_path and plan_path.exists():
        try:
            plan_df = pd.read_csv(plan_path)
            if "signal_date" in plan_df.columns:
                signal_date = str(plan_df["signal_date"].iloc[0])
        except Exception:
            pass

    normalized = sync_real_account_from_csv(
        account,
        account_name=args.real_account_name,
        sync_path=args.real_sync,
        date=args.date,
        persist_trade_log=True,
    )

    result = build_reconciliation_result(
        account,
        date=args.date,
        real_account_name=args.real_account_name,
        shadow_account_name=args.shadow_account_name,
    )
    account_snapshots[args.real_account_name] = extract_account_snapshot(
        account,
        date=args.date,
        account_name=args.real_account_name,
    )
    account_snapshots[args.shadow_account_name] = extract_account_snapshot(
        account,
        date=args.date,
        account_name=args.shadow_account_name,
    )
    written = write_reconciliation_outputs(
        result,
        args.output_dir,
        date=args.date,
        real_sync_snapshot=normalized,
    )

    print(reconciliation_to_markdown(result))
    for name, path in written.items():
        log.info(f"Wrote {name}: {path}")
    
    # Build reconciliation summary for report
    reconciliation_summary = {}
    if not result.summary.empty:
        for _, row in result.summary.iterrows():
            reconciliation_summary[row["metric"]] = {
                "real": row.get("real"),
                "shadow": row.get("shadow"),
                "diff": row.get("diff"),
            }
    
    position_gaps = len(result.positions) if not result.positions.empty else 0
    if not result.positions.empty:
        position_gaps = len(result.positions[result.positions["amount_diff"] != 0])

    signal_quality_snapshot = None
    signal_quality_summary = {}
    try:
        signal_quality_snapshot = collect_signal_quality_snapshot(as_of_date=args.date, signal_dir=daily_root)
        signal_quality_summary = signal_quality_snapshot.summary
        blockers.extend(build_signal_quality_blockers(signal_quality_summary, required_horizons=(1, 2, 3)))
    except Exception as e:
        log.warning(f"Could not build signal quality snapshot: {e}")
        signal_quality_summary = {
            "as_of_date": args.date,
            "status": "failed",
            "reason": str(e),
        }
        blockers.append(f"Signal quality evaluation failed: {e}")
    
    # Generate structured report
    duration = time.time() - start_time
    
    if not args.no_report:
        report = DailyOpsReport.generate_post_close_report(
            signal_date=signal_date,
            execution_date=execution_date,
            data_status=data_status,
            model_info=model_info,
            reconciliation_summary=reconciliation_summary,
            signal_quality_summary=signal_quality_summary,
            real_trades_count=len(result.real_trades) if not result.real_trades.empty else 0,
            position_gaps_count=position_gaps,
            duration_seconds=duration,
            blockers=blockers,
        )
        report.artifacts.update(written)
        if signal_quality_snapshot is not None:
            report.artifacts.update(
                write_signal_quality_outputs(
                    signal_quality_snapshot,
                    output_dir=args.output_dir,
                    as_of_date=args.date,
                )
            )
        
        report.artifacts["account_db"] = args.db_path
        report_path = DailyOpsReport.save(report, output_dir=args.report_dir)
        manifest_path = update_manifest(
            report_dir=manifest_dir,
            execution_date=execution_date,
            signal_date=signal_date,
            stage="post_close",
            status=report.status.value,
            report_path=report_path,
            artifacts=report.artifacts,
            data_status=data_status,
            model_info=model_info,
            blockers=blockers,
            summary={
                "reconciliation": reconciliation_summary,
                "signal_quality": signal_quality_summary,
                "account_snapshots": account_snapshots,
            },
        )
        archive_info = archive_daily_artifacts(
            execution_date=execution_date,
            signal_date=signal_date,
            stage="post_close",
            artifacts={**report.artifacts, "report": report_path, "manifest": manifest_path},
            archive_root=daily_root,
        )
        digest = build_daily_summary_bundle(execution_date=execution_date, archive_root=daily_root)
        log.info(f"Report saved to: {report_path}")
        log.info(f"Manifest saved to: {manifest_path}")
        log.info(f"Daily index saved to: {archive_info['index_path']}")
        log.info(f"Daily digest saved to: {digest.report_markdown_path}")
        
        # Also print markdown summary
        print("\n" + "=" * 60)
        print(report.to_markdown())
        print("=" * 60)
    
    log.info(f"Post-close workflow completed. Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()
