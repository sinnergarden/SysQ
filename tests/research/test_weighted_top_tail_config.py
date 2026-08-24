"""Config/factory contracts for the canonical weighted top-tail experiment."""

from pathlib import Path

import pytest

from qsys.research.matrix_job import (
    RollingResearchConfig,
    _create_generator_from_config,
    expand_multi_label_generators,
)
from qsys.research.generators.lightgbm_single_label import LightGBMSingleLabelGenerator


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_weighted_top_tail.yaml"
BASELINE = ROOT / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800.yaml"
MATCHED_CONTROL = ROOT / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_matched_control.yaml"


def test_weighted_config_changes_only_ids_and_policy() -> None:
    weighted = RollingResearchConfig.from_file(CONFIG)
    baseline = RollingResearchConfig.from_file(BASELINE)
    control = RollingResearchConfig.from_file(MATCHED_CONTROL)
    assert weighted.calendar == control.calendar == baseline.calendar
    assert weighted.feature_list_id == control.feature_list_id == baseline.feature_list_id
    assert weighted.source_manifest_hash == control.source_manifest_hash
    assert weighted.transforms == control.transforms == baseline.transforms
    assert weighted.labels == control.labels == baseline.labels

    weighted_params = weighted.generators[0]["params"]
    baseline_params = control.generators[0]["params"]
    for key in ("universe", "n_estimators", "feature_list_id", "pit_membership", "pit_universe_artifact", "labels"):
        assert weighted_params[key] == baseline_params[key]
    assert weighted_params["sample_weight_policy"] == "top_tail_v1"
    assert weighted.experiment_id.endswith("_weighted_top_tail")


def test_multi_label_expansion_preserves_weight_policy() -> None:
    expanded = expand_multi_label_generators(
        [
            {
                "generator_id": "g",
                "type": "multi_label_lightgbm",
                "params": {
                    "sample_weight_policy": "top_tail_v1",
                    "labels": [{"label_id": "fwd_ret_180d_raw_pit_csi1800"}],
                },
            }
        ]
    )
    assert expanded[0]["params"]["sample_weight_policy"] == "top_tail_v1"


def test_factory_consumes_weight_policy_and_rejects_unknown() -> None:
    gen = _create_generator_from_config(
        {
            "generator_id": "g",
            "type": "single_label_lightgbm",
            "params": {
                "label_id": "fwd_ret_180d_raw_pit_csi1800",
                "sample_weight_policy": "top_tail_v1",
            },
        }
    )
    assert isinstance(gen, LightGBMSingleLabelGenerator)
    assert gen.sample_weight_policy == "top_tail_v1"

    with pytest.raises(ValueError, match="unknown keys"):
        _create_generator_from_config(
            {
                "generator_id": "g",
                "type": "single_label_lightgbm",
                "params": {
                    "label_id": "fwd_ret_180d_raw_pit_csi1800",
                    "sample_weight_policy": "top_tail_v1",
                    "sample_weight": 2.0,
                },
            }
        )
