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
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml
from qlib.data import D

# Add project root to sys.path
project_root = Path(__file__).parent.parent
sys.path.append(str(project_root))

from qsys.data.adapter import QlibAdapter
from qsys.data.health import DataReadinessError, assert_qlib_data_ready
from qsys.live.account import RealAccount
from qsys.live.daily_artifacts import archive_daily_artifacts, build_daily_summary_bundle, extract_account_snapshot
from qsys.live.manager import LiveManager
from qsys.live.ops_manifest import update_manifest
from qsys.live.ops_paths import build_stage_paths, find_plan_path_for_execution_date, resolve_account_db_path
from qsys.live.reconciliation import sync_real_account_from_csv
from qsys.live.signal_monitoring import (
    build_signal_quality_blockers,
    collect_signal_quality_snapshot,
    save_signal_basket,
    write_signal_quality_outputs,
)
from qsys.live.scheduler import ModelScheduler
from qsys.live.simulation import ShadowSimulator
from qsys.reports.daily import DailyOpsReport
from qsys.reports.unified_schema import training_contract_payload, unified_run_artifacts, write_csv, write_json
from qsys.trader.order_intents import build_order_intents, save_order_intents
from qsys.utils.logger import log, log_event, log_stage


def update_data(force=True):
    log_stage("data_update", "start")
    try:
        adapter = QlibAdapter()
        # Use the explicit refresh to close the raw->qlib loop
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


def _resolve_trading_day(anchor_date: str, *, direction: str) -> str:
    QlibAdapter().init_qlib()
    ts = pd.Timestamp(anchor_date)
    if direction == "next":
        calendar = D.calendar(start_time=ts, end_time=ts + pd.Timedelta(days=10))
        candidates = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) > ts]
        fallback = ts
    else:
        calendar = D.calendar(start_time=ts - pd.Timedelta(days=10), end_time=ts)
        candidates = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) < ts]
        fallback = ts - pd.Timedelta(days=1)
    if not candidates:
        return fallback.strftime("%Y-%m-%d")
    selector = min if direction == "next" else max
    return selector(candidates).strftime("%Y-%m-%d")


def next_trading_day(signal_date: str) -> str:
    return _resolve_trading_day(signal_date, direction="next")


def previous_trading_day(signal_date: str) -> str:
    return _resolve_trading_day(signal_date, direction="previous")


def _resolve_model_feature_set(model_path: str, feature_config) -> str:
    if isinstance(feature_config, dict):
        feature_name = feature_config.get("feature_set_alias") or feature_config.get("feature_set_name") or feature_config.get("name")
        if feature_name:
            return str(feature_name)

    meta_path = Path(model_path) / "meta.yaml"
    if meta_path.exists():
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                meta = yaml.safe_load(handle) or {}
            training_meta = meta.get("training_summary") or {}
            feature_name = training_meta.get("feature_set_alias") or training_meta.get("feature_set_name")
            if feature_name:
                return str(feature_name)
        except Exception as exc:
            log.warning(f"Failed to read feature_set from {meta_path}: {exc}")

    feature_selection_path = Path(model_path) / "feature_selection.yaml"
    if feature_selection_path.exists():
        try:
            with open(feature_selection_path, "r", encoding="utf-8") as handle:
                selection_meta = yaml.safe_load(handle) or {}
            feature_name = selection_meta.get("feature_set_alias") or selection_meta.get("feature_set_name")
            if feature_name:
                return str(feature_name)
        except Exception as exc:
            log.warning(f"Failed to read feature_set from {feature_selection_path}: {exc}")

    return "alpha158"


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


