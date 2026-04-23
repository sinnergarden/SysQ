from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from qsys.research.signal_block_diagnostics import (
    TARGET_OBJECTS,
    ALLOWED_LABELS,
    assign_signal_contribution_label,
    build_block_mapping,
    build_diagnostic_summary,
    build_drop_one_feature_configs,
)
from scripts.run_mainline_signal_diagnostics import main as signal_diagnostics_main


def test_block_mapping_contract_fields_and_two_objects_only() -> None:
    mapping = build_block_mapping()
    assert set(mapping.columns) == {
        "field_name",
        "mainline_object_name",
        "block_name",
        "source_family",
        "is_core_block",
        "notes",
    }
    assert set(mapping["mainline_object_name"]) == set(TARGET_OBJECTS)
    assert "feature_254" not in set(mapping["mainline_object_name"])
    assert "feature_254_absnorm" not in set(mapping["mainline_object_name"])
    assert "feature_254_absnorm_trimmed" not in set(mapping["mainline_object_name"])


def test_drop_one_configs_remove_only_selected_block() -> None:
    mapping = build_block_mapping(["feature_254_trimmed"])
    configs = build_drop_one_feature_configs(mapping)
    base_fields = mapping["field_name"].tolist()
    dropped = set(mapping[mapping["block_name"] == "semantic_context"]["field_name"])
    assert set(configs["feature_254_trimmed"]["semantic_context"]).isdisjoint(dropped)
    assert set(configs["feature_254_trimmed"]["semantic_context"]).issubset(set(base_fields))


def test_signal_contribution_label_regression() -> None:
    baseline = {"rolling_rankic_mean": 0.04, "rolling_total_return_mean": 0.05}
    positive = assign_signal_contribution_label(
        baseline=baseline,
        summary={"rolling_rankic_mean": 0.036, "rolling_total_return_mean": 0.03},
    )
    dilutive = assign_signal_contribution_label(
        baseline=baseline,
        summary={"rolling_rankic_mean": 0.045, "rolling_total_return_mean": 0.07},
    )
    neutral = assign_signal_contribution_label(
        baseline=baseline,
        summary={"rolling_rankic_mean": 0.0395, "rolling_total_return_mean": 0.051},
    )
    assert positive == "positive"
    assert dilutive == "dilutive"
    assert neutral == "neutral"


def test_block_diagnostic_artifact_contract(tmp_path: Path) -> None:
    compare_root = tmp_path / "experiments" / "mainline_trimmed_compare"
    for object_name in TARGET_OBJECTS:
        obj = compare_root / object_name
        obj.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([
            {"total_return": 0.05, "RankIC": 0.04, "max_drawdown": -0.1, "turnover": 0.8},
            {"total_return": 0.01, "RankIC": 0.03, "max_drawdown": -0.12, "turnover": 0.7},
        ]).to_csv(obj / "rolling_metrics.csv", index=False)

    mapping = build_block_mapping()
    experiment_rows = []
    for object_name in TARGET_OBJECTS:
        for block_name in mapping[mapping["mainline_object_name"] == object_name]["block_name"].drop_duplicates():
            experiment_rows.append(
                {
                    "mainline_object_name": object_name,
                    "block_name": block_name,
                    "rolling_total_return_mean": 0.02,
                    "rolling_rankic_mean": 0.02,
                    "rolling_rankic_std": 0.01,
                    "rolling_max_drawdown_worst": -0.11,
                }
            )

    summary = build_diagnostic_summary(block_mapping=mapping, experiment_summaries=experiment_rows, project_root=tmp_path)
    expected_cols = {
        "mainline_object_name",
        "diagnostic_mode",
        "block_name",
        "rolling_total_return_mean",
        "rolling_rankic_mean",
        "rolling_rankic_std",
        "rolling_max_drawdown_worst",
        "signal_contribution_label",
        "notes",
    }
    assert set(summary.columns) == expected_cols
    assert set(summary["mainline_object_name"]) == set(TARGET_OBJECTS)
    assert set(summary["signal_contribution_label"]).issubset(ALLOWED_LABELS)


def test_signal_diagnostics_script_writes_contract_files(tmp_path: Path) -> None:
    compare_root = tmp_path / "experiments" / "mainline_trimmed_compare"
    for object_name in TARGET_OBJECTS:
        obj = compare_root / object_name
        obj.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([
            {"total_return": 0.05, "RankIC": 0.04, "max_drawdown": -0.1, "turnover": 0.8},
            {"total_return": 0.01, "RankIC": 0.03, "max_drawdown": -0.12, "turnover": 0.7},
        ]).to_csv(obj / "rolling_metrics.csv", index=False)

    model_root = tmp_path / "data" / "models"
    for object_name in TARGET_OBJECTS:
        model_dir = model_root / {
            "feature_173": "qlib_lgbm_extended",
            "feature_254_trimmed": "qlib_lgbm_semantic_all_features_trimmed",
        }[object_name]
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config_snapshot.json").write_text(
            '{"split_spec": {"train_start": "2020-01-01", "train_end_effective": "2026-03-13"}}',
            encoding="utf-8",
        )

    def _fake_train_custom_model(**kwargs):
        model_dir = tmp_path / "data" / "models" / kwargs["model_name"]
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "config_snapshot.json").write_text("{}", encoding="utf-8")
        return model_dir

    def _fake_run_rolling(model_path, object_name, *, start, end, out_dir):
        obj = out_dir / object_name
        obj.mkdir(parents=True, exist_ok=True)
        return {
            "rolling_total_return_mean": 0.02,
            "rolling_rankic_mean": 0.02,
            "rolling_rankic_std": 0.01,
            "rolling_max_drawdown_worst": -0.11,
        }

    runner = CliRunner()
    with patch("scripts.run_mainline_signal_diagnostics.project_root", tmp_path), \
         patch("scripts.run_mainline_signal_diagnostics._train_custom_model", side_effect=_fake_train_custom_model), \
         patch("scripts.run_mainline_signal_diagnostics._run_rolling", side_effect=_fake_run_rolling):
        result = runner.invoke(signal_diagnostics_main, ["--output_dir", "experiments/mainline_signal_diagnostics"])
    assert result.exit_code == 0, result.output
    out = tmp_path / "experiments" / "mainline_signal_diagnostics"
    assert (out / "block_mapping.csv").exists()
    assert (out / "block_diagnostic_summary.csv").exists()
    assert (out / "block_diagnostic_summary.md").exists()
    assert (out / "drop_one_experiment_rows.csv").exists()
