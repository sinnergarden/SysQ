"""Tests for qsys.research.experiment."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from qsys.research.experiment import ExperimentIndex, ExperimentSpec


def _make_signal_artifact(tmp_path: Path, signal_id: str, signal_run_id: str) -> Path:
    d = tmp_path / "signals" / signal_id / signal_run_id
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "artifact_type": "signal_run",
        "signal_id": signal_id, "signal_run_id": signal_run_id,
        "signal_kind": "raw", "row_count": 1500,
        "prediction_start": "2026-05-18", "prediction_end": "2026-05-22",
        "model_id": "test_model", "feature_set_id": "csi300_v1",
        "label_id": "fr_5d", "universe": "csi300",
        "columns": ["trade_date", "instrument", "score"],
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    return d


def _make_eval_artifact(tmp_path: Path, signal_id: str, signal_run_id: str, label_id: str,
                         no_ic: bool = False) -> Path:
    d = tmp_path / "signals" / signal_id / signal_run_id / "eval" / label_id
    d.mkdir(parents=True, exist_ok=True)
    summary = {
        "signal_id": signal_id, "signal_run_id": signal_run_id, "label_id": label_id,
        "score_column": "score", "n_groups": 5, "n_obs": 1200, "n_days": 4,
        "ic_mean": None if no_ic else 0.175,
        "ic_std": 0.154, "icir": None if no_ic else 1.14,
        "rank_ic_mean": None if no_ic else 0.163,
        "rank_ic_std": 0.094, "rank_icir": None if no_ic else 1.74,
        "coverage_mean": 0.67,
        "start_date": "2026-05-18", "end_date": "2026-05-21",
    }
    (d / "summary.json").write_text(json.dumps(summary))
    return d


def _make_backtest_artifact(tmp_path: Path, strategy_run_id: str, backtest_id: str,
                             total_return: float = 0.0148) -> Path:
    d = tmp_path / "backtests" / strategy_run_id / backtest_id
    d.mkdir(parents=True, exist_ok=True)
    manifest = {
        "artifact_type": "backtest_run",
        "backtest_id": backtest_id, "strategy_run_id": strategy_run_id,
        "strategy_template_id": "rank_weight_top20",
        "signal_id": "alpha_v1_score", "signal_run_id": "smoke",
        "model_mode": "cached_signal", "rolling_train": False,
        "execution_timing": "preopen",
        "start_date": "2026-05-18", "end_date": "2026-05-22",
        "trading_day_count": 5, "initial_capital": 1_000_000,
        "final_value": 1_014_803, "total_return": total_return,
    }
    (d / "manifest.json").write_text(json.dumps(manifest))
    metrics = {
        "initial_capital": 1_000_000, "final_value": 1_014_803,
        "total_return": total_return, "trading_day_count": 5,
        "order_count_total": 120, "filled_count_total": 118,
        "rejected_count_total": 2, "turnover_total": 500_000,
        "avg_turnover": 100_000,
    }
    (d / "metrics.json").write_text(json.dumps(metrics))
    return d


class TestExperimentCreate:
    def test_creates_manifest(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("exp1", title="Test"), overwrite=True)
        exp_dir = tmp_path / "experiments" / "exp1"
        assert exp_dir.exists()
        mf = json.loads((exp_dir / "manifest.json").read_text())
        assert mf["artifact_type"] == "experiment_index"
        assert mf["experiment_id"] == "exp1"

    def test_overwrite_false_protects(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("exp1"), overwrite=True)
        with pytest.raises(FileExistsError):
            idx.create(ExperimentSpec("exp1"))

    def test_overwrite_true_succeeds(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("exp1"), overwrite=True)
        idx.create(ExperimentSpec("exp1"), overwrite=True)


class TestAddReferences:
    def test_add_signal_run_creates_refs(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_run("e1", signal_id="s1", signal_run_id="r1")
        refs = pd.read_csv(tmp_path / "experiments" / "e1" / "signal_run_refs.csv")
        assert len(refs) == 1
        assert refs.iloc[0]["signal_id"] == "s1"

    def test_add_signal_eval_creates_refs(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_eval("e1", signal_id="s1", signal_run_id="r1", label_id="l1")
        refs = pd.read_csv(tmp_path / "experiments" / "e1" / "signal_eval_refs.csv")
        assert len(refs) == 1
        assert refs.iloc[0]["label_id"] == "l1"

    def test_add_backtest_creates_refs(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_backtest_run("e1", strategy_run_id="sr1", backtest_id="bt1")
        refs = pd.read_csv(tmp_path / "experiments" / "e1" / "backtest_refs.csv")
        assert len(refs) == 1
        assert refs.iloc[0]["backtest_id"] == "bt1"


class TestRebuildIndexes:
    def test_signal_run_index(self, tmp_path: Path) -> None:
        _make_signal_artifact(tmp_path, "s1", "r1")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_run("e1", signal_id="s1", signal_run_id="r1")
        result = idx.rebuild_indexes("e1")
        assert result.signal_run_count == 1
        idx_df = pd.read_csv(tmp_path / "experiments" / "e1" / "signal_run_index.csv")
        assert len(idx_df) == 1
        assert idx_df.iloc[0]["signal_kind"] == "raw"

    def test_signal_eval_index_has_metrics(self, tmp_path: Path) -> None:
        _make_eval_artifact(tmp_path, "s1", "r1", "l1")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_eval("e1", signal_id="s1", signal_run_id="r1", label_id="l1")
        idx.rebuild_indexes("e1")
        idx_df = pd.read_csv(tmp_path / "experiments" / "e1" / "signal_eval_index.csv")
        assert len(idx_df) == 1
        assert float(idx_df.iloc[0]["rank_icir"]) == 1.74

    def test_eval_with_none_ic_still_present(self, tmp_path: Path) -> None:
        """Eval with ic_mean=None should be status=present not missing."""
        _make_eval_artifact(tmp_path, "s1", "r1", "l1", no_ic=True)
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_eval("e1", signal_id="s1", signal_run_id="r1", label_id="l1")
        result = idx.rebuild_indexes("e1")
        assert result.signal_eval_count == 1
        idx_df = pd.read_csv(tmp_path / "experiments" / "e1" / "signal_eval_index.csv")
        assert idx_df.iloc[0]["status"] == "present"

    def test_backtest_index_has_metrics(self, tmp_path: Path) -> None:
        _make_backtest_artifact(tmp_path, "sr1", "bt1")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_backtest_run("e1", strategy_run_id="sr1", backtest_id="bt1")
        idx.rebuild_indexes("e1")
        idx_df = pd.read_csv(tmp_path / "experiments" / "e1" / "backtest_index.csv")
        assert len(idx_df) == 1
        assert float(idx_df.iloc[0]["total_return"]) == 0.0148

    def test_missing_artifact_status(self, tmp_path: Path) -> None:
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_run("e1", signal_id="nonexistent", signal_run_id="no_run")
        idx.rebuild_indexes("e1")
        idx_df = pd.read_csv(tmp_path / "experiments" / "e1" / "signal_run_index.csv")
        assert idx_df.iloc[0]["status"] == "missing"

    def test_all_csrs_written(self, tmp_path: Path) -> None:
        _make_signal_artifact(tmp_path, "s1", "r1")
        _make_eval_artifact(tmp_path, "s1", "r1", "l1")
        _make_backtest_artifact(tmp_path, "sr1", "bt1")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_run("e1", signal_id="s1", signal_run_id="r1")
        idx.add_signal_eval("e1", signal_id="s1", signal_run_id="r1", label_id="l1")
        idx.add_backtest_run("e1", strategy_run_id="sr1", backtest_id="bt1")
        result = idx.rebuild_indexes("e1")
        assert result.signal_run_count == 1
        assert result.signal_eval_count == 1
        assert result.backtest_count == 1

    def test_summary_written(self, tmp_path: Path) -> None:
        _make_signal_artifact(tmp_path, "s1", "r1")
        _make_eval_artifact(tmp_path, "s1", "r1", "l1")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1", title="Test Exp"), overwrite=True)
        idx.add_signal_run("e1", signal_id="s1", signal_run_id="r1")
        idx.add_signal_eval("e1", signal_id="s1", signal_run_id="r1", label_id="l1")
        result = idx.rebuild_indexes("e1")
        summary = (tmp_path / "experiments" / "e1" / "summary.md").read_text()
        assert "# Test Exp" in summary

    def test_numeric_sort_signal_eval(self, tmp_path: Path) -> None:
        """rank_icir values like '10.0' and '2.0' sort numerically."""
        _make_eval_artifact(tmp_path, "high", "r1", "l1")
        _make_eval_artifact(tmp_path, "medium", "r1", "l1",
                             no_ic=False)
        # Override high eval's summary with string rank_icir
        d = tmp_path / "signals" / "high" / "r1" / "eval" / "l1"
        (d / "summary.json").write_text(json.dumps({
            "signal_id": "high", "signal_run_id": "r1", "label_id": "l1",
            "rank_icir": "10.0", "n_obs": 1000, "n_days": 5,
            "ic_mean": 0.2, "rank_ic_mean": 0.3, "coverage_mean": 0.9,
        }))
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_eval("e1", signal_id="high", signal_run_id="r1", label_id="l1")
        idx.add_signal_eval("e1", signal_id="medium", signal_run_id="r1", label_id="l1")
        idx.rebuild_indexes("e1")
        summary = (tmp_path / "experiments" / "e1" / "summary.md").read_text()
        pos_high = summary.index("high")
        pos_medium = summary.index("medium")
        assert pos_high < pos_medium  # 10.0 > 1.74

    def test_numeric_sort_backtest(self, tmp_path: Path) -> None:
        """total_return values like '10.0' and '2.0' sort numerically."""
        _make_backtest_artifact(tmp_path, "high_ret", "bt1", total_return="10.0")
        _make_backtest_artifact(tmp_path, "low_ret", "bt1", total_return="2.0")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_backtest_run("e1", strategy_run_id="high_ret", backtest_id="bt1")
        idx.add_backtest_run("e1", strategy_run_id="low_ret", backtest_id="bt1")
        idx.rebuild_indexes("e1")
        summary = (tmp_path / "experiments" / "e1" / "summary.md").read_text()
        pos_high = summary.index("high_ret")
        pos_low = summary.index("low_ret")
        assert pos_high < pos_low  # 10.0 > 2.0

    def test_load_summary_tables(self, tmp_path: Path) -> None:
        _make_signal_artifact(tmp_path, "s1", "r1")
        idx = ExperimentIndex(str(tmp_path))
        idx.create(ExperimentSpec("e1"), overwrite=True)
        idx.add_signal_run("e1", signal_id="s1", signal_run_id="r1")
        idx.rebuild_indexes("e1")
        tables = idx.load_summary_tables("e1")
        assert "signal_run" in tables
        assert len(tables["signal_run"]) == 1