def _resolve_ops_paths(
    *,
    execution_date: str,
    daily_root: str | None,
    output_dir: str | None,
    report_dir: str | None,
    db_path: str | None,
):
    resolved_daily_root = project_root / "daily" if daily_root is None else Path(_resolve_cli_path(daily_root))
    stage_paths = build_stage_paths(execution_date, stage="pre_open", daily_root=resolved_daily_root)

    if db_path is None:
        resolved_db_path = str(resolve_account_db_path(project_root=project_root))
    else:
        resolved_db_path = _resolve_cli_path(db_path)

    resolved_output_dir = str(stage_paths.root) if output_dir is None else _resolve_cli_path(output_dir)
    resolved_report_dir = str(stage_paths.reports_dir) if report_dir is None else _resolve_cli_path(report_dir)
    resolved_manifest_dir = str(stage_paths.manifests_dir) if report_dir is None else resolved_report_dir
    return {
        "daily_root": str(resolved_daily_root),
        "stage_paths": stage_paths,
        "db_path": resolved_db_path,
        "output_dir": resolved_output_dir,
        "report_dir": resolved_report_dir,
        "manifest_dir": resolved_manifest_dir,
    }


def run_preopen_workflow(
    *,
    date: str | None = None,
    execution_date: str | None = None,
    model_path: str | None = None,
    real_sync: str | None = None,
    db_path: str | None = None,
    output_dir: str | None = None,
    report_dir: str | None = None,
    daily_root: str | None = None,
    skip_update: bool = False,
    require_update_success: bool = False,
    shadow_cash: float = 1_000_000.0,
    real_cash: float = 20_000.0,
    retrain_days: int = 7,
    top_k: int = 5,
    min_trade: int = 5000,
    skip_signal_quality_gate: bool = False,
    no_report: bool = False,
    disable_model_retrain: bool = False,
    train_in_preopen: bool = False,
    train_years: int = 4,
    train_model_name: str = "qlib_lgbm",
    train_feature_set: str = "extended",
    label_horizon: int = 5,
    mlflow_root: str | None = None,
):
    start_time = time.time()
    blockers = []
    signal_date, execution_date = resolve_signal_and_execution_date(date, execution_date)
    resolved_paths = _resolve_ops_paths(
        execution_date=execution_date,
        daily_root=daily_root,
        output_dir=output_dir,
        report_dir=report_dir,
        db_path=db_path,
    )
    daily_root = resolved_paths["daily_root"]
    pre_open_paths = resolved_paths["stage_paths"]
    db_path = resolved_paths["db_path"]
    output_dir = resolved_paths["output_dir"]
    report_dir = resolved_paths["report_dir"]
    manifest_dir = resolved_paths["manifest_dir"]
    if real_sync:
        real_sync = _resolve_cli_path(real_sync)

    data_status = {}
    model_info = {}
    health_ok = False
    signal_basket_path = None
    signal_quality_summary = {}
    signal_quality_artifacts = {}
    assumptions = {
        "top_k": top_k,
        "min_trade": min_trade,
        "shadow_cash": shadow_cash,
        "real_cash": real_cash,
        "t1_rule": "new_buy_not_sellable_until_next_session",
        "execution_bucket": "after_sell_cash",
    }

    result = {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "data_status": data_status,
        "model_info": model_info,
        "shadow_plan_summary": {"status": "skipped", "signal_date": signal_date, "execution_date": execution_date},
        "real_plan_summary": {"status": "skipped", "signal_date": signal_date, "execution_date": execution_date},
        "signal_quality_summary": signal_quality_summary,
        "signal_basket_summary": {},
        "account_snapshots": {},
        "artifacts": {"account_db": db_path},
        "blockers": blockers,
        "blocked_symbols": [],
        "cash_utilization": {},
        "assumptions": assumptions,
        "next_action": None,
    }

    def finalize_report(report_summary=None):
        duration = time.time() - start_time
        result["duration_seconds"] = duration
        if report_summary is not None:
            result["report_summary"] = report_summary
        return result

    log_stage(
        "daily_workflow",
        "start",
        signal_date=signal_date,
        execution_date=execution_date,
        top_k=top_k,
        min_trade=min_trade,
        skip_update=skip_update,
        report_dir=report_dir,
    )
    if not skip_update:
        update_ok, update_report = update_data()
        if require_update_success and not update_ok:
            blockers.append("Explicit data refresh failed")
            data_status.update(
                {
                    "raw_latest": update_report.get("raw_latest"),
                    "qlib_latest": update_report.get("qlib_latest"),
                    "aligned": update_report.get("aligned", False),
                    "health_ok": False,
                }
            )
            if not no_report:
                report = DailyOpsReport.generate_pre_open_report(
                    signal_date=signal_date,
                    execution_date=execution_date,
                    data_status=data_status,
                    model_info=model_info,
                    shadow_plan_summary=result["shadow_plan_summary"],
                    real_plan_summary=result["real_plan_summary"],
                    duration_seconds=time.time() - start_time,
                    blockers=blockers,
                )
                report.artifacts["account_db"] = db_path
                report_path = DailyOpsReport.save(report, output_dir=report_dir)
                result["artifacts"]["report"] = report_path
                log_stage("report", "saved", path=report_path)
            result["next_action"] = "Fix data refresh before generating pre-open plans"
            return finalize_report()

    QlibAdapter().init_qlib()

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
                blockers.append("No model found")
                log_event("error", "model_resolution_failed", reason="no model found")
                result["next_action"] = "Train or register a production model before pre-open"
                return finalize_report()

    if not Path(model_path).exists():
        blockers.append("Model path missing")
        log_event("error", "model_path_missing", model_path=model_path)
        result["next_action"] = "Fix model_path or production manifest"
        return finalize_report()

    if train_in_preopen:
        train_end = pd.Timestamp(signal_date)
        train_start = (train_end - pd.DateOffset(years=train_years) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        train_cmd = [
            sys.executable,
            str(project_root / "scripts" / "run_train.py"),
            "--model",
            train_model_name,
            "--start",
            train_start,
            "--end",
            signal_date,
            "--infer_date",
            signal_date,
            "--label_horizon",
            str(label_horizon),
            "--feature_set",
            train_feature_set,
        ]
        if mlflow_root:
            train_cmd.extend(["--mlflow_root", str(mlflow_root)])
        log_stage("model_training", "start", train_start=train_start, train_end=signal_date, infer_date=signal_date)
        subprocess.check_call(train_cmd, cwd=str(project_root))
        log_stage("model_training", "done", train_start=train_start, train_end=signal_date, infer_date=signal_date)
    elif not disable_model_retrain:
        model_path = ModelScheduler.check_and_retrain(model_path, signal_date, retrain_freq_days=retrain_days)

    log_stage("model_ready", "done", model_path=model_path)

    model_info.update(
        {
            "model_path": model_path,
            "model_name": Path(model_path).name,
            "top_k": top_k,
        }
    )

    preview_manager = LiveManager(model_path=model_path)
    preview_manager.load_model()

    feature_config = preview_manager.model.model.feature_config
    model_info["feature_set"] = _resolve_model_feature_set(model_path, feature_config)

    try:
        health = assert_qlib_data_ready(signal_date, feature_config, universe="csi300")
        health_ok = True
    except DataReadinessError as readiness_error:
        health = readiness_error.report
        health_ok = False
        log_event("error", "data_readiness_failed", reason=str(readiness_error))

    data_status.update(
        {
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
    )
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

        if not no_report:
            report = DailyOpsReport.generate_pre_open_report(
                signal_date=signal_date,
                execution_date=execution_date,
                data_status=data_status,
                model_info=model_info,
                shadow_plan_summary=result["shadow_plan_summary"],
                real_plan_summary=result["real_plan_summary"],
                signal_quality_summary=signal_quality_summary,
                duration_seconds=time.time() - start_time,
                blockers=blockers,
            )
            report.artifacts["account_db"] = db_path
            report_path = DailyOpsReport.save(report, output_dir=report_dir)
            manifest_path = update_manifest(
                report_dir=manifest_dir,
                execution_date=execution_date,
                signal_date=signal_date,
                stage="pre_open",
                status=report.status.value,
                report_path=report_path,
                artifacts=report.artifacts,
                data_status=data_status,
                model_info=model_info,
                blockers=blockers,
            )
            result["artifacts"]["report"] = report_path
            result["artifacts"]["manifest"] = manifest_path
            archive_info = archive_daily_artifacts(
                execution_date=execution_date,
                signal_date=signal_date,
                stage="pre_open",
                artifacts=result["artifacts"],
                archive_root=daily_root,
            )
            digest = build_daily_summary_bundle(execution_date=execution_date, archive_root=daily_root)
            result["artifacts"]["daily_index"] = archive_info["index_path"]
            result["artifacts"]["daily_summary_md"] = digest.report_markdown_path
            result["artifacts"]["daily_summary_json"] = digest.report_json_path
            log_stage("report", "saved", path=report_path)
            log_stage("manifest", "saved", path=manifest_path)
        result["next_action"] = "Refresh data until readiness passes before pre-open"
        return finalize_report()

    if not skip_signal_quality_gate:
        signal_quality_snapshot = collect_signal_quality_snapshot(
            as_of_date=signal_date,
            signal_dir=daily_root,
            horizons=(1, 2, 3),
            recent_window=5,
        )
        signal_quality_summary = signal_quality_snapshot.summary
        result["signal_quality_summary"] = signal_quality_summary
        signal_quality_artifacts = write_signal_quality_outputs(
            signal_quality_snapshot,
            output_dir=output_dir,
            as_of_date=signal_date,
        )
        blockers.extend(build_signal_quality_blockers(signal_quality_summary, required_horizons=(1, 2, 3)))
        if blockers:
            log.error("Signal quality gate failed; refusing to continue daily planning")
            if not no_report:
                report = DailyOpsReport.generate_pre_open_report(
                    signal_date=signal_date,
                    execution_date=execution_date,
                    data_status=data_status,
                    model_info=model_info,
                    shadow_plan_summary=result["shadow_plan_summary"],
                    real_plan_summary=result["real_plan_summary"],
                    signal_quality_summary=signal_quality_summary,
                    duration_seconds=time.time() - start_time,
                    blockers=blockers,
                )
                report.artifacts.update(signal_quality_artifacts)
                report.artifacts["account_db"] = db_path
                report_path = DailyOpsReport.save(report, output_dir=report_dir)
                manifest_path = update_manifest(
                    report_dir=manifest_dir,
                    execution_date=execution_date,
                    signal_date=signal_date,
                    stage="pre_open",
                    status=report.status.value,
                    report_path=report_path,
                    artifacts=report.artifacts,
                    data_status=data_status,
                    model_info=model_info,
                    blockers=blockers,
                    summary={"signal_quality_gate": signal_quality_summary},
                )
                result["artifacts"].update(signal_quality_artifacts)
                result["artifacts"]["report"] = report_path
                result["artifacts"]["manifest"] = manifest_path
                archive_info = archive_daily_artifacts(
                    execution_date=execution_date,
                    signal_date=signal_date,
                    stage="pre_open",
                    artifacts=result["artifacts"],
                    archive_root=daily_root,
                )
                digest = build_daily_summary_bundle(execution_date=execution_date, archive_root=daily_root)
                result["artifacts"]["daily_index"] = archive_info["index_path"]
                result["artifacts"]["daily_summary_md"] = digest.report_markdown_path
                result["artifacts"]["daily_summary_json"] = digest.report_json_path
                log_stage("report", "saved", path=report_path)
                log_stage("manifest", "saved", path=manifest_path)
            result["next_action"] = "Investigate signal-quality blockers before trading"
            return finalize_report()

    preview_manager.strategy.top_k = top_k
    signal_basket = preview_manager.generate_signal_basket(
        signal_date,
        execution_date=execution_date,
        universe="csi300",
    )
    signal_basket_path = save_signal_basket(signal_basket, output_dir=output_dir, signal_date=signal_date)
    result["artifacts"]["signal_basket"] = signal_basket_path
    result["signal_basket_summary"] = extract_plan_summary(signal_basket, "target", signal_date, execution_date)
    log.info(f"Signal basket saved to {signal_basket_path}")

    shadow_account_name = "shadow"
    shadow_sim = ShadowSimulator(account_name=shadow_account_name, initial_cash=shadow_cash, db_path=db_path)
    execution_audit_rows = []

    if shadow_sim.initialize_if_needed(signal_date):
        log_stage("shadow_account", "initialized", signal_date=signal_date)
    else:
        plan_path = find_plan_path_for_execution_date(
            execution_date=signal_date,
            account_name=shadow_account_name,
            daily_root=daily_root,
        )
        if plan_path and plan_path.exists():
            log_stage("shadow_account", "simulate_previous_plan", plan_path=str(plan_path), signal_date=signal_date)
            execution_audit_rows = shadow_sim.simulate_execution(
                str(plan_path),
                signal_date,
                volume_participation_cap=0.1,
            ).to_dict(orient="records")
        else:
            log_stage("shadow_account", "skip_simulation", reason="previous plan missing", plan_path=str(plan_path) if plan_path else None)

    real_account_name = "real"
    real_account = RealAccount(db_path=db_path, account_name=real_account_name)
    if real_sync:
        log_stage("real_sync", "start", path=real_sync, signal_date=signal_date)
        sync_real_account_from_csv(real_account, real_account_name, real_sync, signal_date)
        log_stage("real_sync", "done", path=real_sync)
    ensure_real_account_seeded(real_account, signal_date, real_cash, real_account_name)

    manager_shadow = LiveManager(model_path=model_path, db_path=db_path, output_dir=output_dir)
    manager_shadow.strategy.top_k = top_k
    manager_shadow.planner.min_trade_amount = min_trade
    manager_shadow.real_account = shadow_sim.account
    log_stage("plan_generation", "start", account=shadow_account_name)
    plan_shadow = manager_shadow.run_daily_plan(signal_date, account_name=shadow_account_name, execution_date=execution_date)
    shadow_summary = extract_plan_summary(plan_shadow, shadow_account_name, signal_date, execution_date)
    result["shadow_plan_summary"] = shadow_summary
    result["account_snapshots"][shadow_account_name] = extract_account_snapshot(
        shadow_sim.account,
        date=signal_date,
        account_name=shadow_account_name,
    )
    log_plan_summary(shadow_summary)

    manager_real = LiveManager(model_path=model_path, db_path=db_path, output_dir=output_dir)
    manager_real.strategy.top_k = top_k
    manager_real.planner.min_trade_amount = min_trade
    manager_real.real_account = real_account
    log_stage("plan_generation", "start", account=real_account_name)
    plan_real = manager_real.run_daily_plan(signal_date, account_name=real_account_name, execution_date=execution_date)
    real_summary = extract_plan_summary(plan_real, real_account_name, signal_date, execution_date)
    result["real_plan_summary"] = real_summary
    result["account_snapshots"][real_account_name] = extract_account_snapshot(
        real_account,
        date=signal_date,
        account_name=real_account_name,
    )
    log_plan_summary(real_summary)

    shadow_intents = build_order_intents(
        plan_shadow,
        signal_date=signal_date,
        execution_date=execution_date,
        account_name=shadow_account_name,
        model_info=model_info,
        assumptions=assumptions,
    )
    real_intents = build_order_intents(
        plan_real,
        signal_date=signal_date,
        execution_date=execution_date,
        account_name=real_account_name,
        model_info=model_info,
        assumptions=assumptions,
    )
    shadow_intents_path = save_order_intents(
        shadow_intents,
        output_dir=output_dir,
        execution_date=execution_date,
        account_name=shadow_account_name,
    )
    real_intents_path = save_order_intents(
        real_intents,
        output_dir=output_dir,
        execution_date=execution_date,
        account_name=real_account_name,
    )
    result["artifacts"]["shadow_order_intents"] = shadow_intents_path
    result["artifacts"]["real_order_intents"] = real_intents_path

    result["cash_utilization"] = {
        shadow_account_name: {
            "planned_value": shadow_summary.get("total_value", 0.0),
            "initial_cash": shadow_cash,
            "planned_ratio": round(shadow_summary.get("total_value", 0.0) / shadow_cash, 6) if shadow_cash else None,
        },
        real_account_name: {
            "planned_value": real_summary.get("total_value", 0.0),
            "initial_cash": real_cash,
            "planned_ratio": round(real_summary.get("total_value", 0.0) / real_cash, 6) if real_cash else None,
        },
    }

    duration = time.time() - start_time
    training_summary_payload = training_contract_payload(
        training_mode=str(model_info.get("training_mode") or "qlib_native"),
        train_end_requested=signal_date if train_in_preopen else None,
        train_end_effective=signal_date if train_in_preopen else None,
        infer_date=signal_date,
        last_train_sample_date=signal_date if train_in_preopen else None,
        max_label_date_used=signal_date if train_in_preopen else None,
        is_label_mature_at_infer_time=True if train_in_preopen else None,
        mlflow_root=mlflow_root,
    )
    unified_paths = unified_run_artifacts(report_dir)
    suspicious_rows = [
        row for row in execution_audit_rows
        if row.get("status") != "filled" or row.get("one_word_limit") or row.get("limit_state") not in {None, "", "unknown", "none"}
    ]
    result["artifacts"]["config_snapshot"] = write_json(unified_paths["config_snapshot"], {
        "signal_date": signal_date,
        "execution_date": execution_date,
        "top_k": top_k,
        "min_trade": min_trade,
        "model_path": model_path,
        "training_mode": training_summary_payload["training_mode"],
        "volume_participation_cap": 0.1,
        "fill_price_rule": "plan_price_plus_slippage",
    })
    result["artifacts"]["training_summary"] = write_json(unified_paths["training_summary"], training_summary_payload)
    result["artifacts"]["execution_audit"] = write_csv(unified_paths["execution_audit"], execution_audit_rows)
    result["artifacts"]["suspicious_trades"] = write_csv(unified_paths["suspicious_trades"], suspicious_rows)
    result["artifacts"]["metrics"] = write_json(unified_paths["metrics"], {
        "shadow_plan_total_value": shadow_summary.get("total_value"),
        "real_plan_total_value": real_summary.get("total_value"),
        "shadow_reject_count": len([row for row in execution_audit_rows if row.get("status") != "filled"]),
        "suspicious_trade_count": len(suspicious_rows),
    })

    if not no_report:
        report = DailyOpsReport.generate_pre_open_report(
            signal_date=signal_date,
            execution_date=execution_date,
            data_status=data_status,
            model_info=model_info,
            shadow_plan_summary=shadow_summary,
            real_plan_summary=real_summary,
            signal_quality_summary=signal_quality_summary,
            duration_seconds=duration,
            blockers=blockers,
            notes=[
                f"top_k={top_k}",
                f"min_trade={min_trade}",
                f"shadow_cash={shadow_cash}",
                f"real_cash={real_cash}",
            ],
        )
        plans_dir = pre_open_paths.plans_dir if output_dir == str(pre_open_paths.root) else Path(output_dir)
        report.artifacts["shadow_plan"] = str(plans_dir / f"plan_{signal_date}_{shadow_account_name}.csv")
        report.artifacts["real_plan"] = str(plans_dir / f"plan_{signal_date}_{real_account_name}.csv")
        report.artifacts["shadow_real_sync_template"] = str(plans_dir / f"real_sync_template_{signal_date}_{shadow_account_name}.csv")
        report.artifacts["real_real_sync_template"] = str(plans_dir / f"real_sync_template_{signal_date}_{real_account_name}.csv")
        report.artifacts["shadow_order_intents"] = shadow_intents_path
        report.artifacts["real_order_intents"] = real_intents_path
        report.artifacts["account_db"] = db_path
        if signal_basket_path:
            report.artifacts["signal_basket"] = signal_basket_path
        report.artifacts.update(signal_quality_artifacts)
        report.artifacts.update(result["artifacts"])

        report_path = DailyOpsReport.save(report, output_dir=report_dir)
        manifest_path = update_manifest(
            report_dir=manifest_dir,
            execution_date=execution_date,
            signal_date=signal_date,
            stage="pre_open",
            status=report.status.value,
            report_path=report_path,
            artifacts=report.artifacts,
            data_status=data_status,
            model_info=model_info,
            blockers=blockers,
            notes=report.notes,
            summary={
                "shadow_plan": shadow_summary,
                "real_plan": real_summary,
                "signal_quality_gate": signal_quality_summary,
                "account_snapshots": result["account_snapshots"],
                "training_summary": training_summary_payload,
                "execution_metrics": {
                    "shadow_reject_count": len([row for row in execution_audit_rows if row.get("status") != "filled"]),
                    "suspicious_trade_count": len(suspicious_rows),
                },
            },
        )
        result["artifacts"].update(report.artifacts)
        result["artifacts"]["report"] = report_path
        result["artifacts"]["manifest"] = manifest_path
        archive_info = archive_daily_artifacts(
            execution_date=execution_date,
            signal_date=signal_date,
            stage="pre_open",
            artifacts=result["artifacts"],
            archive_root=daily_root,
        )
        digest = build_daily_summary_bundle(execution_date=execution_date, archive_root=daily_root)
        result["artifacts"]["daily_index"] = archive_info["index_path"]
        result["artifacts"]["daily_summary_md"] = digest.report_markdown_path
        result["artifacts"]["daily_summary_json"] = digest.report_json_path
        log_stage("report", "saved", path=report_path)
        log_stage("manifest", "saved", path=manifest_path)

    result["next_action"] = "Review blocked symbols and convert executable plans into order intents"
    log_stage("daily_workflow", "done", duration_seconds=duration, blockers=len(blockers))
    return finalize_report(
        {
            "shadow_plan": shadow_summary,
            "real_plan": real_summary,
            "signal_quality_gate": signal_quality_summary,
        }
    )


def main():
    parser = argparse.ArgumentParser(description="Run Daily Trading Workflow (Real + Shadow)")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="Signal date or target execution date. If a future trading day is given without --execution_date, it is treated as execution date and the previous trading day is used as signal_date.")
    parser.add_argument("--execution_date", type=str, help="Execution Date (YYYY-MM-DD). Defaults to next trading day after signal date")
    parser.add_argument("--model_path", type=str, help="Path to model directory")
    parser.add_argument("--real_sync", type=str, help="Path to CSV file with Real Account state (cash, positions)")
    parser.add_argument("--db_path", help="SQLite account database path (default: data/meta/real_account.db)")
    parser.add_argument("--output_dir", help="Directory to write pre-open artifacts (default: <daily_root>/<execution_date>/pre_open)")
    parser.add_argument("--report_dir", help="Directory to write structured JSON reports (default: <daily_root>/<execution_date>/pre_open/reports)")
    parser.add_argument("--daily_root", help="Root directory for dated daily artifacts (default: daily)")
    parser.add_argument("--skip_update", action="store_true", help="Skip data update")
    parser.add_argument("--require_update_success", action="store_true", help="Abort if the explicit data refresh step fails")
    parser.add_argument("--shadow_cash", type=float, default=1_000_000.0, help="Initial cash for Shadow Account")
    parser.add_argument("--real_cash", type=float, default=20_000.0, help="Initial cash for Real Account if no state exists")
    parser.add_argument("--retrain_days", type=int, default=7, help="Model max age in days before retraining")
    parser.add_argument("--top_k", type=int, default=5, help="Number of stocks to select in strategy")
    parser.add_argument("--min_trade", type=int, default=5000, help="Minimum trade amount in RMB")
    parser.add_argument("--skip_signal_quality_gate", action="store_true", help="Bypass the pre-open signal-quality readiness gate")
    parser.add_argument("--no_report", action="store_true", help="Skip generating the structured report")
    parser.add_argument("--disable_model_retrain", action="store_true", help="Do not trigger age-based retrain inside preopen")
    parser.add_argument("--train_in_preopen", action="store_true", help="Train the model inside preopen before signal generation")
    parser.add_argument("--train_years", type=int, default=4, help="Trailing train window in years when --train_in_preopen is set")
    parser.add_argument("--train_model_name", type=str, default="qlib_lgbm", help="Model name used by preopen training")
    parser.add_argument("--train_feature_set", type=str, default="extended", help="Feature set used by preopen training")
    parser.add_argument("--label_horizon", type=int, default=5, help="Label horizon for maturity-safe preopen training")
    parser.add_argument("--mlflow_root", type=str, help="Optional MLflow tracking root used only for future preopen training runs")
    args = parser.parse_args()
    run_preopen_workflow(**vars(args))


if __name__ == "__main__":
    main()
