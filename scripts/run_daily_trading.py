"""
Primary daily ops entrypoint (pre-open).

Purpose:
- validate data/model readiness
- build shadow + real trading plans
- emit structured daily pre-open report

Typical usage:
- python scripts/run_daily_trading.py --date 2026-03-20 --execution_date 2026-03-23
- python scripts/run_daily_trading.py --date 2026-03-23  # future date is treated as execution_date

Key args:
- --date: signal date or future execution date
- --execution_date: explicit execution date
- --model_path: override production model manifest resolution
- --skip_update: skip qlib refresh
- --top_k / --min_trade: plan construction knobs
- --no_report: skip JSON run report
"""

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from qlib.data import D

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from qsys.data.adapter import QlibAdapter
from qsys.data.health import DataReadinessError, assert_qlib_data_ready
from qsys.live.account import RealAccount
from qsys.live.manager import LiveManager
from qsys.live.reconciliation import sync_real_account_from_csv
from qsys.live.scheduler import ModelScheduler
from qsys.live.simulation import ShadowSimulator
from qsys.reports.base import ReportStatus
from qsys.reports.daily import DailyOpsReport
from qsys.utils.logger import log


def update_data(force=True):
    log.info("Updating Qlib Data...")
    try:
        adapter = QlibAdapter()
        # Use the explicit refresh to close the raw->qlib loop
        adapter.refresh_qlib_date()
        
        # Print status report
        report = adapter.get_data_status_report()
        log.info(f"Data status: raw={report.get('raw_latest')}, qlib={report.get('qlib_latest')}, aligned={report.get('aligned')}")
    except Exception as e:
        log.error(f"Failed to update data: {e}")


def next_trading_day(signal_date: str) -> str:
    ts = pd.Timestamp(signal_date)
    calendar = D.calendar(start_time=ts, end_time=ts + pd.Timedelta(days=10))
    future_days = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) > ts]
    if not future_days:
        return ts.strftime("%Y-%m-%d")
    return min(future_days).strftime("%Y-%m-%d")


