"""Tests for qsys.research.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from qsys.research.paths import ResearchPaths


class TestResearchPaths:
    def test_label_dir(self) -> None:
        rp = ResearchPaths("/tmp/research")
        assert rp.label_dir("fr_5d") == Path("/tmp/research/labels/fr_5d")
        assert rp.label_file("fr_5d") == Path("/tmp/research/labels/fr_5d/labels.parquet")
        assert rp.label_manifest("fr_5d") == Path("/tmp/research/labels/fr_5d/manifest.json")

    def test_signal_dir(self) -> None:
        rp = ResearchPaths("/tmp/research")
        p = rp.signal_dir("alpha_v1", "run_123")
        assert p == Path("/tmp/research/signals/alpha_v1/run_123")
        assert rp.signal_file("alpha_v1", "run_123") == p / "predictions.parquet"
        assert rp.signal_manifest("alpha_v1", "run_123") == p / "manifest.json"

    def test_model_dir(self) -> None:
        rp = ResearchPaths("/tmp/research")
        assert rp.model_dir("lgbm", "v1") == Path("/tmp/research/models/lgbm/v1")

    def test_backtest_dir(self) -> None:
        rp = ResearchPaths("/tmp/research")
        assert rp.backtest_dir("run1", "bt1") == Path("/tmp/research/backtests/run1/bt1")

    def test_experiment_dir(self) -> None:
        rp = ResearchPaths("/tmp/research")
        assert rp.experiment_dir("exp1") == Path("/tmp/research/experiments/exp1")

    def test_root_resolves(self, tmp_path: Path) -> None:
        rp = ResearchPaths(str(tmp_path))
        assert rp.root == tmp_path.resolve()

    def test_empty_label_id_raises(self) -> None:
        rp = ResearchPaths("/r")
        with pytest.raises(ValueError, match="empty"):
            rp.label_dir("")

    def test_dotdot_raises(self) -> None:
        rp = ResearchPaths("/r")
        with pytest.raises(ValueError):
            rp.label_dir("..")

    def test_slash_in_segment_raises(self) -> None:
        rp = ResearchPaths("/r")
        with pytest.raises(ValueError):
            rp.signal_dir("a/b", "c")

    def test_ensure_dir_creates(self, tmp_path: Path) -> None:
        rp = ResearchPaths(str(tmp_path))
        d = rp.ensure_dir(tmp_path / "new_dir")
        assert d.exists()
        assert d.is_dir()

    def test_no_writes_on_construction(self, tmp_path: Path) -> None:
        rp = ResearchPaths(str(tmp_path))
        path = rp.label_dir("test_label")
        assert not path.exists()
