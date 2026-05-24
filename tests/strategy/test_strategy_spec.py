"""Contract tests for StrategySpec — static identity, config, lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from qsys.strategy.spec import (
    SUPPORTED_STAGES,
    StrategySpec,
    is_runtime_stage,
    load_strategy_spec,
    load_strategy_specs,
    load_strategy_specs_for_stage,
    spec_from_config,
    validate_stage,
)


# ── validate_stage ─────────────────────────────────────────────────────────────


class TestValidateStage:
    def test_valid_stages(self):
        for stage in SUPPORTED_STAGES:
            validate_stage(stage)  # does not raise

    def test_invalid_stage_raises(self):
        with pytest.raises(ValueError, match="unsupported stage"):
            validate_stage("invalid_stage")

    def test_empty_stage_raises(self):
        with pytest.raises(ValueError, match="unsupported stage"):
            validate_stage("")


# ── is_runtime_stage ───────────────────────────────────────────────────────────


class TestIsRuntimeStage:
    def test_candidate_is_runtime(self):
        assert is_runtime_stage("candidate") is True

    def test_production_is_runtime(self):
        assert is_runtime_stage("production") is True

    def test_research_is_not_runtime(self):
        assert is_runtime_stage("research") is False

    def test_rejected_is_not_runtime(self):
        assert is_runtime_stage("rejected") is False

    def test_archived_is_not_runtime(self):
        assert is_runtime_stage("archived") is False


# ── StrategySpec ───────────────────────────────────────────────────────────────


class TestStrategySpec:
    def test_minimal_spec(self):
        spec = StrategySpec(strategy_id="test_strat")
        assert spec.strategy_id == "test_strat"
        assert spec.stage == "research"  # default
        assert spec.display_name == "test_strat"  # falls back to strategy_id

    def test_strategy_id_required(self):
        with pytest.raises(ValueError, match="strategy_id"):
            StrategySpec(strategy_id="")

    def test_invalid_stage_raises(self):
        with pytest.raises(ValueError, match="unsupported stage"):
            StrategySpec(strategy_id="test", stage="invalid")

    def test_full_spec(self):
        spec = StrategySpec(
            strategy_id="test_strat",
            stage="candidate",
            family="momentum",
            display_name="Test Strategy",
            owner="researcher",
            universe="csi300",
            feature_set="test_features",
            model_version="v1",
            signal_version="v1",
            account_id="shadow_test",
            hypothesis="test hypothesis",
            label={"horizons": [5]},
            model={"type": "lgbm"},
            signal={"method": "rank"},
            portfolio={"top_n": 20},
            paths={"model_dir": "/tmp/models"},
            lifecycle={"created": "2026-01-01"},
            evaluation={"latest_status": "pass"},
            promotion_gates={"backtest_verified": True},
        )
        assert spec.strategy_id == "test_strat"
        assert spec.stage == "candidate"
        assert spec.portfolio["top_n"] == 20

    def test_to_dict_omits_raw_config(self):
        spec = StrategySpec(strategy_id="test")
        d = spec.to_dict()
        assert "raw_config" not in d
        assert "config_path" not in d
        assert d["strategy_id"] == "test"


# ── spec_from_config ───────────────────────────────────────────────────────────


class TestSpecFromConfig:
    def test_minimal_config(self):
        config = {"strategy_id": "minimal_strat"}
        spec = spec_from_config(config)
        assert spec.strategy_id == "minimal_strat"
        assert spec.stage == "research"  # default

    def test_stage_from_config(self):
        config = {"strategy_id": "s", "stage": "candidate"}
        spec = spec_from_config(config)
        assert spec.stage == "candidate"

    def test_none_stage_defaults_to_research(self):
        config = {"strategy_id": "s", "stage": None}
        spec = spec_from_config(config)
        assert spec.stage == "research"

    def test_unknown_keys_go_to_raw_config(self):
        config = {"strategy_id": "s", "unknown_key": "value"}
        spec = spec_from_config(config)
        assert spec.raw_config["unknown_key"] == "value"

    def test_config_path_set(self):
        config = {"strategy_id": "s"}
        spec = spec_from_config(config, path="/tmp/test.yaml")
        assert spec.config_path == "/tmp/test.yaml"

    def test_none_config_path_is_none(self):
        config = {"strategy_id": "s"}
        spec = spec_from_config(config, path=None)
        assert spec.config_path is None


# ── load_strategy_spec ─────────────────────────────────────────────────────────


class TestLoadStrategySpec:
    def test_load_alpha_v1(self, tmp_path: Path):
        path = _write_yaml(tmp_path, "alpha_v1.yaml", {
            "strategy_id": "alpha_v1",
            "stage": "candidate",
        })
        spec = load_strategy_spec(path)
        assert spec.strategy_id == "alpha_v1"
        assert spec.stage == "candidate"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_strategy_spec("/nonexistent/path.yaml")

    def test_non_dict_yaml_raises(self, tmp_path: Path):
        path = tmp_path / "list.yaml"
        with open(path, "w") as f:
            yaml.dump(["a", "b"], f)
        with pytest.raises(ValueError, match="expected a YAML dict"):
            load_strategy_spec(path)


# ── load_strategy_specs ────────────────────────────────────────────────────────


class TestLoadStrategySpecs:
    def test_loads_multiple_specs(self, tmp_path: Path):
        _write_yaml(tmp_path, "s1.yaml", {"strategy_id": "s1"})
        _write_yaml(tmp_path, "s2.yaml", {"strategy_id": "s2"})
        specs = load_strategy_specs(tmp_path)
        assert len(specs) == 2
        assert specs[0].strategy_id == "s1"
        assert specs[1].strategy_id == "s2"

    def test_skips_non_yaml_files(self, tmp_path: Path):
        _write_yaml(tmp_path, "s1.yaml", {"strategy_id": "s1"})
        (tmp_path / "readme.txt").write_text("hello")
        specs = load_strategy_specs(tmp_path)
        assert len(specs) == 1

    def test_recursive_scan(self, tmp_path: Path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        _write_yaml(sub, "s1.yaml", {"strategy_id": "s1"})
        specs = load_strategy_specs(tmp_path)
        assert len(specs) == 1


# ── load_strategy_specs_for_stage ──────────────────────────────────────────────


class TestLoadStrategySpecsForStage:
    def test_selects_candidate(self, tmp_path: Path):
        _write_yaml(tmp_path, "v1.yaml", {"strategy_id": "v1", "stage": "candidate"})
        _write_yaml(tmp_path, "v2.yaml", {"strategy_id": "v2", "stage": "candidate"})
        _write_yaml(tmp_path, "rx.yaml", {"strategy_id": "rx", "stage": "research"})
        specs = load_strategy_specs_for_stage("candidate", tmp_path, registry_required=False)
        assert len(specs) == 2
        assert {s.strategy_id for s in specs} == {"v1", "v2"}

    def test_excludes_research(self, tmp_path: Path):
        _write_yaml(tmp_path, "rx.yaml", {"strategy_id": "rx", "stage": "research"})
        specs = load_strategy_specs_for_stage("candidate", tmp_path, registry_required=False)
        assert len(specs) == 0

    def test_excludes_rejected_and_archived(self, tmp_path: Path):
        _write_yaml(tmp_path, "rj.yaml", {"strategy_id": "rj", "stage": "rejected"})
        _write_yaml(tmp_path, "ar.yaml", {"strategy_id": "ar", "stage": "archived"})
        specs = load_strategy_specs_for_stage("candidate", tmp_path, registry_required=False)
        assert len(specs) == 0

    def test_research_stage_raises(self, tmp_path: Path):
        """research is not a valid daily batch stage."""
        with pytest.raises(ValueError, match="not a daily batch stage"):
            load_strategy_specs_for_stage("research", tmp_path)

    def test_registry_required_filters_unregistered(self, tmp_path: Path):
        """With registry_required=True, unregistered strategies are excluded."""
        _write_yaml(tmp_path, "unknown.yaml",
                     {"strategy_id": "unknown_strat", "stage": "candidate"})
        # unknown_strat is not in the registry
        specs = load_strategy_specs_for_stage("candidate", tmp_path, registry_required=True)
        assert len(specs) == 0


# ── Helpers ────────────────────────────────────────────────────────────────────


def _write_yaml(tmp_path: Path, name: str, data: dict[str, Any]) -> Path:
    path = tmp_path / name
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path
