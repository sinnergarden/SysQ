from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from qsys.research.mainline import MAINLINE_OBJECTS
from qsys.research.rolling import canonical_model_path
from scripts.run_mainline_rolling_eval import main as rolling_eval_main


class _FakeBacktestEngine:
    def __init__(self, *args, **kwargs):
        self.last_signal_metrics = {
            "IC": 0.11,
            "RankIC": 0.07,
            "long_short_spread": 0.02,
        }

    def run(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": "2025-01-02",
                    "total_assets": 100.0,
                    "daily_turnover": 10.0,
                    "position_count": 5,
                },
                {
                    "date": "2025-01-03",
                    "total_assets": 110.0,
                    "daily_turnover": 20.0,
                    "position_count": 4,
                },
            ]
        )


def test_canonical_model_path_matches_mainline_aliases(tmp_path: Path) -> None:
    assert canonical_model_path(tmp_path, MAINLINE_OBJECTS["feature_173"]) == tmp_path / "data" / "models" / "qlib_lgbm_extended"
    assert canonical_model_path(tmp_path, MAINLINE_OBJECTS["feature_254"]) == tmp_path / "data" / "models" / "qlib_lgbm_semantic_all_features"
    assert canonical_model_path(tmp_path, MAINLINE_OBJECTS["feature_254_absnorm"]) == tmp_path / "data" / "models" / "qlib_lgbm_semantic_all_features_absnorm"


def test_mainline_rolling_eval_writes_required_outputs(tmp_path: Path) -> None:
    runner = CliRunner()
    for spec in MAINLINE_OBJECTS.values():
        model_dir = canonical_model_path(tmp_path, spec)
        model_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "status": "available",
        "start": "2021-01-01",
        "end": "2024-12-31",
        "feature_set": "extended",
        "bundle_id": "bundle_feature_173",
    }
    with patch("scripts.run_mainline_rolling_eval.project_root", tmp_path), \
         patch("scripts.run_mainline_rolling_eval.BacktestEngine", _FakeBacktestEngine), \
         patch("scripts.run_mainline_rolling_eval.load_training_snapshot", return_value=snapshot), \
         patch("scripts.run_mainline_rolling_eval.build_backtest_lineage", return_value={"lineage_status": "ok"}):
        result = runner.invoke(
            rolling_eval_main,
            [
                "--start", "2025-01-02",
                "--end", "2025-02-15",
                "--output_dir", "tmp/rolling",
                "--test_window_days", "20",
                "--step_days", "20",
            ],
        )

    assert result.exit_code == 0, result.output
    for spec in MAINLINE_OBJECTS.values():
        object_dir = tmp_path / "tmp" / "rolling" / spec.mainline_object_name
        windows = pd.read_csv(object_dir / "rolling_windows.csv")
        metrics = pd.read_csv(object_dir / "rolling_metrics.csv")
        summary = json.loads((object_dir / "rolling_summary.json").read_text(encoding="utf-8"))

        assert {"mainline_object_name", "bundle_id", "legacy_feature_set_alias", "window_id", "train_start", "train_end", "test_start", "test_end"}.issubset(windows.columns)
        assert {
            "mainline_object_name",
            "bundle_id",
            "legacy_feature_set_alias",
            "window_id",
            "train_start",
            "train_end",
            "test_start",
            "test_end",
            "total_return",
            "max_drawdown",
            "turnover",
            "IC",
            "RankIC",
            "long_short_spread",
            "empty_portfolio_ratio",
            "avg_holding_count",
        }.issubset(metrics.columns)
        assert summary["mainline_object_name"] == spec.mainline_object_name
        assert summary["bundle_id"] == spec.bundle_id
        assert summary["legacy_feature_set_alias"] == spec.legacy_feature_set_alias
        assert summary["rolling_window_count"] >= 1
