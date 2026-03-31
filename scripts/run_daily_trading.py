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
from qsys.data.collector import TushareCollector
from qsys.data.health import DataReadinessError, assert_qlib_data_ready
from qsys.live.account import RealAccount
from qsys.live.manager import LiveManager
from qsys.live.reconciliation import sync_real_account_from_csv
from qsys.live.scheduler import ModelScheduler
from qsys.live.simulation import ShadowSimulator
from qsys.reports.daily import DailyOpsReport
from qsys.utils.logger import log, log_event, log_stage


def update_data(signal_date: str, force=True, universe: str = "csi300"):
    log_stage("data_update", "start", signal_date=signal_date, universe=universe)
    try:
        collector = TushareCollector()
        collector.update_universe_history(universe, start_date=pd.Timestamp(signal_date).strftime("%Y%m%d"))

        adapter = QlibAdapter()
        adapter.refresh_qlib_date()

        report = adapter.get_data_status_report()
        log_stage(
            "data_update",
            "done",
            raw_latest=report.get("raw_latest"),
            qlib_latest=report.get("qlib_latest"),
            aligned=report.get("aligned"),
            gap_days=report.get("gap_days"),
        )
        return True, report
    except Exception as e:
        log_stage("data_update", "failed", error=str(e))
        return False, {"error": str(e), "aligned": False}


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


def extract_plan_summary(plan_df, account_name, signal_date, execution_date):
    if plan_df is None or plan_df.empty:
        return {
            "account": account_name,
            "status": "no_plan",
            "trades": 0,
            "symbols": [],
            "buy_trades": 0,
            "sell_trades": 0,
            "total_value": 0.0,
            "signal_date": signal_date,
            "execution_date": execution_date,
        }

    trades = plan_df[plan_df["amount"] > 0] if "amount" in plan_df.columns else plan_df
    symbols = trades["symbol"].tolist() if "symbol" in trades.columns else []
    side_series = trades["side"].astype(str).str.lower() if "side" in trades.columns else pd.Series(dtype=str)
    buy_trades = int((side_series == "buy").sum()) if not side_series.empty else 0
    sell_trades = int((side_series == "sell").sum()) if not side_series.empty else 0

    return {
        "account": account_name,
        "status": "ready" if len(trades) else "empty_plan",
        "trades": len(trades),
        "symbols": symbols,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "total_value": float(trades["est_value"].sum()) if "est_value" in trades.columns else 0.0,
        "signal_date": signal_date,
        "execution_date": execution_date,
    }


def log_plan_summary(summary):
    symbols = summary.get("symbols", [])
    preview_symbols = symbols[:5]
    suffix = "" if len(symbols) <= 5 else f" +{len(symbols) - 5} more"
    log_stage(
        "plan",
        summary.get("status", "unknown"),
        account=summary.get("account"),
        trades=summary.get("trades"),
        buys=summary.get("buy_trades"),
        sells=summary.get("sell_trades"),
        total_value=summary.get("total_value"),
        symbols=preview_symbols,
        extra_symbols=suffix or None,
        signal_date=summary.get("signal_date"),
        execution_date=summary.get("execution_date"),
    )


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
    log_stage("account_seed", "done", account=account_name, signal_date=signal_date, initial_cash=initial_cash)
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


def _resolve_cli_path(path_str: str) -> str:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return str(path)


