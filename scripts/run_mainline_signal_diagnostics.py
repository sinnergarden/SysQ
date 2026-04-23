#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.model.zoo.qlib_native import QlibNativeModel
from qsys.research.ablation import summarize_rolling_metrics
from qsys.research.mainline import MAINLINE_OBJECTS
from qsys.research.rolling import build_rolling_windows, compute_window_metrics, snapshot_train_window
from qsys.research.signal_block_diagnostics import (
    TARGET_OBJECTS,
    build_block_mapping,
    build_diagnostic_summary,
    build_drop_one_feature_configs,
    write_outputs,
)
from qsys.research.spec import V1_IMPL1_FIXED_LABEL_HORIZON
from scripts.run_backtest import build_backtest_lineage, load_training_snapshot
from scripts.run_trimmed_ablation import QLIB_CONFIG, _run_rolling


@click.command(name="run_mainline_signal_diagnostics")
@click.option("--start", default="2025-01-02")
@click.option("--end", default="2026-03-20")
@click.option("--output_dir", default="experiments/mainline_signal_diagnostics")
@click.option("--skip_existing/--no_skip_existing", default=True)
def main(start: str, end: str, output_dir: str, skip_existing: bool) -> None:
    out_dir = (project_root / output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rolling_root = out_dir / "rolling"
    rolling_root.mkdir(parents=True, exist_ok=True)

    mapping = build_block_mapping(TARGET_OBJECTS)
    drop_one_configs = build_drop_one_feature_configs(mapping)

    experiment_rows: list[dict] = []
    for object_name in TARGET_OBJECTS:
        spec = MAINLINE_OBJECTS[object_name]
        baseline_model_path = project_root / "data" / "models" / spec.model_name
        baseline_snapshot = load_training_snapshot(baseline_model_path)
        train_start, train_end = snapshot_train_window(baseline_snapshot)
        infer_date = pd.Timestamp(train_end or end).strftime("%Y-%m-%d")

        for block_name, feature_config in drop_one_configs[object_name].items():
            model_name = f"qlib_lgbm_{object_name}_drop_{block_name}"
            model_path = project_root / "data" / "models" / model_name
            if not (skip_existing and (model_path / "config_snapshot.json").exists()):
                model_path = _train_custom_model(
                    model_name=model_name,
                    feature_config=feature_config,
                    base_object_name=object_name,
                    train_start=train_start,
                    train_end=train_end,
                    infer_date=infer_date,
                    dropped_block_name=block_name,
                )
            summary = _run_drop_one_rolling(model_path, model_name, start=start, end=end, out_dir=rolling_root)
            experiment_rows.append(
                {
                    "mainline_object_name": object_name,
                    "diagnostic_mode": "drop_one",
                    "block_name": block_name,
                    **{k: summary.get(k) for k in [
                        "rolling_total_return_mean",
                        "rolling_rankic_mean",
                        "rolling_rankic_std",
                        "rolling_max_drawdown_worst",
                    ]},
                    "notes": f"model_name={model_name}",
                }
            )

    summary = build_diagnostic_summary(
        block_mapping=mapping,
        experiment_summaries=experiment_rows,
        project_root=project_root,
    )
    written = write_outputs(output_dir=out_dir, block_mapping=mapping, diagnostic_summary=summary)
    experiment_path = out_dir / "drop_one_experiment_rows.csv"
    pd.DataFrame(experiment_rows).to_csv(experiment_path, index=False)
    print(f"block_mapping={written['block_mapping']}")
    print(f"block_diagnostic_summary_csv={written['block_diagnostic_summary_csv']}")
    print(f"block_diagnostic_summary_md={written['block_diagnostic_summary_md']}")
    print(f"drop_one_experiment_rows={experiment_path}")



def _ensure_single_kernel_qlib() -> None:
    try:
        from qlib.config import C

        if hasattr(C, "_config") and isinstance(C._config, dict):
            C._config["registered"] = getattr(C, "_registered", False)
            C._config["kernels"] = 1
    except Exception:
        pass



def _train_custom_model(
    *,
    model_name: str,
    feature_config: list[str],
    base_object_name: str,
    train_start: str | None,
    train_end: str | None,
    infer_date: str,
    dropped_block_name: str,
) -> Path:
    if train_start is None or train_end is None:
        raise ValueError(f"Missing train window for {base_object_name}")
    adapter = QlibAdapter()
    adapter.init_qlib()
    _ensure_single_kernel_qlib()
    model = QlibNativeModel(name=model_name, model_config=QLIB_CONFIG, feature_config=feature_config)
    model.fit("csi300", train_start, train_end, infer_date=infer_date, label_horizon=5)
    root = cfg.get_path("root")
    if root is None:
        raise ValueError("Root path not configured")
    save_path = root / "models" / model_name
    model.save(save_path)
    baseline_snapshot = load_training_snapshot(project_root / "data" / "models" / MAINLINE_OBJECTS[base_object_name].model_name)
    lineage = build_backtest_lineage(baseline_snapshot)
    snapshot = {
        "input_mode": "signal_block_drop_one",
        "feature_set": None,
        "bundle_id": MAINLINE_OBJECTS[base_object_name].bundle_id,
        "mainline_object_name": base_object_name,
        "legacy_feature_set_alias": MAINLINE_OBJECTS[base_object_name].legacy_feature_set_alias,
        "factor_variants": [f"{base_object_name}@drop_one:{dropped_block_name}"],
        "bundle_resolution_status": lineage.get("bundle_resolution_status"),
        "object_layer_status": "signal_block_drop_one",
        "lineage_status": "signal_block_drop_one",
        "label_spec": {"label_type": "forward_return", "label_horizon": V1_IMPL1_FIXED_LABEL_HORIZON},
        "split_spec": {"train_start": train_start, "train_end": train_end, "train_end_effective": train_end},
        "model_spec": {"model_name": model_name, "model_type": "qlib_lgbm", "model_path": str(save_path)},
        "strategy_spec": {"strategy_type": "rank_topk", "top_k": 5, "rebalance_freq": "weekly"},
        "feature_config": feature_config,
        "signal_block_diagnostic": {
            "diagnostic_mode": "drop_one",
            "base_object_name": base_object_name,
            "dropped_block_name": dropped_block_name,
        },
    }
    (save_path / "config_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return save_path


def _run_drop_one_rolling(model_path: Path, object_name: str, *, start: str, end: str, out_dir: Path) -> dict:
    adapter = QlibAdapter()
    adapter.init_qlib()
    _ensure_single_kernel_qlib()
    return _run_rolling(model_path, object_name, start=start, end=end, out_dir=out_dir)


if __name__ == "__main__":
    main()
