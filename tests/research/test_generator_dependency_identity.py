"""Checkpoint identity coverage for generator code dependencies."""

from pathlib import Path

import hashlib

import pytest

from qsys.research.generators.fixture import FixtureSignalGenerator
from qsys.research.generators.lightgbm_single_label import LightGBMSingleLabelGenerator
from qsys.research.matrix_job import RollingResearchConfig
from qsys.research.signal_pipeline import (
    SignalResearchPipeline,
    _generator_dependency_code_identity,
)


def _config() -> RollingResearchConfig:
    return RollingResearchConfig(
        experiment_id="dependency_identity",
        calendar={"start_date": "2026-01-01", "end_date": "2026-01-05"},
        labels=[{"label_id": "l1"}],
    )


def test_lightgbm_declares_training_dependency() -> None:
    generator = LightGBMSingleLabelGenerator()
    dependencies = _generator_dependency_code_identity(generator)

    repo_root = Path(__file__).resolve().parents[2]
    expected_paths = {
        "qsys.data.adapter": repo_root / "qsys/data/adapter.py",
        "qsys.feature.builder": repo_root / "qsys/feature/builder.py",
        "qsys.feature.groups.value_growth_v3a": (
            repo_root / "qsys/feature/groups/value_growth_v3a.py"
        ),
        "qsys.signal.alpha_v1.training": (
            repo_root / "qsys/signal/alpha_v1/training.py"
        ),
    }
    assert [entry["name"] for entry in dependencies] == sorted(expected_paths)
    assert {
        entry["name"]: entry["sha256"] for entry in dependencies
    } == {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in expected_paths.items()
    }


def test_dependency_hash_changes_checkpoint_identity(tmp_path: Path) -> None:
    dependency = tmp_path / "training_dependency.py"
    dependency.write_text("version = 1\n", encoding="utf-8")

    class Generator:
        checkpoint_code_dependencies = {"training": dependency}

    pipeline = SignalResearchPipeline(str(tmp_path / "research"))
    first = pipeline._window_checkpoint_base_identity(_config(), {}, Generator())
    assert first["generator_dependency_code"][0]["name"] == "training"

    dependency.write_text("version = 2\n", encoding="utf-8")
    second = pipeline._window_checkpoint_base_identity(_config(), {}, Generator())
    assert first["generator_dependency_code"] != second["generator_dependency_code"]


def test_generators_without_dependencies_keep_legacy_identity_shape(tmp_path: Path) -> None:
    pipeline = SignalResearchPipeline(str(tmp_path / "research"))
    identity = pipeline._window_checkpoint_base_identity(
        _config(), {}, FixtureSignalGenerator()
    )
    assert "generator_dependency_code" not in identity


@pytest.mark.parametrize(
    "declared",
    [None, [], {"": Path("missing.py")}, {"bad": object()}],
)
def test_dependency_declaration_is_fail_closed(declared) -> None:
    class Generator:
        checkpoint_code_dependencies = declared

    if declared is None:
        assert _generator_dependency_code_identity(Generator()) == []
    else:
        with pytest.raises(ValueError, match="checkpoint"):
            _generator_dependency_code_identity(Generator())


def test_missing_dependency_file_is_fail_closed(tmp_path: Path) -> None:
    class Generator:
        checkpoint_code_dependencies = {"missing": tmp_path / "absent.py"}

    with pytest.raises(ValueError, match="not a readable file"):
        _generator_dependency_code_identity(Generator())
