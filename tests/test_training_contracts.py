"""Tests for qsys/model/training.py — TrainingResult and ModelManifest."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qsys.model.training import (
    ModelManifest,
    TrainingResult,
    write_model_manifest,
    write_training_result,
)


class TestTrainingResult:
    def test_default_status(self):
        r = TrainingResult(strategy_id="test", model_version="v1", model_dir="/tmp")
        assert r.status == "success"
        assert r.message is None

    def test_optional_fields_default_to_none(self):
        r = TrainingResult(strategy_id="test", model_version="v1", model_dir="/tmp")
        assert r.train_start is None
        assert r.train_end is None
        assert r.valid_start is None
        assert r.valid_end is None

    def test_metrics_default_to_empty(self):
        r = TrainingResult(strategy_id="test", model_version="v1", model_dir="/tmp")
        assert r.metrics == {}

    def test_artifacts_default_to_empty(self):
        r = TrainingResult(strategy_id="test", model_version="v1", model_dir="/tmp")
        assert r.artifacts == {}

    def test_failed_status(self):
        r = TrainingResult(
            strategy_id="alpha_v1",
            model_version="v1",
            model_dir="",
            status="failed",
            message="Training script not found",
        )
        assert r.status == "failed"
        assert "not found" in r.message

    def test_serializes_to_json(self, tmp_path):
        r = TrainingResult(
            strategy_id="alpha_v1",
            model_version="20260523",
            model_dir="experiments/alpha_v1_models/20260523",
            train_start="2024-06-01",
            train_end="2026-05-15",
            metrics={"feature_count": 180, "training_rows": 50000},
            artifacts={"model_5d.txt": "model_5d.txt", "features.json": "features.json"},
        )
        p = tmp_path / "training_result.json"
        write_training_result(r, p)
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["strategy_id"] == "alpha_v1"
        assert data["status"] == "success"
        assert data["metrics"]["feature_count"] == 180

    def test_deserialize_roundtrip(self, tmp_path):
        r = TrainingResult(
            strategy_id="alpha_v1", model_version="v1", model_dir="/tmp",
            metrics={"rankic": 0.05},
        )
        p = tmp_path / "rt.json"
        write_training_result(r, p)
        data = json.loads(p.read_text())
        assert data["metrics"]["rankic"] == 0.05
        assert data["status"] == "success"

    def test_empty_metrics_serialize(self, tmp_path):
        r = TrainingResult(strategy_id="t", model_version="v", model_dir="/tmp")
        p = tmp_path / "empty_metrics.json"
        write_training_result(r, p)
        data = json.loads(p.read_text())
        assert data["metrics"] == {}


class TestModelManifest:
    def test_minimal_manifest(self):
        m = ModelManifest(
            strategy_id="alpha_v1",
            model_version="v1",
            model_type="lightgbm_dual",
            feature_set="alpha_v1",
            label={"horizons": [5, 20], "type": "forward_return"},
            train_window={"train_days": 504},
            created_at="2026-05-23T12:00:00",
            artifacts={"model_5d.txt": "model_5d.txt"},
            metrics={"rankic_5d": 0.03},
        )
        assert m.strategy_id == "alpha_v1"
        assert m.model_type == "lightgbm_dual"

    def test_git_commit_optional(self):
        m = ModelManifest(
            strategy_id="t", model_version="v", model_type="x",
            feature_set="f", label={}, train_window={},
            created_at="now", artifacts={}, metrics={},
        )
        assert m.git_commit is None

    def test_serializes_to_json(self, tmp_path):
        m = ModelManifest(
            strategy_id="alpha_v1",
            model_version="20260523",
            model_type="lightgbm_dual",
            feature_set="alpha_v1",
            label={"horizons": [5, 20], "type": "forward_return"},
            train_window={"train_days": 504, "test_days": 5},
            created_at="2026-05-23T12:00:00",
            artifacts={"model_5d.txt": "model_5d.txt", "features.json": "features.json"},
            metrics={"rankic_5d": 0.035, "rankic_20d": 0.021},
            git_commit="abc123",
        )
        p = tmp_path / "manifest.json"
        write_model_manifest(m, p)
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["model_type"] == "lightgbm_dual"
        assert data["git_commit"] == "abc123"
        assert data["metrics"]["rankic_5d"] == 0.035

    def test_parent_dir_created(self, tmp_path):
        m = ModelManifest(
            strategy_id="t", model_version="v", model_type="x",
            feature_set="f", label={}, train_window={},
            created_at="now", artifacts={}, metrics={},
        )
        p = tmp_path / "nested" / "sub" / "manifest.json"
        write_model_manifest(m, p)
        assert p.exists()
