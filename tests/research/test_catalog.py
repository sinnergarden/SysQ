"""Tests for the strategy catalog (config scanning, filtering, DataFrame output)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from qsys.research.catalog import (
    build_strategy_catalog,
    list_strategy_specs,
    load_strategy_configs,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def config_root(tmp_path: Path) -> Path:
    """Create a temporary config directory with two strategy YAML files."""
    _write_yaml(tmp_path / "alpha_v1.yaml", {
        "strategy_id": "alpha_v1",
        "stage": "candidate",
        "family": "ml",
        "display_name": "Alpha V1",
        "owner": "quant",
        "universe": "csi300",
        "feature_set": "alpha_v1",
        "model_version": "v1",
        "signal_version": "v1",
        "account_id": "shadow_alpha_v1",
    })
    _write_yaml(tmp_path / "alpha_v2.yaml", {
        "strategy_id": "alpha_v2",
        "stage": "research",
        "family": "momentum",
        "display_name": "Alpha V2",
        "universe": "csi300",
        "feature_set": "alpha_v2",
    })
    return tmp_path


# ── load_strategy_configs ──────────────────────────────────────────────────────


class TestLoadStrategyConfigs:
    def test_loads_all_configs(self, config_root: Path):
        configs = load_strategy_configs(config_root)
        assert len(configs) == 2

    def test_returns_raw_dicts(self, config_root: Path):
        configs = load_strategy_configs(config_root)
        ids = sorted(c["strategy_id"] for c in configs)
        assert ids == ["alpha_v1", "alpha_v2"]

    def test_skips_non_yaml_files(self, config_root: Path):
        (config_root / "readme.txt").write_text("hello")
        configs = load_strategy_configs(config_root)
        assert len(configs) == 2


# ── list_strategy_specs ────────────────────────────────────────────────────────


class TestListStrategySpecs:
    def test_list_all(self, config_root: Path):
        specs = list_strategy_specs(config_root)
        assert len(specs) == 2

    def test_filter_by_stage(self, config_root: Path):
        specs = list_strategy_specs(config_root, stage="candidate")
        assert len(specs) == 1
        assert specs[0].strategy_id == "alpha_v1"

    def test_research_stage_no_registry(self, config_root: Path):
        """Research-stage specs load fine without any registry entry."""
        specs = list_strategy_specs(config_root, stage="research")
        assert len(specs) == 1
        assert specs[0].strategy_id == "alpha_v2"


# ── build_strategy_catalog ─────────────────────────────────────────────────────


class TestBuildStrategyCatalog:
    def test_returns_dataframe(self, config_root: Path):
        df = build_strategy_catalog(config_root)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2

    def test_required_columns(self, config_root: Path):
        df = build_strategy_catalog(config_root)
        required = {
            "strategy_id", "stage", "family", "display_name",
            "universe", "feature_set", "model_version", "signal_version",
            "owner", "account_id", "config_path",
        }
        missing = sorted(required - set(df.columns))
        assert not missing, f"missing columns: {missing}"

    def test_sorted_by_strategy_id(self, config_root: Path):
        df = build_strategy_catalog(config_root)
        assert list(df["strategy_id"]) == ["alpha_v1", "alpha_v2"]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f)
