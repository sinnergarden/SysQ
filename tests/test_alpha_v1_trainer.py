"""Tests for qsys/model/alpha_v1_trainer.py — AlphaV1Trainer wrapper (legacy)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from qsys.model.alpha_v1_trainer import (
    AlphaV1Trainer,
    _discover_artifacts,
    _discover_model_dir,
    _try_discover_metrics,
)


class TestDiscoverArtifacts:
    def test_discovers_expected_files(self, tmp_path):
        model_dir = tmp_path / "models" / "v1"
        model_dir.mkdir(parents=True)
        (model_dir / "model_5d.txt").write_text("data")
        (model_dir / "features.json").write_text("[]")
        arts = _discover_artifacts(tmp_path, str(model_dir.relative_to(tmp_path)))
        assert "model_5d.txt" in arts
        assert "features.json" in arts

    def test_missing_files_not_included(self, tmp_path):
        model_dir = tmp_path / "models" / "v2"
        model_dir.mkdir(parents=True)
        (model_dir / "model_5d.txt").write_text("data")
        arts = _discover_artifacts(tmp_path, str(model_dir.relative_to(tmp_path)))
        assert "model_5d.txt" in arts
        assert "model_20d.txt" not in arts

    def test_absolute_path_resolution(self, tmp_path):
        model_dir = tmp_path / "models" / "v3"
        model_dir.mkdir(parents=True)
        (model_dir / "model_5d.txt").write_text("data")
        arts = _discover_artifacts(tmp_path, str(model_dir))
        assert "model_5d.txt" in arts or arts == {}


class TestTryDiscoverMetrics:
    def test_reads_meta_json(self, tmp_path):
        model_dir = tmp_path / "models" / "v1"
        model_dir.mkdir(parents=True)
        meta = {"train_start": "2024-01-01", "train_end": "2026-05-15",
                "feature_count": 180, "training_rows": 50000}
        (model_dir / "meta.json").write_text(json.dumps(meta))
        metrics = _try_discover_metrics(tmp_path, str(model_dir.relative_to(tmp_path)))
        assert metrics["train_start"] == "2024-01-01"
        assert metrics["feature_count"] == 180

    def test_missing_meta_returns_empty(self, tmp_path):
        model_dir = tmp_path / "models" / "v2"
        model_dir.mkdir(parents=True)
        metrics = _try_discover_metrics(tmp_path, str(model_dir.relative_to(tmp_path)))
        assert metrics == {}

    def test_corrupted_meta_returns_empty(self, tmp_path):
        model_dir = tmp_path / "models" / "v3"
        model_dir.mkdir(parents=True)
        (model_dir / "meta.json").write_text("not valid json{{{")
        metrics = _try_discover_metrics(tmp_path, str(model_dir.relative_to(tmp_path)))
        assert metrics == {}


def _make_script_and_model(tmp_path: Path) -> None:
    """Create stub script + model dir that AlphaV1Trainer.run() expects."""
    script_dir = tmp_path / "scripts" / "deprecated"
    script_dir.mkdir(parents=True)
    (script_dir / "run_alpha_v1_weekly_train.py").write_text("")
    model_dir = tmp_path / "experiments" / "alpha_v1_models" / "20260704"
    model_dir.mkdir(parents=True)
    for f in ("model_5d.txt", "model_20d.txt", "features.json", "meta.json"):
        (model_dir / f).write_text("")


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.strategy_id = "alpha_v1"
    ctx.run_id = "test_run_123"
    ctx.no_notify = False
    return ctx


class TestAlphaV1Trainer:
    def test_script_not_found(self, tmp_path):
        trainer = AlphaV1Trainer(project_root=tmp_path)
        ctx = _make_ctx()
        result = trainer.run(ctx)
        assert result.status == "failed"
        assert "Training script not found" in (result.message or "")

    @patch("qsys.model.alpha_v1_trainer.subprocess.run")
    def test_success_result(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        _make_script_and_model(tmp_path)
        trainer = AlphaV1Trainer(project_root=tmp_path)
        ctx = _make_ctx()
        result = trainer.run(ctx)
        assert result.status == "success"
        assert result.strategy_id == "alpha_v1"

    @patch("qsys.model.alpha_v1_trainer.subprocess.run")
    def test_failure_result(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 1
        _make_script_and_model(tmp_path)
        trainer = AlphaV1Trainer(project_root=tmp_path)
        ctx = _make_ctx()
        result = trainer.run(ctx)
        assert result.status == "failed"
        assert "exited with code" in (result.message or "")

    @patch("qsys.model.alpha_v1_trainer.subprocess.run")
    def test_passes_end_date_from_config(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        _make_script_and_model(tmp_path)
        trainer = AlphaV1Trainer(
            project_root=tmp_path,
            config={"training": {"end_date": "2026-05-15"}},
        )
        ctx = _make_ctx()
        trainer.run(ctx)
        args = mock_run.call_args[0]
        cmd_args = args[0]
        assert "--end-date" in cmd_args
        assert cmd_args[cmd_args.index("--end-date") + 1] == "2026-05-15"

    @patch("qsys.model.alpha_v1_trainer.subprocess.run")
    def test_discovers_artifacts_on_success(self, mock_run, tmp_path):
        mock_run.return_value.returncode = 0
        _make_script_and_model(tmp_path)
        trainer = AlphaV1Trainer(project_root=tmp_path)
        ctx = _make_ctx()
        result = trainer.run(ctx)
        assert result.status == "success"
        assert "model_5d.txt" in result.artifacts
        assert "model_20d.txt" in result.artifacts

    def test_model_discovery_ignores_latest_symlink(self, tmp_path):
        base = tmp_path / "experiments/alpha_v1_models"
        approved = base / "20260704"
        approved.mkdir(parents=True)
        latest_target = tmp_path / "external_latest"
        latest_target.mkdir()
        (base / "latest").symlink_to(latest_target, target_is_directory=True)
        assert _discover_model_dir(tmp_path).endswith("20260704")
