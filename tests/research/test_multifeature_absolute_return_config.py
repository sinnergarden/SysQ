from pathlib import Path

from qsys.feature.registry import FeatureListRegistry
from qsys.research.generators.temporal_validation import (
    TemporalValidationLightGBMSingleLabelGenerator,
)
from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
    build_matrix_jobs,
)


REPO = Path(__file__).resolve().parents[2]
CONFIG = (
    REPO / "configs/research/csi1800_multifeature_absolute_return_120d_v2.yaml"
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
