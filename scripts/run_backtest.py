"""
Primary backtest entrypoint.

Purpose:
- run a historical backtest for a saved model artifact
- save daily result csv + structured backtest report

Typical usage:
- python scripts/run_backtest.py --model_path data/models/qlib_lgbm_phase123_extended --start 2025-01-01 --end 2026-03-20 --top_k 5

Key args:
- --model_path: model directory to evaluate
- --universe: backtest universe
- --start / --end: backtest window
- --top_k: portfolio breadth
"""

import json
import sys
import time
from pathlib import Path

import click
import yaml

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.reports.backtest import BacktestReport
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.research import ExperimentSpec
from qsys.research.spec import (
    SUPPORTED_FEATURE_SETS,
    SUPPORTED_FREQUENCIES,
    SUPPORTED_LABEL_TYPES,
    SUPPORTED_MODEL_TYPES,
    SUPPORTED_REBALANCE_MODES,
    SUPPORTED_STRATEGY_TYPES,
    V1_IMPL1_FIXED_LABEL_HORIZON,
)
from qsys.strategy.engine import DEFAULT_TOP_K
from qsys.utils.logger import log


@click.command()
@click.option("--model_path", type=str, default=None, help="Model directory. Defaults to data/models/qlib_lgbm_phase123")
@click.option("--universe", type=str, default="csi300", help="Backtest universe")
@click.option("--start", type=str, default="2022-01-01", help="Backtest start date")
@click.option("--end", type=str, default="2022-03-01", help="Backtest end date")
@click.option("--top_k", type=int, default=DEFAULT_TOP_K, help="Top K positions")
@click.option("--feature_set", type=str, default=None, help="Research feature set. Defaults to model artifact inference in v1")
@click.option("--model_type", type=str, default=None, help="Research model type. Defaults to model artifact inference in v1")
@click.option("--label_type", type=str, default="forward_return", help="Research label type for v1")
@click.option("--strategy_type", type=str, default="rank_topk", help="Research strategy type for v1")
@click.option("--rebalance_mode", type=str, default="full_rebalance", help="Research rebalance mode for v1")
@click.option("--rebalance_freq", type=str, default="weekly", help="Research rebalance frequency for v1")
@click.option("--inference_freq", type=str, default="daily", help="Research inference frequency for v1")
@click.option("--retrain_freq", type=str, default="weekly", help="Research retrain frequency for v1")
@click.option("--label_horizon", type=str, default=V1_IMPL1_FIXED_LABEL_HORIZON, help="Label horizon used by current signal metrics evaluator")
@click.option("--min_signal_threshold", type=float, default=0.0, help="Minimum signal threshold for cash-gated rank selection")
@click.option("--min_selected_count", type=int, default=1, help="Minimum eligible names required before deploying capital in cash-gated rank selection")
@click.option("--allow_empty_portfolio/--no_allow_empty_portfolio", default=True, help="Allow cash-gated strategy to stay empty when threshold gate fails")
@click.option("--min_trade_buffer_ratio", type=float, default=0.0, help="Skip order staging when target/current weight gap is below this ratio of equity")
def main(model_path, universe, start, end, top_k, feature_set, model_type, label_type, strategy_type, rebalance_mode, rebalance_freq, inference_freq, retrain_freq, label_horizon, min_signal_threshold, min_selected_count, allow_empty_portfolio, min_trade_buffer_ratio):
    start_time = time.time()
    root_path = cfg.get_path("root")
    if root_path is None:
        raise ValueError("Root path not configured")

    model_dir = Path(model_path) if model_path else root_path / "models" / "qlib_lgbm"
    resolved_feature_set, feature_set_source = _resolve_feature_set(model_dir, feature_set)
    resolved_model_type, model_type_source = _resolve_model_type(model_dir, model_type)
    resolved_label_type = _resolve_supported_value("label_type", label_type, SUPPORTED_LABEL_TYPES)
    resolved_strategy_type = _resolve_supported_value("strategy_type", strategy_type, SUPPORTED_STRATEGY_TYPES)
    resolved_rebalance_mode = _resolve_supported_value("rebalance_mode", rebalance_mode, SUPPORTED_REBALANCE_MODES)
    resolved_rebalance_freq = _resolve_supported_value("rebalance_freq", rebalance_freq, SUPPORTED_FREQUENCIES)
    resolved_inference_freq = _resolve_supported_value("inference_freq", inference_freq, SUPPORTED_FREQUENCIES)
    resolved_retrain_freq = _resolve_supported_value("retrain_freq", retrain_freq, SUPPORTED_FREQUENCIES)

    strategy_params = {
        "min_signal_threshold": min_signal_threshold,
        "min_selected_count": min_selected_count,
        "allow_empty_portfolio": allow_empty_portfolio,
        "min_trade_buffer_ratio": min_trade_buffer_ratio,
    }

    experiment_spec = ExperimentSpec(
        run_name=f"backtest_{model_dir.name}_{start}_{end}",
        feature_set=resolved_feature_set,
        model_type=resolved_model_type,
        label_type=resolved_label_type,
        strategy_type=resolved_strategy_type,
        universe=universe,
        output_dir=str(root_path / "experiments"),
        top_k=top_k,
        label_horizon=label_horizon,
        rebalance_mode=resolved_rebalance_mode,
        rebalance_freq=resolved_rebalance_freq,
        inference_freq=resolved_inference_freq,
        retrain_freq=resolved_retrain_freq,
        strategy_params=strategy_params,
    )
    engine = BacktestEngine(
        model_dir,
        universe=universe,
        start_date=start,
        end_date=end,
        top_k=top_k,
        strategy_type=resolved_strategy_type,
        label_horizon=label_horizon,
        strategy_params=strategy_params,
    )
    res = engine.run()

    if res.empty:
        log.error("Backtest produced no results")
        return

    save_dir = root_path / "experiments"
    save_dir.mkdir(parents=True, exist_ok=True)
    daily_path = save_dir / "backtest_result.csv"
    res.to_csv(daily_path, index=False)
    written = engine.save_report(save_dir, prefix="backtest")

    log.info(f"Backtest daily result saved to {daily_path}")
    for name, path in written.items():
        log.info(f"Backtest {name} saved to {path}")

    duration = time.time() - start_time
    report = BacktestReport.from_backtest_result(
        result_df=res,
        model_path=str(model_dir),
        start_date=start,
        end_date=end,
        top_k=top_k,
        universe=universe,
        duration_seconds=duration,
        daily_result_path=str(daily_path),
        experiment_spec=experiment_spec.to_dict(),
    )

    unified_paths = unified_run_artifacts(save_dir)
    training_summary_path = model_dir / "training_summary.json"
    training_summary = {}
    if training_summary_path.exists():
        training_summary = json.loads(training_summary_path.read_text(encoding="utf-8"))
    metrics_payload = {}
    for section in report.sections:
        if section.name == "Performance":
            metrics_payload = dict(section.metrics)
            break
    report.artifacts["config_snapshot"] = write_json(
        unified_paths["config_snapshot"],
        {
            **experiment_spec.to_dict(),
            "model_path": str(model_dir),
            "start": start,
            "end": end,
            "spec_source": "explicit_cli_plus_artifact_inference_v1",
            "spec_status": "cli_driven_with_artifact_fallbacks_v1",
            "spec_inputs": {
                "feature_set": _input_source_payload(feature_set, resolved_feature_set, feature_set_source),
                "model_type": _input_source_payload(model_type, resolved_model_type, model_type_source),
                "label_type": _input_source_payload(label_type, resolved_label_type, "explicit_cli"),
                "strategy_type": _input_source_payload(strategy_type, resolved_strategy_type, "explicit_cli"),
                "rebalance_mode": _input_source_payload(rebalance_mode, resolved_rebalance_mode, "explicit_cli"),
                "rebalance_freq": _input_source_payload(rebalance_freq, resolved_rebalance_freq, "explicit_cli"),
                "inference_freq": _input_source_payload(inference_freq, resolved_inference_freq, "explicit_cli"),
                "retrain_freq": _input_source_payload(retrain_freq, resolved_retrain_freq, "explicit_cli"),
                "label_horizon": _input_source_payload(label_horizon, label_horizon, "explicit_cli"),
            },
        },
    )
    report.artifacts["training_summary"] = write_json(unified_paths["training_summary"], training_summary)
    report.artifacts["signal_metrics"] = write_json(unified_paths["signal_metrics"], engine.last_signal_metrics or {"status": "not_available_in_flow", "label_horizon": label_horizon})
    report.artifacts["group_returns"] = write_csv(
        unified_paths["group_returns"],
        engine.last_group_returns.to_dict(orient="records") if not engine.last_group_returns.empty else [],
        columns=["date", "group", "mean_return", "nav", "label_horizon"],
    )
    report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
    report.artifacts["suspicious_trades"] = write_csv(unified_paths["suspicious_trades"], [])
    report.artifacts["metrics"] = write_json(unified_paths["metrics"], metrics_payload)
    report.artifacts["exposure_summary"] = write_json(unified_paths["exposure_summary"], engine.last_exposure_summary or {"status": "not_available"})
    report.artifacts["exposure_timeseries"] = write_csv(
        unified_paths["exposure_timeseries"],
        engine.last_exposure_timeseries.to_dict(orient="records") if not engine.last_exposure_timeseries.empty else [],
        columns=["date", "metric", "value"],
    )
    report.artifacts["selection_daily"] = write_csv(
        unified_paths["selection_daily"],
        engine.last_selection_daily.to_dict(orient="records") if not engine.last_selection_daily.empty else [],
        columns=["date", "instrument", "signal_value", "target_weight", "selected_rank"],
    )

    report_path = BacktestReport.save(report)
    log.info(f"Structured report saved to {report_path}")

    print("\n" + "=" * 60)
    print(report.to_markdown())
    print("=" * 60)


