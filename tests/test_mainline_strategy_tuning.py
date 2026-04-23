from __future__ import annotations

import pandas as pd
from click.testing import CliRunner
from unittest.mock import patch

from qsys.research.strategy_tuning import STRATEGY_VARIANTS, build_strategy_summary, build_window_stability_summary
from scripts.run_mainline_strategy_tuning import main as strategy_tuning_main


def test_strategy_tuning_artifact_contract_fields() -> None:
    rows = [
        {
            "mainline_object_name": "feature_173",
            "strategy_variant": "k5_daily_b000",
            "top_k": 5,
            "rebalance_mode": "daily",
            "turnover_buffer": 0.0,
            "rolling_window_count": 20,
            "rolling_total_return_mean": 0.05,
            "rolling_rankic_mean": 0.04,
            "rolling_rankic_std": 0.01,
            "rolling_max_drawdown_worst": -0.1,
            "rolling_turnover_mean": 1.0,
            "rolling_empty_portfolio_ratio_mean": 0.0,
        },
        {
            "mainline_object_name": "feature_254_trimmed",
            "strategy_variant": "k5_daily_b000",
            "top_k": 5,
            "rebalance_mode": "daily",
            "turnover_buffer": 0.0,
            "rolling_window_count": 20,
            "rolling_total_return_mean": -0.02,
            "rolling_rankic_mean": 0.01,
            "rolling_rankic_std": 0.01,
            "rolling_max_drawdown_worst": -0.12,
            "rolling_turnover_mean": 0.8,
            "rolling_empty_portfolio_ratio_mean": 0.0,
        },
    ]
    df = build_strategy_summary(rows)
    assert {
        "mainline_object_name",
        "strategy_variant",
        "top_k",
        "rebalance_mode",
        "turnover_buffer",
        "rolling_window_count",
        "rolling_total_return_mean",
        "rolling_rankic_mean",
        "rolling_rankic_std",
        "rolling_max_drawdown_worst",
        "rolling_turnover_mean",
        "rolling_empty_portfolio_ratio_mean",
        "vs_baseline_delta_return",
        "vs_baseline_delta_rankic",
    }.issubset(df.columns)


def test_strategy_variant_naming_is_stable_and_distinct() -> None:
    names = [v["strategy_variant"] for v in STRATEGY_VARIANTS]
    assert names == [
        "k5_daily_b000",
        "k8_daily_b000",
        "k10_daily_b000",
        "k5_weekly_b000",
        "k5_daily_b002",
        "k5_daily_b005",
    ]
    assert len(names) == len(set(names))


def test_window_stability_summary_contract() -> None:
    metrics = pd.DataFrame([
        {"window_id": "w1", "test_start": "2025-01-01", "test_end": "2025-02-01", "total_return": 0.1, "RankIC": 0.02},
        {"window_id": "w2", "test_start": "2025-02-02", "test_end": "2025-03-01", "total_return": -0.02, "RankIC": -0.01},
        {"window_id": "w3", "test_start": "2025-03-02", "test_end": "2025-04-01", "total_return": 0.03, "RankIC": 0.01},
    ])
    row = build_window_stability_summary("feature_254_trimmed", "k5_daily_b000", metrics)
    assert set(row.keys()) == {
        "mainline_object_name",
        "strategy_variant",
        "positive_return_window_ratio",
        "rankic_positive_window_ratio",
        "top3_positive_return_share",
        "worst_3_windows",
        "best_3_windows",
    }


def test_two_object_only_strategy_tuning_scope() -> None:
    import scripts.run_mainline_strategy_tuning as mod
    assert mod.OBJECTS == ["feature_173", "feature_254_trimmed"]


def test_strategy_tuning_script_writes_outputs(tmp_path) -> None:
    runner = CliRunner()

    def fake_run_strategy_variant(model_path, *, start, end, top_k, rebalance_mode, turnover_buffer):
        result = pd.DataFrame([
            {"date": start, "total_assets": 100.0, "position_count": top_k, "trade_count": 1, "daily_fee": 1.0, "daily_turnover": 1000.0},
            {"date": end, "total_assets": 101.0, "position_count": top_k, "trade_count": 1, "daily_fee": 1.0, "daily_turnover": 1000.0},
        ])
        return result, {"RankIC": 0.01, "IC": 0.01, "long_short_spread": 0.01}, {}

    with patch("scripts.run_mainline_strategy_tuning.project_root", tmp_path), \
         patch("scripts.run_mainline_strategy_tuning._variant_model_path", return_value=tmp_path / "model"), \
         patch("scripts.run_mainline_strategy_tuning.load_training_snapshot", return_value={"split_spec": {"train_start": "2021-01-01", "train_end_effective": "2024-12-31"}}), \
         patch("scripts.run_mainline_strategy_tuning.compute_window_metrics", return_value={"mainline_object_name": "feature_173", "bundle_id": "b", "legacy_feature_set_alias": "a", "window_id": "window_001", "train_start": "2021-01-01", "train_end": "2024-12-31", "test_start": "2025-01-02", "test_end": "2025-03-05", "total_return": 0.01, "max_drawdown": -0.1, "turnover": 1.0, "IC": 0.01, "RankIC": 0.01, "long_short_spread": 0.01, "empty_portfolio_ratio": 0.0, "avg_holding_count": 5.0}), \
         patch("scripts.run_mainline_strategy_tuning.run_strategy_variant", side_effect=fake_run_strategy_variant):
        result = runner.invoke(strategy_tuning_main, ["--output_dir", "experiments/mainline_strategy_tuning"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "experiments" / "mainline_strategy_tuning" / "strategy_tuning_summary.csv").exists()
    assert (tmp_path / "experiments" / "mainline_strategy_tuning" / "window_stability_summary.csv").exists()
