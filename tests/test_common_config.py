"""Tests for qsys/common/config.py — read_yaml, load_strategy_config."""
from __future__ import annotations

from pathlib import Path

import pytest

from qsys.common.config import load_strategy_config, read_yaml


class TestReadYaml:
    def test_load_simple(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("key: value\nnested:\n  inner: 42\n")
        assert read_yaml(f) == {"key": "value", "nested": {"inner": 42}}

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError, match="nonexistent"):
            read_yaml("/tmp/nonexistent_path_for_test.yaml")

    def test_non_dict_root(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("- one\n- two\n")
        with pytest.raises(ValueError, match="dict"):
            read_yaml(f)

    def test_empty_dict(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("{}\n")
        assert read_yaml(f) == {}

    def test_handles_unicode(self, tmp_path):
        f = tmp_path / "unicode.yaml"
        f.write_text("display_name: Alpha V1\n")
        assert read_yaml(f) == {"display_name": "Alpha V1"}


class TestLoadStrategyConfig:
    def test_load_alpha_v1_default(self, project_root_fixture):
        """Confirm the shipped alpha_v1.yaml is loadable."""
        cfg = load_strategy_config("alpha_v1", project_root_fixture)
        assert isinstance(cfg, dict)
        assert cfg["strategy_id"] == "alpha_v1"
        assert "portfolio" in cfg
        assert "paths" in cfg

    def test_alpha_v1_has_expected_fields(self, project_root_fixture):
        cfg = load_strategy_config("alpha_v1", project_root_fixture)
        assert cfg["display_name"] == "Alpha V1"
        assert cfg["account_id"] == "shadow_alpha_v1"
        assert cfg["universe"] == "csi300"
        assert cfg["portfolio"]["top_n"] == 20
        assert cfg["model_version"] == "alpha_v1_candidate_202605"
        assert "model_dir" not in cfg["paths"]

    def test_explicit_config_path_overrides(self, tmp_path):
        custom = tmp_path / "custom.yaml"
        custom.write_text("custom: true\n")
        cfg = load_strategy_config("ignored", tmp_path, config_path=custom)
        assert cfg == {"custom": True}

    def test_missing_default_config(self):
        with pytest.raises(FileNotFoundError):
            load_strategy_config("nonexistent_strategy", Path("/tmp"))

    def test_missing_explicit_path(self, tmp_path):
        missing = tmp_path / "missing.yaml"
        with pytest.raises(FileNotFoundError):
            load_strategy_config("alpha_v1", tmp_path, config_path=missing)


@pytest.fixture(scope="session")
def project_root_fixture() -> Path:
    """Resolve the actual SysQ project root (2 levels up from tests/)."""
    return Path(__file__).resolve().parent.parent
