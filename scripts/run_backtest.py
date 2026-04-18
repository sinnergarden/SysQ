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
from typing import Any

import click
import yaml

# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.backtest import BacktestEngine
from qsys.config import cfg
from qsys.reports.backtest import BacktestReport
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.research import ExperimentSpec, decision_payload, resolve_mainline_object_name, resolve_subject_decision
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
@click.pass_context
def main(ctx, model_path, universe, start, end, top_k, feature_set, model_type, label_type, strategy_type, rebalance_mode, rebalance_freq, inference_freq, retrain_freq, label_horizon, min_signal_threshold, min_selected_count, allow_empty_portfolio, min_trade_buffer_ratio):
    start_time = time.time()
    root_path = cfg.get_path("root")
    if root_path is None:
        raise ValueError("Root path not configured")

    model_dir = Path(model_path) if model_path else root_path / "models" / "qlib_lgbm"
    training_snapshot = load_training_snapshot(model_dir)
    lineage = build_backtest_lineage(training_snapshot)

    explicit = lambda name: ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE
    resolved_feature_set, feature_set_source = _resolve_feature_set(model_dir, feature_set, training_snapshot)
    resolved_model_type, model_type_source = _resolve_model_type(model_dir, model_type, training_snapshot)
    resolved_label_type, label_type_source = _resolve_label_type(label_type, training_snapshot, explicit("label_type"))
    resolved_strategy_type, strategy_type_source = _resolve_strategy_type(strategy_type, training_snapshot, explicit("strategy_type"))
    resolved_rebalance_mode, rebalance_mode_source = _resolve_rebalance_mode(rebalance_mode, training_snapshot, explicit("rebalance_mode"))
    resolved_rebalance_freq, rebalance_freq_source = _resolve_rebalance_freq(rebalance_freq, training_snapshot, explicit("rebalance_freq"))
    resolved_inference_freq, inference_freq_source = _resolve_inference_freq(inference_freq, training_snapshot, explicit("inference_freq"))
    resolved_retrain_freq, retrain_freq_source = _resolve_retrain_freq(retrain_freq, training_snapshot, explicit("retrain_freq"))

    strategy_params = {
        "min_signal_threshold": min_signal_threshold,
        "min_selected_count": min_selected_count,
        "allow_empty_portfolio": allow_empty_portfolio,
        "min_trade_buffer_ratio": min_trade_buffer_ratio,
    }
    strategy_spec = merge_strategy_spec(training_snapshot, {
        "strategy_type": resolved_strategy_type,
        "top_k": top_k,
        "strategy_params": strategy_params,
        "rebalance_mode": resolved_rebalance_mode,
        "rebalance_freq": resolved_rebalance_freq,
        "inference_freq": resolved_inference_freq,
        "retrain_freq": resolved_retrain_freq,
    })
    cost_spec = extract_cost_spec(training_snapshot, strategy_params)

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
        experiment_spec={
            **experiment_spec.to_dict(),
            "input_mode": lineage["input_mode"],
            "bundle_id": lineage["bundle_id"],
            "strategy_spec": strategy_spec,
            "cost_spec": cost_spec,
        },
    )
    if not hasattr(report, "model_info") or report.model_info is None:
        report.model_info = {}
    mainline_decision = resolve_subject_decision(
        subject_type="mainline_object",
        subject_ids=[lineage.get("mainline_object_name")],
    )
    run_decision = resolve_subject_decision(
        subject_type="experiment_run",
        subject_ids=[model_dir.name, str(model_dir)],
    )
    report.model_info.update({
        "input_mode": lineage["input_mode"],
        "bundle_id": lineage["bundle_id"],
        "factor_variants": lineage["factor_variants"],
        "lineage_status": lineage["lineage_status"],
        "strategy_type": strategy_spec.get("strategy_type"),
        "mainline_decision_status": decision_payload(mainline_decision).get("status"),
        "run_decision_status": decision_payload(run_decision).get("status"),
    })

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
    spec_source = "explicit_cli_plus_training_snapshot_v1" if training_snapshot.get("status") == "available" else "explicit_cli_plus_artifact_inference_v1"
    spec_status = "snapshot_driven_when_available_v1" if training_snapshot.get("status") == "available" else "cli_driven_with_artifact_fallbacks_v1"
    report.artifacts["config_snapshot"] = write_json(
        unified_paths["config_snapshot"],
        {
            **experiment_spec.to_dict(),
            "model_path": str(model_dir),
            "start": start,
            "end": end,
            "spec_source": spec_source,
            "spec_status": spec_status,
            "spec_inputs": {
                "feature_set": _input_source_payload(feature_set, resolved_feature_set, feature_set_source),
                "model_type": _input_source_payload(model_type, resolved_model_type, model_type_source),
                "label_type": _input_source_payload(label_type, resolved_label_type, label_type_source),
                "strategy_type": _input_source_payload(strategy_type, resolved_strategy_type, strategy_type_source),
                "rebalance_mode": _input_source_payload(rebalance_mode, resolved_rebalance_mode, rebalance_mode_source),
                "rebalance_freq": _input_source_payload(rebalance_freq, resolved_rebalance_freq, rebalance_freq_source),
                "inference_freq": _input_source_payload(inference_freq, resolved_inference_freq, inference_freq_source),
                "retrain_freq": _input_source_payload(retrain_freq, resolved_retrain_freq, retrain_freq_source),
                "label_horizon": _input_source_payload(label_horizon, label_horizon, "explicit_cli"),
            },
            "lineage": lineage,
            "label_spec": merge_label_spec(training_snapshot, resolved_label_type, label_horizon),
            "model_spec": merge_model_spec(training_snapshot, resolved_model_type, model_dir),
            "strategy_spec": strategy_spec,
            "cost_spec": cost_spec,
        },
    )
    report.artifacts["training_summary"] = write_json(unified_paths["training_summary"], training_summary)
    report.artifacts["signal_metrics"] = write_json(
        unified_paths["signal_metrics"],
        {
            **(engine.last_signal_metrics or {"status": "not_available_in_flow", "label_horizon": label_horizon}),
            "lineage": lineage,
            "label_spec": merge_label_spec(training_snapshot, resolved_label_type, label_horizon),
        },
    )
    report.artifacts["group_returns"] = write_csv(
        unified_paths["group_returns"],
        engine.last_group_returns.to_dict(orient="records") if not engine.last_group_returns.empty else [],
        columns=["date", "group", "mean_return", "nav", "label_horizon"],
    )
    report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
    report.artifacts["suspicious_trades"] = write_csv(unified_paths["suspicious_trades"], [])
    report.artifacts["metrics"] = write_json(
        unified_paths["metrics"],
        {
            **metrics_payload,
            "lineage": lineage,
            "strategy_spec": strategy_spec,
            "cost_spec": cost_spec,
        },
    )
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
    report.artifacts["decisions"] = write_json(
        unified_paths["decisions"],
        {
            "mainline_object": decision_payload(mainline_decision),
            "experiment_run": decision_payload(run_decision),
            "lineage": lineage,
        },
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


def load_training_snapshot(model_dir: Path) -> dict[str, Any]:
    snapshot_path = model_dir / "config_snapshot.json"
    if not snapshot_path.exists():
        return {
            "status": "not_found",
            "lineage_status": "legacy_or_incomplete_lineage",
            "snapshot_path": str(snapshot_path),
        }
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload["status"] = "available"
        payload["snapshot_path"] = str(snapshot_path)
        payload.setdefault("lineage_status", payload.get("object_layer_status") or "snapshot_available")
        return payload
    except Exception as exc:
        return {
            "status": "invalid",
            "lineage_status": "legacy_or_incomplete_lineage",
            "snapshot_path": str(snapshot_path),
            "error": str(exc),
        }


def build_backtest_lineage(snapshot: dict[str, Any]) -> dict[str, Any]:
    feature_set = snapshot.get("feature_set")
    bundle_id = snapshot.get("bundle_id")
    mainline_object_name = snapshot.get("mainline_object_name") or resolve_mainline_object_name(feature_set=feature_set, bundle_id=bundle_id)
    return {
        "input_mode": snapshot.get("input_mode") or "feature_set",
        "feature_set": feature_set,
        "bundle_id": bundle_id,
        "mainline_object_name": mainline_object_name,
        "legacy_feature_set_alias": snapshot.get("legacy_feature_set_alias") or feature_set,
        "factor_variants": list(snapshot.get("factor_variants") or []),
        "bundle_resolution_status": snapshot.get("bundle_resolution_status") or "legacy_or_incomplete_lineage",
        "object_layer_status": snapshot.get("object_layer_status") or "legacy_or_incomplete_lineage",
        "lineage_status": snapshot.get("lineage_status") or "legacy_or_incomplete_lineage",
        "snapshot_path": snapshot.get("snapshot_path"),
    }


def merge_label_spec(snapshot: dict[str, Any], resolved_label_type: str, label_horizon: str) -> dict[str, Any]:
    label_spec = dict(snapshot.get("label_spec") or {})
    label_spec.setdefault("label_type", resolved_label_type)
    label_spec.setdefault("label_horizon", label_horizon)
    return label_spec


def merge_model_spec(snapshot: dict[str, Any], resolved_model_type: str, model_dir: Path) -> dict[str, Any]:
    model_spec = dict(snapshot.get("model_spec") or {})
    model_spec.setdefault("model_type", resolved_model_type)
    model_spec.setdefault("model_name", model_dir.name)
    model_spec.setdefault("model_path", str(model_dir))
    return model_spec


def merge_strategy_spec(snapshot: dict[str, Any], runtime_strategy_spec: dict[str, Any]) -> dict[str, Any]:
    strategy_spec = dict(snapshot.get("strategy_spec") or {})
    strategy_spec.update({key: value for key, value in runtime_strategy_spec.items() if value is not None})
    return strategy_spec


def extract_cost_spec(snapshot: dict[str, Any], strategy_params: dict[str, Any]) -> dict[str, Any]:
    cost_spec = dict(snapshot.get("cost_spec") or {})
    if not cost_spec:
        cost_spec = {"status": "not_available_in_training_snapshot"}
    cost_spec.setdefault("min_trade_buffer_ratio", strategy_params.get("min_trade_buffer_ratio"))
    return cost_spec


def _resolve_model_type(model_dir: Path, cli_value: str | None, snapshot: dict[str, Any]) -> tuple[str, str]:
    if cli_value is not None:
        return _resolve_supported_value("model_type", cli_value, SUPPORTED_MODEL_TYPES), "explicit_cli"
    snapshot_value = _nested_get(snapshot, ["model_spec", "model_type"])
    if snapshot_value in SUPPORTED_MODEL_TYPES:
        return snapshot_value, "training_snapshot"
    inferred = _infer_model_type_from_artifact(model_dir)
    if inferred is None:
        raise click.BadParameter("model_type could not be inferred from model artifact; pass --model_type explicitly", param_hint="--model_type")
    return inferred, "artifact_name_inference"


def _resolve_feature_set(model_dir: Path, cli_value: str | None, snapshot: dict[str, Any]) -> tuple[str, str]:
    if cli_value is not None:
        return _resolve_supported_value("feature_set", cli_value, SUPPORTED_FEATURE_SETS), "explicit_cli"
    snapshot_value = snapshot.get("feature_set")
    if snapshot_value in SUPPORTED_FEATURE_SETS:
        return snapshot_value, "training_snapshot"
    inferred = _infer_feature_set_from_artifact(model_dir)
    if inferred is None:
        raise click.BadParameter("feature_set could not be inferred from model artifact; pass --feature_set explicitly", param_hint="--feature_set")
    return inferred, "artifact_meta_or_name_inference"


def _resolve_label_type(cli_value: str, snapshot: dict[str, Any], cli_explicit: bool) -> tuple[str, str]:
    snapshot_value = _nested_get(snapshot, ["label_spec", "label_type"])
    if cli_explicit:
        return _resolve_supported_value("label_type", cli_value, SUPPORTED_LABEL_TYPES), "explicit_cli"
    if snapshot_value in SUPPORTED_LABEL_TYPES:
        return snapshot_value, "training_snapshot"
    return _resolve_supported_value("label_type", cli_value or "forward_return", SUPPORTED_LABEL_TYPES), "explicit_cli"


def _resolve_strategy_type(cli_value: str, snapshot: dict[str, Any], cli_explicit: bool) -> tuple[str, str]:
    snapshot_value = _nested_get(snapshot, ["strategy_spec", "strategy_type"])
    if cli_explicit:
        return _resolve_supported_value("strategy_type", cli_value, SUPPORTED_STRATEGY_TYPES), "explicit_cli"
    if snapshot_value in SUPPORTED_STRATEGY_TYPES:
        return snapshot_value, "training_snapshot"
    return _resolve_supported_value("strategy_type", cli_value or "rank_topk", SUPPORTED_STRATEGY_TYPES), "explicit_cli"


def _resolve_rebalance_mode(cli_value: str, snapshot: dict[str, Any], cli_explicit: bool) -> tuple[str, str]:
    snapshot_value = _nested_get(snapshot, ["strategy_spec", "rebalance_mode"])
    if cli_explicit:
        return _resolve_supported_value("rebalance_mode", cli_value, SUPPORTED_REBALANCE_MODES), "explicit_cli"
    if snapshot_value in SUPPORTED_REBALANCE_MODES:
        return snapshot_value, "training_snapshot"
    return _resolve_supported_value("rebalance_mode", cli_value or "full_rebalance", SUPPORTED_REBALANCE_MODES), "explicit_cli"


def _resolve_rebalance_freq(cli_value: str, snapshot: dict[str, Any], cli_explicit: bool) -> tuple[str, str]:
    snapshot_value = _nested_get(snapshot, ["strategy_spec", "rebalance_freq"])
    if cli_explicit:
        return _resolve_supported_value("rebalance_freq", cli_value, SUPPORTED_FREQUENCIES), "explicit_cli"
    if snapshot_value in SUPPORTED_FREQUENCIES:
        return snapshot_value, "training_snapshot"
    return _resolve_supported_value("rebalance_freq", cli_value or "weekly", SUPPORTED_FREQUENCIES), "explicit_cli"


def _resolve_inference_freq(cli_value: str, snapshot: dict[str, Any], cli_explicit: bool) -> tuple[str, str]:
    snapshot_value = _nested_get(snapshot, ["strategy_spec", "inference_freq"])
    if cli_explicit:
        return _resolve_supported_value("inference_freq", cli_value, SUPPORTED_FREQUENCIES), "explicit_cli"
    if snapshot_value in SUPPORTED_FREQUENCIES:
        return snapshot_value, "training_snapshot"
    return _resolve_supported_value("inference_freq", cli_value or "daily", SUPPORTED_FREQUENCIES), "explicit_cli"


def _resolve_retrain_freq(cli_value: str, snapshot: dict[str, Any], cli_explicit: bool) -> tuple[str, str]:
    snapshot_value = _nested_get(snapshot, ["strategy_spec", "retrain_freq"])
    if cli_explicit:
        return _resolve_supported_value("retrain_freq", cli_value, SUPPORTED_FREQUENCIES), "explicit_cli"
    if snapshot_value in SUPPORTED_FREQUENCIES:
        return snapshot_value, "training_snapshot"
    return _resolve_supported_value("retrain_freq", cli_value or "weekly", SUPPORTED_FREQUENCIES), "explicit_cli"


def _nested_get(payload: dict[str, Any], path: list[str]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _input_source_payload(raw_value: str | None, resolved_value: str, source: str) -> dict[str, str | None]:
    return {
        "input": raw_value,
        "resolved": resolved_value,
        "source": source,
    }


if __name__ == "__main__":
    main()
