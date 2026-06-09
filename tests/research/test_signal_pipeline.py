"""Tests for SignalResearchPipeline — signal generation + eval only."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.research.matrix_job import RollingResearchConfig
from qsys.research.signal_pipeline import (
    SignalEvalRef,
    SignalResearchPipeline,
    SignalResearchResult,
    SignalRunRef,
)


def _make_minimal_config(**overrides) -> RollingResearchConfig:
    """Build a minimal rolling research config for testing."""
    cfg = RollingResearchConfig(
        experiment_id="test_signal_pipeline",
        calendar={
            "start_date": "2026-01-05",
            "end_date": "2026-01-09",
            "train_window_days": 60,
            "step_days": 5,
        },
        signal={
            "signal_id": "test_signal",
            "signal_run_id": "test_run",
        },
        labels=[{"label_id": "l1"}],
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


class TestSignalResearchPipelineBasics:
    """Verify pipeline produces signals + evals, not backtests."""

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_run_single_returns_result_object(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config()

        result = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)

        assert isinstance(result, SignalResearchResult)
        assert result.experiment_id == "test_signal_pipeline"
        assert len(result.signal_runs) == 1
        assert len(result.eval_refs) == 1
        assert isinstance(result.signal_runs[0], SignalRunRef)
        assert isinstance(result.eval_refs[0], SignalEvalRef)
        assert result.manifest_path.exists()

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_signal_run_has_correct_columns(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config()

        result = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)
        sref = result.signal_runs[0]

        loaded = pipeline._signal_store.load_signal_run(
            sref.signal_id, sref.signal_run_id,
        )
        required = {"trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"}
        assert required.issubset(set(loaded.columns))
        assert (loaded["signal_id"] == sref.signal_id).all()
        assert (loaded["signal_run_id"] == sref.signal_run_id).all()

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_no_backtest_artifacts(self, mock_labels, tmp_path: Path) -> None:
        """Pipeline must NOT write backtest artifacts."""
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config()

        pipeline.run(config, overwrite_signal=True, overwrite_eval=True)

        bt_dir = Path(str(tmp_path)) / "backtests"
        files = list(bt_dir.iterdir()) if bt_dir.exists() else []
        assert len(files) == 0

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_manifest_has_no_backtest_refs(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config()

        result = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)
        mf = json.loads(result.manifest_path.read_text())

        assert mf["artifact_type"] == "signal_research"
        assert "backtest_count" not in mf
        assert "backtest_refs" not in mf


class TestSignalResearchPipelineMatrix:
    """Matrix experiment path: generators × transforms."""

    @staticmethod
    def _matrix_config() -> RollingResearchConfig:
        return RollingResearchConfig(
            experiment_id="matrix_test",
            calendar={
                "start_date": "2026-01-05",
                "end_date": "2026-01-09",
                "train_window_days": 60,
                "step_days": 5,
            },
            signal={"signal_id": "matrix_sig", "score_column": "score"},
            labels=[{"label_id": "l1"}],
            generators=[
                {"generator_id": "g1", "type": "fixture", "params": {"n_instruments": 10}},
            ],
            transforms=[
                {"transform_id": "raw", "type": "identity"},
                {"transform_id": "zs", "type": "daily_zscore"},
            ],
        )

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_matrix_produces_signal_runs(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = self._matrix_config()

        result = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)

        # 1 generator × 2 transforms = 2 signal runs
        assert len(result.signal_runs) == 2
        # Each label evaluated for each signal run
        assert len(result.eval_refs) == 2

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_matrix_manifest_structure(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = self._matrix_config()

        result = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)
        mf = json.loads(result.manifest_path.read_text())

        assert mf["artifact_type"] == "signal_research"
        assert mf["mode"] == "matrix"
        assert mf["generator_count"] == 1
        assert mf["transform_count"] == 2
        assert len(mf["signal_runs"]) == 2


class TestSignalResearchPipelineMultiHead:
    """Multi-head generator support."""

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_multi_head_produces_independent_signal_runs(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        config = RollingResearchConfig(
            experiment_id="multi_head_test",
            calendar={
                "start_date": "2026-01-05",
                "end_date": "2026-01-09",
                "train_window_days": 60,
                "step_days": 5,
            },
            signal={"signal_id": "mh_sig", "score_column": "score"},
            labels=[{"label_id": "l1"}],
            generators=[
                {
                    "generator_id": "multi_gen",
                    "type": "fixture",
                    "params": {
                        "n_instruments": 10,
                        "heads": [
                            {"signal_id": "head_a"},
                            {"signal_id": "head_b"},
                        ],
                    },
                },
            ],
            transforms=[{"transform_id": "raw", "type": "identity"}],
        )

        from qsys.research.generators.fixture import MultiHeadFixtureGenerator

        pipeline = SignalResearchPipeline(str(tmp_path))
        result = pipeline.run(
            config,
            signal_generator=MultiHeadFixtureGenerator(
                head_signal_ids=("head_a", "head_b"),
                n_instruments=10,
            ),
            overwrite_signal=True,
            overwrite_eval=True,
        )

        # 1 generator × 1 transform × 2 heads = 2 signal runs
        assert len(result.signal_runs) == 2
        signal_ids = {r.signal_id for r in result.signal_runs}
        assert "head_a__raw" in signal_ids
        assert "head_b__raw" in signal_ids

        # Assert each saved SignalRun is non-empty and has correct signal_id
        for sref in result.signal_runs:
            df = pipeline._signal_store.load_signal_run(sref.signal_id, sref.signal_run_id)
            assert not df.empty, f"SignalRun {sref.signal_id}/{sref.signal_run_id} is empty"
            assert set(df["signal_id"].unique()) == {sref.signal_id}


class TestSignalResearchPipelineConfigValidation:
    """Verify config validation rejects backtest/strategy configs."""

    def test_rejects_strategies(self, tmp_path: Path) -> None:
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config(strategies=[{"strategy_id": "s1"}])

        with pytest.raises(ValueError, match="does not accept strategies"):
            pipeline.run(config)

    def test_rejects_backtests(self, tmp_path: Path) -> None:
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config(backtests=[{"strategy_template_id": "rank_weight_top20"}])

        with pytest.raises(ValueError, match="does not accept backtests"):
            pipeline.run(config)


class TestSignalResearchPipelineSignalCombinations:
    """Signal combination in pipeline (without backtest)."""

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_combination_produces_combined_signal_run(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        config = RollingResearchConfig(
            experiment_id="comb_test",
            calendar={
                "start_date": "2026-01-05",
                "end_date": "2026-01-09",
                "train_window_days": 60,
                "step_days": 5,
            },
            signal={"signal_id": "comb_sig", "score_column": "score"},
            labels=[{"label_id": "l1"}],
            generators=[
                {"generator_id": "g1", "type": "fixture", "params": {"n_instruments": 10}},
                {"generator_id": "g2", "type": "fixture", "params": {"n_instruments": 10, "seed": 99}},
            ],
            transforms=[{"transform_id": "raw", "type": "identity"}],
            signal_combinations=[
                {
                    "combine_id": "blend_1",
                    "type": "linear_blend",
                    "inputs": [
                        {"source_generator_id": "g1", "source_transform_id": "raw", "weight": 0.6},
                        {"source_generator_id": "g2", "source_transform_id": "raw", "weight": 0.4},
                    ],
                },
            ],
        )

        pipeline = SignalResearchPipeline(str(tmp_path))
        result = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)

        # 2 generators × 1 transform = 2 base runs + 1 combined = 3 total
        assert len(result.signal_runs) == 3
        combine_runs = [r for r in result.signal_runs if r.transform_id == "combined"]
        assert len(combine_runs) == 1
        assert combine_runs[0].generator_id == "blend_1"

        # 3 signal runs × 1 label = 3 eval refs
        assert len(result.eval_refs) == 3


class TestSignalResearchPipelineDeterministic:
    """Deterministic fixture → reproducible output."""

    @patch("qsys.label.store.LabelStore.load_labels")
    def test_two_runs_produce_identical_signals(self, mock_labels, tmp_path: Path) -> None:
        mock_labels.return_value = _make_fake_labels()
        pipeline = SignalResearchPipeline(str(tmp_path))
        config = _make_minimal_config()

        result1 = pipeline.run(config, overwrite_signal=True, overwrite_eval=True)

        config2 = _make_minimal_config(experiment_id="test_signal_pipeline_2")
        result2 = pipeline.run(config2, overwrite_signal=True, overwrite_eval=True)

        df1 = pipeline._signal_store.load_signal_run(
            result1.signal_runs[0].signal_id, result1.signal_runs[0].signal_run_id,
        )
        df2 = pipeline._signal_store.load_signal_run(
            result2.signal_runs[0].signal_id, result2.signal_runs[0].signal_run_id,
        )
        pd.testing.assert_frame_equal(df1.sort_index(axis=1), df2.sort_index(axis=1))


def _make_fake_labels(label_id: str = "l1") -> pd.DataFrame:
    dates = [f"2026-01-{d:02d}" for d in range(4, 15)]
    rows = []
    for td in dates:
        for inst in [f"000{i:04d}.SZ" for i in range(10)]:
            rows.append({
                "trade_date": td,
                "instrument": inst,
                "label_id": label_id,
                "label_value": 0.01,
            })
    df = pd.DataFrame(rows)
    df["label_id"] = label_id
    return df
