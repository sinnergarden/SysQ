import sys
from dataclasses import replace
from pathlib import Path

import pytest

from qsys.feature.registry import FeatureListRegistry
from qsys.research.generators.temporal_validation import (
    TemporalValidationLightGBMSingleLabelGenerator,
)
from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
    build_matrix_jobs,
)
from qsys.research.rolling_window import build_rolling_windows
from qsys.research.signal_pipeline import SignalResearchPipeline
from scripts.research.preheat_feature_cache import main as preheat_feature_cache


REPO = Path(__file__).resolve().parents[2]
CONFIG = (
    REPO / "configs/research/csi1800_multifeature_absolute_return_120d_v2.yaml"
)
PREHOLDOUT_20D_CONFIG = (
    REPO
    / "configs/research/csi1800_multifeature_ridge_total_return_120d_20d_preholdout_v1.yaml"
)
TERMINAL_68W_CONFIG = (
    REPO
    / "configs/research/csi1800_multifeature_ridge_total_return_120d_20d_terminal_68w_v1.yaml"
)


def test_multifeature_protocol_is_frozen_and_holdout_safe() -> None:
    config = RollingResearchConfig.from_file(CONFIG)
    features = FeatureListRegistry.load(config.feature_list_id)
    assert len(features) == len(set(features)) == 97
    assert config.research_protocol["protocol_version"] == "absolute_return_abcd_v1"
    assert config.research_protocol["artifact_contract"] == (
        "rolling_checkpoint_model_diagnostics_v1"
    )
    assert config.research_protocol["require_checkpoint_model_diagnostics"] is True
    assert config.research_protocol["holdout"]["start_date"] == "2025-01-02"
    assert config.labels == [{
        "label_id": "fwd_ret_120d_open_open_raw_pit_csi1800_v1",
        "label_maturity_lag_trading_days": 121,
        "label_maturity_before": "2025-01-02",
        "require_pit_lineage": True,
    }]
    assert len(build_matrix_jobs(config)) == 4


def test_models_share_features_and_strict_temporal_validation() -> None:
    config = RollingResearchConfig.from_file(CONFIG)
    expected_exposures = (
        "Log($circ_mv+1)",
        "ret_60d",
        "Std(($close*$factor)/(Ref($close*$factor, 1)+1e-12)-1, 60)",
        "$amount/(Mean($amount, 60)+1e-12)",
    )
    generators = [
        _create_generator_from_config(
            item,
            feature_list_id=config.feature_list_id,
            use_feature_cache=True,
            feature_cache_root=config.feature_cache_root,
            source_manifest_hash=config.source_manifest_hash,
        )
        for item in config.generators
    ]
    assert generators[0].feature_list_id == generators[1].feature_list_id
    assert all(
        generator.signal_exposure_features == expected_exposures
        for generator in generators
    )
    assert isinstance(
        generators[1], TemporalValidationLightGBMSingleLabelGenerator
    )
    assert (
        "qsys.research.generators.temporal_validation"
        not in generators[0].checkpoint_code_dependencies
    )
    pipeline = SignalResearchPipeline(REPO / "data/research")
    ridge_identity = pipeline._window_checkpoint_base_identity(
        config, config.generators[0], generators[0]
    )
    lightgbm_identity = pipeline._window_checkpoint_base_identity(
        config, config.generators[1], generators[1]
    )
    ridge_dependencies = {
        item["name"] for item in ridge_identity["generator_dependency_code"]
    }
    lightgbm_dependencies = {
        item["name"] for item in lightgbm_identity["generator_dependency_code"]
    }
    assert "qsys.research.generators.temporal_validation" in ridge_dependencies
    assert "qsys.research.generators.lightgbm_single_label" in ridge_dependencies
    assert (
        "qsys.research.generators.lightgbm_single_label"
        in lightgbm_dependencies
    )


def test_profitable_ridge_20d_prefix_is_frozen_to_49_preholdout_windows() -> None:
    config = RollingResearchConfig.from_file(PREHOLDOUT_20D_CONFIG)
    windows = build_rolling_windows(
        config.calendar["start_date"],
        config.calendar["end_date"],
        train_window_days=config.calendar["train_window_days"],
        step_days=config.calendar["step_days"],
        label_maturity_lag_trading_days=121,
    )
    assert len(windows) == 49
    assert len(config.generators) == len(config.transforms) == 1
    assert len(build_matrix_jobs(config)) == 1
    assert config.generators[0]["generator_id"] == "ridge_fixed"
    assert config.transforms[0]["transform_id"] == "identity"
    assert config.research_protocol["holdout"] == {
        "start_date": "2025-01-02",
        "status": "untouched",
        "terminal_extension_end_date": "2026-07-31",
        "terminal_extension_window_count": 68,
        "unlock_rule": "explicit authorization to consume the one-time terminal holdout",
    }


def test_terminal_ridge_config_is_exactly_68_windows_and_locked() -> None:
    config = RollingResearchConfig.from_file(
        TERMINAL_68W_CONFIG,
        allow_locked_holdout_for_inspection=True,
    )
    windows = build_rolling_windows(
        config.calendar["start_date"],
        config.calendar["end_date"],
        train_window_days=config.calendar["train_window_days"],
        step_days=config.calendar["step_days"],
        label_maturity_lag_trading_days=121,
    )
    assert len(windows) == 68
    preholdout = RollingResearchConfig.from_file(PREHOLDOUT_20D_CONFIG)
    preholdout_windows = build_rolling_windows(
        preholdout.calendar["start_date"],
        preholdout.calendar["end_date"],
        train_window_days=preholdout.calendar["train_window_days"],
        step_days=preholdout.calendar["step_days"],
        label_maturity_lag_trading_days=121,
    )
    assert windows[:48] == preholdout_windows[:48]
    assert windows[48].predict_start == preholdout_windows[48].predict_start
    assert windows[48].predict_end == "2025-01-16"
    assert preholdout_windows[48].predict_end == "2024-12-31"
    assert windows[-1].predict_end == "2026-07-31"
    assert len(config.generators) == len(config.transforms) == 1
    with pytest.raises(ValueError, match="overlaps a locked holdout"):
        SignalResearchPipeline._validate_config(config)

    authorized_protocol = dict(config.research_protocol)
    authorized_protocol["holdout"] = {
        **authorized_protocol["holdout"],
        "status": "authorized_terminal_run",
        "authorization_ref": "unit-test-explicit-authorization",
    }
    SignalResearchPipeline._validate_config(
        replace(config, research_protocol=authorized_protocol)
    )


def test_terminal_ridge_preheat_is_locked_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root = tmp_path / "feature_cache"
    monkeypatch.setattr(sys, "argv", [
        "preheat_feature_cache.py",
        "--config",
        str(TERMINAL_68W_CONFIG),
        "--feature-cache-root",
        str(cache_root),
    ])
    with pytest.raises(ValueError, match="overlaps a locked holdout"):
        preheat_feature_cache()
    assert not cache_root.exists()
