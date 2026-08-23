from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from qsys.feature.registry import FeatureListRegistry
from qsys.feature.library import FeatureLibrary
from qsys.research.matrix_job import RollingResearchConfig
from qsys.research.rolling_window import build_rolling_windows


REPO_ROOT = Path(__file__).resolve().parents[2]
ALPHA_V1_CLEAN227_SHA256 = (
    "37d3a149b4454b63afe953db8ffde3d61ee291b232407049855aaea0bd009cc3"
)
QLIB_SOURCE_SHA256 = (
    "780ba40ec5ce8a5f5b5c88b02e1e171e0d67c3c2b02639b22ed0a2b889abc237"
)


def test_alpha_v1_clean227_contract_is_frozen_from_semantic360() -> None:
    from qsys.strategy.alpha_v1.spec import get_clean_features

    path = REPO_ROOT / "configs/features/alpha_v1_clean_227_frozen.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    features = FeatureListRegistry.load("alpha_v1_clean_227_frozen")
    candidates = FeatureLibrary.get_semantic_all_features_config()

    assert payload["candidate_feature_count"] == len(candidates) == 360
    assert payload["feature_count"] == 227
    assert len(features) == len(set(features)) == 227
    assert features == get_clean_features(candidates)
    assert payload["source_artifact_sha256"] == ALPHA_V1_CLEAN227_SHA256
    canonical = json.dumps(features, indent=2).encode("utf-8")
    assert hashlib.sha256(canonical).hexdigest() == ALPHA_V1_CLEAN227_SHA256
    contract = FeatureListRegistry.contract("alpha_v1_clean_227_frozen")
    assert contract["feature_count"] == 227
    assert contract["features_sha256"] == ALPHA_V1_CLEAN227_SHA256
    assert contract["source_artifact_sha256"] == ALPHA_V1_CLEAN227_SHA256


@pytest.mark.parametrize(
    ("override", "error"),
    [
        ({"feature_count": 3}, "count mismatch"),
        ({"source_artifact_sha256": "0" * 64}, "SHA-256 mismatch"),
    ],
)
def test_feature_contract_rejects_tampered_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: dict[str, object],
    error: str,
) -> None:
    payload: dict[str, object] = {
        "feature_list_id": "frozen",
        "feature_count": 2,
        "features": ["$close", "$volume"],
    }
    payload.update(override)
    (tmp_path / "frozen.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    monkeypatch.setattr(FeatureListRegistry, "_CONFIG_DIR", tmp_path)

    with pytest.raises(ValueError, match=error):
        FeatureListRegistry.load("frozen")


@pytest.mark.parametrize(
    "invalid_count", ["2", 2.0, True]
)
def test_feature_contract_rejects_non_integer_declared_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_count: object,
) -> None:
    payload = {
        "feature_list_id": "frozen",
        "feature_count": invalid_count,
        "features": ["$close", "$volume"],
    }
    (tmp_path / "frozen.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    monkeypatch.setattr(FeatureListRegistry, "_CONFIG_DIR", tmp_path)

    with pytest.raises(TypeError, match="feature_count must be int"):
        FeatureListRegistry.load("frozen")


def test_legacy_feature_list_without_declared_hash_is_still_content_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "feature_list_id": "legacy",
        "features": ["$close", "$volume"],
    }
    (tmp_path / "legacy.yaml").write_text(
        yaml.safe_dump(payload), encoding="utf-8"
    )
    monkeypatch.setattr(FeatureListRegistry, "_CONFIG_DIR", tmp_path)

    contract = FeatureListRegistry.contract("legacy")
    assert contract["source_artifact_sha256"] is None
    assert contract["source_artifact_sha256_declared"] is False
    assert len(contract["features_sha256"]) == 64


def test_alpha_v1_20d_pit_label_contract() -> None:
    path = REPO_ROOT / "configs/labels/fwd_ret_20d_raw_pit.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert payload["label_id"] == "fwd_ret_20d_raw_pit"
    assert payload["formula"] == {
        "type": "forward_return",
        "horizon": 20,
        "price": "close",
        "price_basis": "adjusted_close",
        "adjustment_factor": "factor",
    }
    assert payload["universe"] == "csi800_pit_union"
    assert payload["pit_universe_artifact"] == "csi800_pit_v2"
    assert payload["require_clean_provenance"] is True


def test_alpha_v1_clean227_20d_config_changes_only_signal_contract() -> None:
    path = (
        REPO_ROOT
        / "configs/research/alpha_v1_20d_rolling_2y_to_202607_pit.yaml"
    )
    config = RollingResearchConfig.from_file(path)
    params = config.generators[0]["params"]

    assert config.calendar == {
        "start_date": "2021-01-01",
        "end_date": "2026-07-31",
        "train_window_days": 504,
        "step_days": 20,
    }
    assert config.experiment_id == "alpha_v1_clean227_20d_rolling_2y_to_202607_pit"
    assert config.feature_list_id == "alpha_v1_clean_227_frozen"
    assert config.source_manifest_hash == QLIB_SOURCE_SHA256
    assert config.window_checkpoints is True
    assert params["universe"] == "csi800_pit_union"
    assert params["feature_list_id"] == "alpha_v1_clean_227_frozen"
    assert params["pit_membership"] is True
    assert params["pit_universe_artifact"] == "csi800_pit_v2"
    assert params["labels"] == [{"label_id": "fwd_ret_20d_raw_pit"}]
    assert "blend_weights" not in params
    assert config.labels == [
        {
            "label_id": "fwd_ret_20d_raw_pit",
            "label_maturity_lag_trading_days": 21,
        }
    ]
    assert params["n_estimators"] == 300
    assert "lgb_params" not in params

    windows = build_rolling_windows(
        config.calendar["start_date"],
        config.calendar["end_date"],
        train_window_days=config.calendar["train_window_days"],
        step_days=config.calendar["step_days"],
        label_maturity_lag_trading_days=21,
    )
    assert windows[0].predict_start == "2021-01-04"
    assert windows[-1].predict_end == "2026-07-31"
    assert len(windows) == 68


def test_alpha_v1_clean227_matches_pit_phase0_pipeline_except_signal() -> None:
    clean227 = RollingResearchConfig.from_file(
        REPO_ROOT
        / "configs/research/alpha_v1_20d_rolling_2y_to_202607_pit.yaml"
    )
    baseline = RollingResearchConfig.from_file(
        REPO_ROOT
        / "configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit.yaml"
    )

    assert clean227.calendar == baseline.calendar
    assert clean227.signal["score_column"] == baseline.signal["score_column"]
    assert clean227.transforms == baseline.transforms

    clean227_params = dict(clean227.generators[0]["params"])
    baseline_params = dict(baseline.generators[0]["params"])
    assert set(clean227_params) == set(baseline_params)
    for key in set(clean227_params) - {"feature_list_id", "labels"}:
        assert clean227_params[key] == baseline_params[key]

    assert clean227_params["feature_list_id"] == (
        "alpha_v1_clean_227_frozen"
    )
    assert clean227_params["labels"] == [
        {"label_id": "fwd_ret_20d_raw_pit"}
    ]
