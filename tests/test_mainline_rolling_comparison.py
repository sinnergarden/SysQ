from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from scripts.run_mainline_rolling_comparison import main as rolling_comparison_main


SUMMARY_ROWS = {
    "feature_173": {
        "mainline_object_name": "feature_173",
        "bundle_id": "bundle_feature_173",
        "legacy_feature_set_alias": "extended",
        "rolling_window_count": 3,
        "rolling_total_return_mean": 0.10,
        "rolling_total_return_median": 0.11,
        "rolling_rankic_mean": 0.05,
        "rolling_rankic_std": 0.01,
        "rolling_max_drawdown_worst": -0.08,
        "rolling_turnover_mean": 0.21,
        "rolling_empty_portfolio_ratio_mean": 0.0,
    },
    "feature_254": {
        "mainline_object_name": "feature_254",
        "bundle_id": "bundle_feature_254",
        "legacy_feature_set_alias": "semantic_all_features",
        "rolling_window_count": 3,
        "rolling_total_return_mean": 0.07,
        "rolling_total_return_median": 0.06,
        "rolling_rankic_mean": 0.03,
        "rolling_rankic_std": 0.02,
        "rolling_max_drawdown_worst": -0.12,
        "rolling_turnover_mean": 0.30,
        "rolling_empty_portfolio_ratio_mean": 0.02,
    },
    "feature_254_absnorm": {
        "mainline_object_name": "feature_254_absnorm",
        "bundle_id": "bundle_feature_254_absnorm",
        "legacy_feature_set_alias": "semantic_all_features_absnorm",
        "rolling_window_count": 3,
        "rolling_total_return_mean": 0.12,
        "rolling_total_return_median": 0.10,
        "rolling_rankic_mean": 0.08,
        "rolling_rankic_std": 0.015,
        "rolling_max_drawdown_worst": -0.09,
        "rolling_turnover_mean": 0.24,
        "rolling_empty_portfolio_ratio_mean": 0.0,
    },
}


def _write_summary(root: Path, name: str, payload: dict) -> None:
    target = root / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "rolling_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_mainline_rolling_comparison_writes_csv_and_markdown(tmp_path: Path) -> None:
    rolling_root = tmp_path / "rolling"
    for name, payload in SUMMARY_ROWS.items():
        _write_summary(rolling_root, name, payload)

    runner = CliRunner()
    with patch("scripts.run_mainline_rolling_comparison.project_root", tmp_path):
        result = runner.invoke(
            rolling_comparison_main,
            [
                "--rolling_dir", "rolling",
            ],
        )

    assert result.exit_code == 0, result.output
    summary = pd.read_csv(rolling_root / "comparison_summary.csv")
    report = (rolling_root / "comparison_summary.md").read_text(encoding="utf-8")

    assert {
        "mainline_object_name",
        "bundle_id",
        "legacy_feature_set_alias",
        "rolling_window_count",
        "rolling_total_return_mean",
        "rolling_total_return_median",
        "rolling_rankic_mean",
        "rolling_rankic_std",
        "rolling_max_drawdown_worst",
        "rolling_turnover_mean",
        "rolling_empty_portfolio_ratio_mean",
        "decision_status",
        "decision_reason",
    }.issubset(summary.columns)
    assert "feature_254_absnorm" in report
    assert "Best" in report
    assert "Worst" in report
