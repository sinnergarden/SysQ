"""
Primary daily ops entrypoint (post-close).

Purpose:
- sync broker-exported real account state
- reconcile real vs shadow
- emit structured post-close report

Typical usage:
- python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/real_sync_2026-03-20.csv

Key args:
- --date: trading date being reconciled
- --real_sync: broker/account CSV after market close
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
from qsys.live.reconciliation import (
    build_reconciliation_result,
    reconciliation_to_markdown,
    sync_real_account_from_csv,
    write_reconciliation_outputs,
)
from qsys.reports.daily import DailyOpsReport
from qsys.utils.logger import log


def _resolve_cli_path(path_str: str) -> str:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return str(path)


def main():
    start_time = time.time()
    blockers = []
    
    parser = argparse.ArgumentParser(
        description="Post-close workflow: sync real account CSV and reconcile against shadow account"
    )
    parser.add_argument("--date", required=True, help="Trading date to reconcile (YYYY-MM-DD)")
    parser.add_argument("--real_sync", required=True, help="Broker/account CSV exported after market close")
    parser.add_argument("--db_path", default="data/real_account.db", help="SQLite account database path")
    parser.add_argument(
        "--output_dir",
        default="data/reconciliation",
        help="Directory to write reconciliation CSV outputs",
    )
    parser.add_argument(
        "--plan_dir",
        default="data",
        help="Directory containing pre-open plan CSV artifacts",
    )
    parser.add_argument(
        "--report_dir",
        default="data/reports",
        help="Directory to write structured JSON reports",
    )
    parser.add_argument("--real_account_name", default="real", help="Account name for real broker state")
    parser.add_argument("--shadow_account_name", default="shadow", help="Account name for shadow simulation state")
    parser.add_argument("--execution_date", type=str, help="Execution date (defaults to args.date)")
    parser.add_argument("--no_report", action="store_true", help="Skip generating the structured report")
    args = parser.parse_args()
    args.db_path = _resolve_cli_path(args.db_path)
    args.output_dir = _resolve_cli_path(args.output_dir)
    args.report_dir = _resolve_cli_path(args.report_dir)
    args.plan_dir = _resolve_cli_path(args.plan_dir)
    args.real_sync = _resolve_cli_path(args.real_sync)
    
    execution_date = args.execution_date or args.date
    
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
    
    # Try to get signal_date from plan file
    signal_date = args.date
    plan_path = Path(args.plan_dir) / f"plan_{args.date}_{args.shadow_account_name}.csv"
    if plan_path.exists():
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
    
    # Generate structured report
    duration = time.time() - start_time
    
    if not args.no_report:
        report = DailyOpsReport.generate_post_close_report(
            signal_date=signal_date,
            execution_date=execution_date,
            data_status=data_status,
            model_info=model_info,
            reconciliation_summary=reconciliation_summary,
            real_trades_count=len(result.real_trades) if not result.real_trades.empty else 0,
            position_gaps_count=position_gaps,
            duration_seconds=duration,
            blockers=blockers,
        )
        report.artifacts.update(written)
        
        report.artifacts["account_db"] = args.db_path
        report_path = DailyOpsReport.save(report, output_dir=args.report_dir)
        log.info(f"Report saved to: {report_path}")
        
        # Also print markdown summary
        print("\n" + "=" * 60)
        print(report.to_markdown())
        print("=" * 60)
    
    log.info(f"Post-close workflow completed. Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()
