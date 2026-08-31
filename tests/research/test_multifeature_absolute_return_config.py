import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

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
TERMINAL_68W_LOCK = TERMINAL_68W_CONFIG.with_suffix(".lock.yaml")


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
    assert sum(
        window.predict_end >= config.research_protocol["holdout"]["start_date"]
        for window in windows
    ) == 20
    assert config.research_protocol["terminal_evaluation"] == {
        "contract": "terminal_final_evaluation_v1",
        "data_use": "one_time_final_evaluation_only",
        "signal_scope": {
            "prediction_start_date": "2025-01-02",
            "prediction_end_date": "2026-07-31",
            "maturity_cutoff_exclusive": "2026-08-01",
            "maturity_contract": (
                "maturity_and_return_end_strictly_before_cutoff_v1"
            ),
        },
        "required_signal_metrics": [
            "IC", "RankIC", "quantile_5", "decile", "Top5", "Top20",
            "Top50", "calendar_year_stability", "frozen_phase_stability",
        ],
        "stability_phases": [
            {
                "phase_id": "terminal_2025",
                "start_date": "2025-01-02",
                "end_date": "2025-12-31",
            },
            {
                "phase_id": "terminal_2026_matured_as_of_cutoff",
                "start_date": "2026-01-01",
                "end_date": "2026-07-31",
            },
        ],
        "required_portfolio_metrics": [
            "total_return", "cagr", "sharpe", "max_drawdown", "turnover",
            "annual_returns", "beta", "alpha_daily", "alpha_annualized",
            "commission", "tax", "total_fee",
        ],
        "portfolio_scope": (
            "one_time_terminal_backtest_without_selection_feedback"
        ),
        "post_terminal_model_or_feature_selection": "forbidden",
        "post_terminal_parameter_selection": "forbidden",
        "required_artifact_contracts": {
            "signal_evaluation": "signal_evaluation_methodology_v2",
            "stability": "signal_evaluation_stability_v1",
            "portfolio": "portfolio_analytics_v3",
            "accounting": "accounting_v1",
        },
    }
    assert config.research_protocol["benchmark_ids"] == [
        "csi800_official_price_index_v1",
        "csi800_pit_float_cap_total_return_proxy_v1",
        "csi1800_pit_float_cap_total_return_proxy_v1",
    ]
    assert len(config.generators) == len(config.transforms) == 1
    assert config.labels == [{
        "label_id": "fwd_ret_120d_open_open_raw_pit_csi1800_v1",
        "label_maturity_lag_trading_days": 121,
        "evaluation_start_date": "2025-01-02",
        "evaluation_end_date": "2026-07-31",
        "label_maturity_before": "2026-08-01",
        "require_pit_lineage": True,
    }]
    params = config.generators[0]["params"]
    assert params["income_sidecar_artifact_id"] == (
        "7775d55750de73d80e1cd30a4cc3fd91d065750867d478a3d9c7f1e8027fbe0b"
    )
    assert "income_sidecar_path" not in params
    assert "income_sidecar_manifest_path" not in params
    assert "/home/liuming" not in TERMINAL_68W_CONFIG.read_text(encoding="utf-8")
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


def test_terminal_readiness_lock_binds_config_feature_source_and_label() -> None:
    lock = yaml.safe_load(TERMINAL_68W_LOCK.read_text(encoding="utf-8"))
    config_sha = hashlib.sha256(TERMINAL_68W_CONFIG.read_bytes()).hexdigest()
    feature_contract = FeatureListRegistry.contract(
        "csi1800_multifeature_120d_v1"
    )

    assert lock["schema_version"] == "terminal_readiness_lock_v1"
    assert lock["terminal_config"]["sha256"] == config_sha
    assert lock["terminal_config"]["report_contract"] == (
        "terminal_final_evaluation_v1"
    )
    assert lock["feature_identity"]["feature_count"] == 97
    assert lock["feature_identity"]["config_sha256"] == feature_contract[
        "feature_list_config_sha256"
    ]
    assert lock["feature_identity"]["ordered_features_sha256"] == (
        feature_contract["features_sha256"]
    )
    assert lock["source_identity"]["pit_universe"]["artifact_id"] == (
        "csi1800_pit_v2"
    )
    assert lock["label_identity"]["label_id"] == (
        "fwd_ret_120d_open_open_raw_pit_csi1800_v1"
    )
    assert lock["holdout"]["status"] == (
        "locked_pending_explicit_authorization"
    )
    assert lock["holdout"]["holdout_consumed"] is False


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