def main():
    start_time = time.time()
    blockers = []

    parser = argparse.ArgumentParser(description="Run Daily Trading Workflow (Real + Shadow)")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Signal date or target execution date. If a future trading day is given without --execution_date, it is treated as execution date and the previous trading day is used as signal_date.")
    parser.add_argument("--execution_date", type=str, help="Execution Date (YYYY-MM-DD). Defaults to next trading day after signal date")
    parser.add_argument("--model_path", type=str, help="Path to model directory")
    parser.add_argument("--real_sync", type=str, help="Path to CSV file with Real Account state (cash, positions)")
    parser.add_argument("--db_path", default="data/real_account.db", help="SQLite account database path")
    parser.add_argument("--output_dir", default="data", help="Directory to write plan and sync-template artifacts")
    parser.add_argument("--report_dir", default="data/reports", help="Directory to write structured JSON reports")
    parser.add_argument("--skip_update", action="store_true", help="Skip data update")
    parser.add_argument("--require_update_success", action="store_true", help="Abort if the explicit data refresh step fails")
    parser.add_argument("--shadow_cash", type=float, default=1_000_000.0, help="Initial cash for Shadow Account")
    parser.add_argument("--real_cash", type=float, default=20_000.0, help="Initial cash for Real Account if no state exists")
    parser.add_argument("--retrain_days", type=int, default=7, help="Model max age in days before retraining")
    parser.add_argument("--top_k", type=int, default=30, help="Number of stocks to select in strategy")
    parser.add_argument("--min_trade", type=int, default=5000, help="Minimum trade amount in RMB")
    parser.add_argument("--no_report", action="store_true", help="Skip generating the structured report")
    args = parser.parse_args()
    args.db_path = _resolve_cli_path(args.db_path)
    args.output_dir = _resolve_cli_path(args.output_dir)
    args.report_dir = _resolve_cli_path(args.report_dir)
    if args.real_sync:
        args.real_sync = _resolve_cli_path(args.real_sync)

    data_status = {}
    model_info = {}
    health_ok = False
    signal_date, execution_date = resolve_signal_and_execution_date(args.date, args.execution_date)

    log_stage(
        "daily_workflow",
        "start",
        signal_date=signal_date,
        execution_date=execution_date,
        top_k=args.top_k,
        min_trade=args.min_trade,
        skip_update=args.skip_update,
        report_dir=args.report_dir,
    )

    if not args.skip_update:
        update_ok, update_report = update_data(signal_date=signal_date)
        if args.require_update_success and not update_ok:
            blockers.append("Explicit data refresh failed")
            data_status = {
                "raw_latest": update_report.get("raw_latest"),
                "qlib_latest": update_report.get("qlib_latest"),
                "aligned": update_report.get("aligned", False),
                "health_ok": False,
            }
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
                report.artifacts["account_db"] = args.db_path
                report_path = DailyOpsReport.save(report, output_dir=args.report_dir)
                log_stage("report", "saved", path=report_path)
            return

    QlibAdapter().init_qlib()

    model_path = args.model_path
    if not model_path:
        try:
            model_path = ModelScheduler.resolve_production_model()
            log_stage("model_resolution", "manifest", model_path=model_path)
        except Exception as e:
            log_event("warning", "model_resolution_fallback", reason=str(e))
            latest_model = ModelScheduler.find_latest_model()
            if latest_model:
                model_path = str(latest_model)
                log_stage("model_resolution", "latest_model", model_path=model_path)
            else:
                log_event("error", "model_resolution_failed", reason="no model found")
                return

    if not Path(model_path).exists():
        log_event("error", "model_path_missing", model_path=model_path)
        return

    model_path = ModelScheduler.check_and_retrain(model_path, signal_date, retrain_freq_days=args.retrain_days)
    log_stage("model_ready", "done", model_path=model_path)

    model_info = {
        "model_path": model_path,
        "model_name": Path(model_path).name,
    }

    preview_manager = LiveManager(model_path=model_path)
    preview_manager.load_model()

    feature_config = preview_manager.model.model.feature_config
    model_info["feature_set"] = feature_config.get("name", "unknown") if isinstance(feature_config, dict) else "alpha158"

    try:
        health = assert_qlib_data_ready(signal_date, feature_config, universe="csi300")
        health_ok = True
    except DataReadinessError as readiness_error:
        health = readiness_error.report
        health_ok = False
        log_event("error", "data_readiness_failed", reason=str(readiness_error))

    data_status = {
        "raw_latest": health.raw_latest,
        "qlib_latest": health.last_qlib_date,
        "aligned": health.aligned,
        "health_ok": health_ok,
        "expected_latest_date": health.expected_latest_date,
        "feature_rows": health.feature_rows,
        "feature_cols": health.feature_cols,
        "missing_ratio": health.missing_ratio,
        "core_daily_status": health.core_daily_status,
        "pit_status": health.pit_status,
        "margin_status": health.margin_status,
        "blocking_issues": health.blocking_issues,
        "warnings": health.warnings,
    }
    log_stage(
        "readiness",
        "ok" if health_ok else "blocked",
        core_daily_status=health.core_daily_status,
        pit_status=health.pit_status,
        margin_status=health.margin_status,
        aligned=health.aligned,
        feature_rows=health.feature_rows,
        missing_ratio=health.missing_ratio,
        blockers=len(health.blocking_issues),
        warnings=len(health.warnings),
    )

    if not health_ok:
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
            report_path = DailyOpsReport.save(report, output_dir=args.report_dir)
            log_stage("report", "saved", path=report_path)
        return

    shadow_account_name = "shadow"
    shadow_sim = ShadowSimulator(account_name=shadow_account_name, initial_cash=args.shadow_cash, db_path=args.db_path)

    if shadow_sim.initialize_if_needed(signal_date):
        log_stage("shadow_account", "initialized", signal_date=signal_date)
    else:
        previous_signal_date = previous_trading_day(signal_date)
        plan_path = Path(args.output_dir) / f"plan_{previous_signal_date}_{shadow_account_name}.csv"
        if plan_path.exists():
            log_stage("shadow_account", "simulate_previous_plan", plan_path=str(plan_path), signal_date=signal_date)
            shadow_sim.simulate_execution(str(plan_path), signal_date)
        else:
            log_stage("shadow_account", "skip_simulation", reason="previous plan missing", plan_path=str(plan_path))

    real_account_name = "real"
    real_account = RealAccount(db_path=args.db_path, account_name=real_account_name)
    if args.real_sync:
        log_stage("real_sync", "start", path=args.real_sync, signal_date=signal_date)
        sync_real_account_from_csv(real_account, real_account_name, args.real_sync, signal_date)
        log_stage("real_sync", "done", path=args.real_sync)
    ensure_real_account_seeded(real_account, signal_date, args.real_cash, real_account_name)

    manager_shadow = LiveManager(model_path=model_path, db_path=args.db_path, output_dir=args.output_dir)
    manager_shadow.strategy.top_k = args.top_k
    manager_shadow.planner.min_trade_amount = args.min_trade
    manager_shadow.real_account = shadow_sim.account
    log_stage("plan_generation", "start", account=shadow_account_name)
    plan_shadow = manager_shadow.run_daily_plan(signal_date, account_name=shadow_account_name, execution_date=execution_date)
    shadow_summary = extract_plan_summary(plan_shadow, shadow_account_name, signal_date, execution_date)
    log_plan_summary(shadow_summary)

    manager_real = LiveManager(model_path=model_path, db_path=args.db_path, output_dir=args.output_dir)
    manager_real.strategy.top_k = args.top_k
    manager_real.planner.min_trade_amount = args.min_trade
    manager_real.real_account = real_account
    log_stage("plan_generation", "start", account=real_account_name)
    plan_real = manager_real.run_daily_plan(signal_date, account_name=real_account_name, execution_date=execution_date)
    real_summary = extract_plan_summary(plan_real, real_account_name, signal_date, execution_date)
    log_plan_summary(real_summary)

    duration = time.time() - start_time

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
        report.artifacts["shadow_plan"] = str(Path(args.output_dir) / f"plan_{signal_date}_{shadow_account_name}.csv")
        report.artifacts["real_plan"] = str(Path(args.output_dir) / f"plan_{signal_date}_{real_account_name}.csv")
        report.artifacts["shadow_real_sync_template"] = str(Path(args.output_dir) / f"real_sync_template_{signal_date}_{shadow_account_name}.csv")
        report.artifacts["real_real_sync_template"] = str(Path(args.output_dir) / f"real_sync_template_{signal_date}_{real_account_name}.csv")
        report.artifacts["account_db"] = args.db_path

        report_path = DailyOpsReport.save(report, output_dir=args.report_dir)
        log_stage("report", "saved", path=report_path)

    log_stage("daily_workflow", "done", duration_seconds=duration, blockers=len(blockers))


if __name__ == "__main__":
    main()