def _resolve_supported_value(field_name: str, value: str, supported_values: set[str]) -> str:
    if value not in supported_values:
        supported = ", ".join(sorted(supported_values))
        raise click.BadParameter(f"{field_name}={value} is not_supported_in_v1; supported values: {supported}", param_hint=f"--{field_name}")
    return value


def _infer_model_type_from_artifact(model_dir: Path) -> str | None:
    return next((candidate for candidate in sorted(SUPPORTED_MODEL_TYPES, key=len, reverse=True) if model_dir.name.startswith(candidate)), None)


def _infer_feature_set_from_artifact(model_dir: Path) -> str | None:
    meta_path = model_dir / "meta.yaml"
    if meta_path.exists():
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except Exception:
            meta = {}
        feature_set = meta.get("feature_set")
        if feature_set in SUPPORTED_FEATURE_SETS:
            return feature_set
    for candidate in sorted(SUPPORTED_FEATURE_SETS, key=len, reverse=True):
        if candidate == "baseline":
            continue
        if candidate in model_dir.name:
            return candidate
    if "margin_extended" in model_dir.name:
        return "phase123"
    return "baseline"


def _resolve_model_type(model_dir: Path, cli_value: str | None) -> tuple[str, str]:
    if cli_value is not None:
        return _resolve_supported_value("model_type", cli_value, SUPPORTED_MODEL_TYPES), "explicit_cli"
    inferred = _infer_model_type_from_artifact(model_dir)
    if inferred is None:
        raise click.BadParameter("model_type could not be inferred from model artifact; pass --model_type explicitly", param_hint="--model_type")
    return inferred, "artifact_name_inference"


def _resolve_feature_set(model_dir: Path, cli_value: str | None) -> tuple[str, str]:
    if cli_value is not None:
        return _resolve_supported_value("feature_set", cli_value, SUPPORTED_FEATURE_SETS), "explicit_cli"
    inferred = _infer_feature_set_from_artifact(model_dir)
    if inferred is None:
        raise click.BadParameter("feature_set could not be inferred from model artifact; pass --feature_set explicitly", param_hint="--feature_set")
    return inferred, "artifact_meta_or_name_inference"


def _input_source_payload(raw_value: str | None, resolved_value: str, source: str) -> dict[str, str | None]:
    return {
        "input": raw_value,
        "resolved": resolved_value,
        "source": source,
    }


if __name__ == "__main__":
    main()