def previous_trading_day(signal_date: str) -> str:
    ts = pd.Timestamp(signal_date)
    calendar = D.calendar(start_time=ts - pd.Timedelta(days=10), end_time=ts)
    past_days = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) < ts]
    if not past_days:
        return (ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return max(past_days).strftime("%Y-%m-%d")


def print_plan_summary(plan_df, account_name, signal_date, execution_date):
    if plan_df is None or plan_df.empty:
        log.info(f"No trades planned for {account_name}.")
        return

    log.info(f"=== Trading Plan for {account_name} | signal_date={signal_date} execution_date={execution_date} ===")
    required_cols = {"amount", "symbol", "side", "price"}
    missing_cols = required_cols - set(plan_df.columns)
    if missing_cols:
        log.warning(f"Plan format invalid for {account_name}. Missing columns: {sorted(missing_cols)}")
        return

    trades = plan_df[plan_df["amount"] > 0]
    if trades.empty:
        log.info("No active trades required (Portfolio balanced).")
        return

    for _, row in trades.iterrows():
        log.info(
            f"{row['side'].upper()} {int(row['amount'])} shares of {row['symbol']} @ {float(row['price']):.2f} "
            f"| weight={float(row.get('weight', 0.0)):.4f} score={row.get('score')} rank={row.get('score_rank')}"
        )
    log.info(f"Total Trades: {len(trades)}")


def ensure_real_account_seeded(real_account: RealAccount, signal_date: str, initial_cash: float, account_name: str):
    latest_date = real_account.get_latest_date(before_date=signal_date, account_name=account_name)
    if latest_date:
        return latest_date

    empty_positions = pd.DataFrame(columns=["symbol", "amount", "price", "cost_basis"])
    real_account.sync_broker_state(
        date=signal_date,
        cash=initial_cash,
        positions=empty_positions,
        total_assets=initial_cash,
        account_name=account_name,
    )
    log.info(f"Seeded real account '{account_name}' with initial cash {initial_cash:,.2f} on {signal_date}")
    return signal_date


def resolve_signal_and_execution_date(date_arg: str | None, execution_date_arg: str | None):
    if execution_date_arg:
        signal_date = pd.Timestamp(date_arg).strftime("%Y-%m-%d") if date_arg else pd.Timestamp(datetime.now()).strftime("%Y-%m-%d")
        execution_date = pd.Timestamp(execution_date_arg).strftime("%Y-%m-%d")
        return signal_date, execution_date

    if date_arg:
        normalized = pd.Timestamp(date_arg).strftime("%Y-%m-%d")
        today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d"))
        target = pd.Timestamp(normalized)
        if target > today:
            signal_date = previous_trading_day(normalized)
            return signal_date, normalized
        return normalized, next_trading_day(normalized)

    today = pd.Timestamp(datetime.now().strftime("%Y-%m-%d")).strftime("%Y-%m-%d")
    return today, next_trading_day(today)


def main():
    start_time = time.time()
    blockers = []
    
    parser = argparse.ArgumentParser(description="Run Daily Trading Workflow (Real + Shadow)")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Signal date or target execution date. If a future trading day is given without --execution_date, it is treated as execution date and the previous trading day is used as signal_date.")
    parser.add_argument("--execution_date", type=str, help="Execution Date (YYYY-MM-DD). Defaults to next trading day after signal date")
    parser.add_argument("--model_path", type=str, help="Path to model directory")
    parser.add_argument("--real_sync", type=str, help="Path to CSV file with Real Account state (cash, positions)")
    parser.add_argument("--skip_update", action="store_true", help="Skip data update")
    parser.add_argument("--shadow_cash", type=float, default=1_000_000.0, help="Initial cash for Shadow Account")
    parser.add_argument("--real_cash", type=float, default=20_000.0, help="Initial cash for Real Account if no state exists")
    parser.add_argument("--retrain_days", type=int, default=7, help="Model max age in days before retraining")
    parser.add_argument("--top_k", type=int, default=30, help="Number of stocks to select in strategy")
    parser.add_argument("--min_trade", type=int, default=5000, help="Minimum trade amount in RMB")
    parser.add_argument("--no_report", action="store_true", help="Skip generating the structured report")
    args = parser.parse_args()

    # Data status tracking for report
    data_status = {}
    model_info = {}
    health_ok = False
    
    if not args.skip_update:
        update_data()

    QlibAdapter().init_qlib()
    signal_date, execution_date = resolve_signal_and_execution_date(args.date, args.execution_date)
    log.info(f"=== Starting Daily Trading Workflow for signal_date={signal_date}, execution_date={execution_date} ===")

    model_path = args.model_path
    if not model_path:
        # P0.1: Prefer production manifest resolution for daily ops
        try:
            model_path = ModelScheduler.resolve_production_model()
            log.info(f"Using production model from manifest: {model_path}")
        except Exception as e:
            log.warning(f"Could not resolve from manifest: {e}. Falling back to latest model.")
            latest_model = ModelScheduler.find_latest_model()
            if latest_model:
                model_path = str(latest_model)
                log.info(f"Auto-detected latest model: {model_path}")
            else:
                log.error("No model path provided and none found in data/models or data/experiments.")
                return

    if not Path(model_path).exists():
        log.error(f"Model path does not exist: {model_path}")
        return

    model_path = ModelScheduler.check_and_retrain(model_path, signal_date, retrain_freq_days=args.retrain_days)
    log.info(f"Using Model: {model_path}")
    
    # Record model info for report
    model_info = {
        "model_path": model_path,
        "model_name": Path(model_path).name,
    }

    preview_manager = LiveManager(model_path=model_path)
    preview_manager.load_model()
    
    # Track feature set for report
    feature_config = preview_manager.model.model.feature_config
    model_info["feature_set"] = feature_config.get("name", "unknown") if isinstance(feature_config, dict) else "alpha158"
    
    try:
        health = assert_qlib_data_ready(signal_date, feature_config, universe="csi300")
        health_ok = True
    except DataReadinessError as readiness_error:
        health = readiness_error.report
        health_ok = False
        log.error(str(readiness_error))

    data_status = {
        "raw_latest": health.raw_latest,
        "qlib_latest": health.last_qlib_date,
        "aligned": health.aligned,
        "health_ok": health_ok,
        "expected_latest_date": health.expected_latest_date,
        "feature_rows": health.feature_rows,
        "feature_cols": health.feature_cols,
        "missing_ratio": health.missing_ratio,
    }
    
    if not health_ok:
        log.error("\n" + health.to_markdown())
        log.error("Data health check failed. Aborting daily workflow.")
        blockers.append("Data health check failed")
        
        if not args.no_report:
            report = DailyOpsReport.generate_pre_open_report(
                signal_date=signal_date,
                execution_date=execution_date,
                data_status=data_status,
                model_info=model_info,
                shadow_plan_summary={"status": "skipped"},
                real_plan_summary={"status": "skipped"},
                duration_seconds=time.time() - start_time,
                blockers=blockers,
            )
            report_path = DailyOpsReport.save(report)
            log.info(f"Report saved to: {report_path}")
        return
    log.info("\n" + health.to_markdown())

    shadow_account_name = "shadow"
    shadow_sim = ShadowSimulator(account_name=shadow_account_name, initial_cash=args.shadow_cash)

    # Seed and init accounts BEFORE creating managers
    if shadow_sim.initialize_if_needed(signal_date):
        log.info("Shadow Account Initialized.")
    else:
        previous_signal_date = previous_trading_day(signal_date)
        plan_path = f"data/plan_{previous_signal_date}_{shadow_account_name}.csv"
        if Path(plan_path).exists():
            log.info(f"Simulating execution for Shadow Account using {plan_path}...")
            shadow_sim.simulate_execution(plan_path, signal_date)
        else:
            log.warning(f"No plan found for previous signal_date ({previous_signal_date}) at {plan_path}. Skipping shadow simulation.")

    real_account_name = "real"
    real_account = RealAccount(account_name=real_account_name)
    if args.real_sync:
        sync_real_account_from_csv(real_account, real_account_name, args.real_sync, signal_date)
    ensure_real_account_seeded(real_account, signal_date, args.real_cash, real_account_name)

    # Now managers can see seeded accounts
    log.info("Generating Plan for Shadow Account...")
    manager_shadow = LiveManager(model_path=model_path)
    manager_shadow.strategy.top_k = args.top_k
    manager_shadow.planner.min_trade_amount = args.min_trade
    manager_shadow.real_account = shadow_sim.account
    plan_shadow = manager_shadow.run_daily_plan(signal_date, account_name=shadow_account_name, execution_date=execution_date)
    print_plan_summary(plan_shadow, shadow_account_name, signal_date, execution_date)

    log.info("Generating Plan for Real Account...")
    manager_real = LiveManager(model_path=model_path)
    manager_real.strategy.top_k = args.top_k
    manager_real.planner.min_trade_amount = args.min_trade
    manager_real.real_account = real_account
    plan_real = manager_real.run_daily_plan(signal_date, account_name=real_account_name, execution_date=execution_date)
    print_plan_summary(plan_real, real_account_name, signal_date, execution_date)

    # Generate structured report
    duration = time.time() - start_time
    
    def extract_plan_summary(plan_df, account_name):
        if plan_df is None or plan_df.empty:
            return {"account": account_name, "trades": 0, "symbols": []}
        trades = plan_df[plan_df["amount"] > 0] if "amount" in plan_df.columns else plan_df
        return {
            "account": account_name,
            "trades": len(trades),
            "symbols": trades["symbol"].tolist() if "symbol" in trades.columns else [],
            "total_value": float(trades["est_value"].sum()) if "est_value" in trades.columns else 0.0,
        }
    
    shadow_summary = extract_plan_summary(plan_shadow, shadow_account_name)
    real_summary = extract_plan_summary(plan_real, real_account_name)
    
    if not args.no_report:
        report = DailyOpsReport.generate_pre_open_report(
            signal_date=signal_date,
            execution_date=execution_date,
            data_status=data_status,
            model_info=model_info,
            shadow_plan_summary=shadow_summary,
            real_plan_summary=real_summary,
            duration_seconds=duration,
            blockers=blockers,
            notes=[
                f"top_k={args.top_k}",
                f"min_trade={args.min_trade}",
                f"shadow_cash={args.shadow_cash}",
                f"real_cash={args.real_cash}",
            ],
        )
        report.artifacts["shadow_plan"] = f"data/plan_{signal_date}_{shadow_account_name}.csv"
        report.artifacts["real_plan"] = f"data/plan_{signal_date}_{real_account_name}.csv"
        
        report_path = DailyOpsReport.save(report)
        log.info(f"Report saved to: {report_path}")
        
        # Also print markdown summary
        print("\n" + "=" * 60)
        print(report.to_markdown())
        print("=" * 60)
    
    log.info(f"Daily Trading Workflow Completed. Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()
