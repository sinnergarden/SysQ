from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from qsys.signal.model_blend_inference import (
    InferenceContractError,
    _atomic_write_json,
    compute_candidate_hash,
    compute_feature_snapshot_hash,
    compute_universe_hash,
    load_model_lineage,
    load_universe_snapshot_members,
    profile_feature_quality,
    resolve_inference_dates,
    validate_ordered_model_features,
    validate_training_feature_lineage,
    validate_inference_config,
)

OPEN_DATES = [
    "2026-04-27",
    "2026-04-28",
    "2026-04-29",
    "2026-04-30",
    "2026-05-06",
    "2026-08-06",
    "2026-08-07",
    "2026-08-10",
    "2026-08-11",
]


def _config(model_root: Path) -> dict:
    models = []
    for tag, horizon, model_hash in (("60d", 2, "hash60"), ("180d", 3, "hash180")):
        model_dir = model_root / tag / model_hash
        model_dir.mkdir(parents=True)
        for filename in ("model.txt", "center.json", "scale.json"):
            (model_dir / filename).write_text("{}", encoding="utf-8")
        (model_dir / "meta.json").write_text(
            json.dumps(
                {
                    "model_hash": model_hash,
                    "feature_list_id": "features_v1",
                    "label_id": f"label_{tag}",
                    "universe": "csi800",
                    "horizon": horizon,
                    "train_start": "2026-04-27",
                    "train_end": "2026-04-27",
                }
            ),
            encoding="utf-8",
        )
        artifact_sha256 = {
            filename: hashlib.sha256((model_dir / filename).read_bytes()).hexdigest()
            for filename in ("model.txt", "center.json", "scale.json", "meta.json")
        }
        models.append(
            {
                "tag": tag,
                "model_hash": model_hash,
                "artifact_id": model_hash,
                "model_dir": str(model_dir.relative_to(model_root.parents[2])),
                "label_id": f"label_{tag}",
                "horizon": horizon,
                "weight": 0.5,
                "artifact_sha256": artifact_sha256,
            }
        )
    return {
        "strategy_id": "financial_rc",
        "universe": "csi800",
        "feature_set": "features_v1",
        "inference": {
            "engine": "pinned_model_blend_v1",
            "feature_list_id": "features_v1",
            "model_bundle": {"bundle_id": "bundle_v1", "models": models},
        },
    }


def test_resolves_friday_close_to_monday_execution() -> None:
    dates = resolve_inference_dates(
        "2026-08-07",
        None,
        OPEN_DATES,
        now=datetime.fromisoformat("2026-08-08T12:00:00+08:00"),
    )
    assert dates.signal_date == "2026-08-07"
    assert dates.data_date == "2026-08-07"
    assert dates.decision_date == "2026-08-07"
    assert dates.execution_date == "2026-08-10"


def test_monday_decision_uses_aligned_friday_snapshot_and_tuesday_execution() -> None:
    dates = resolve_inference_dates(
        "2026-08-07",
        None,
        OPEN_DATES,
        now=datetime.fromisoformat("2026-08-10T19:00:00+08:00"),
        feature_snapshot_lag_sessions=1,
    )
    assert dates.signal_date == "2026-08-07"
    assert dates.data_date == "2026-08-07"
    assert dates.decision_date == "2026-08-10"
    assert dates.execution_date == "2026-08-11"


def test_auto_before_cutoff_uses_previous_completed_session() -> None:
    dates = resolve_inference_dates(
        "auto",
        None,
        OPEN_DATES,
        now=datetime.fromisoformat("2026-08-07T10:00:00+08:00"),
    )
    assert dates.signal_date == "2026-08-06"
    assert dates.execution_date == "2026-08-07"


def test_rejects_same_day_execution() -> None:
    with pytest.raises(InferenceContractError, match="next open session"):
        resolve_inference_dates(
            "2026-08-07",
            "2026-08-07",
            OPEN_DATES,
            now=datetime.fromisoformat("2026-08-08T12:00:00+08:00"),
        )


def test_rejects_date_outside_configured_aligned_snapshot_boundary() -> None:
    with pytest.raises(InferenceContractError, match="aligned feature session"):
        resolve_inference_dates(
            "2026-08-06",
            None,
            OPEN_DATES,
            now=datetime.fromisoformat("2026-08-08T12:00:00+08:00"),
            universe_snapshot_semantics="current_constituents_snapshot",
        )


def test_pit_universe_semantics_are_rejected_until_provider_exists() -> None:
    with pytest.raises(InferenceContractError, match="historical PIT inference"):
        resolve_inference_dates(
            "2026-08-07",
            None,
            OPEN_DATES,
            now=datetime.fromisoformat("2026-08-08T12:00:00+08:00"),
            universe_snapshot_semantics="pit_constituents_snapshot",
        )


def test_validates_pinned_bundle_and_maturity(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    settings = validate_inference_config("financial_rc", config, tmp_path)
    lineage = load_model_lineage(settings, "2026-08-07", OPEN_DATES)
    assert settings["bundle_id"] == "bundle_v1"
    assert [item["tag"] for item in lineage] == ["60d", "180d"]


def test_rejects_non_unit_model_weights(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["model_bundle"]["models"][0]["weight"] = 0.7
    with pytest.raises(InferenceContractError, match="sum to 1.0"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_model_bundle_accepts_one_model(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["model_bundle"]["models"] = [
        config["inference"]["model_bundle"]["models"][0]
    ]
    config["inference"]["model_bundle"]["models"][0]["weight"] = 1.0
    settings = validate_inference_config("financial_rc", config, tmp_path)
    assert len(settings["models"]) == 1
    assert settings["require_complete_universe_features"] is True
    assert settings["idempotent_reuse"] is False


def test_allows_incomplete_qlib_rows_when_explicitly_configured(
    tmp_path: Path,
) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["require_complete_universe_features"] = False
    config["inference"]["idempotent_reuse"] = True
    settings = validate_inference_config("financial_rc", config, tmp_path)
    assert settings["require_complete_universe_features"] is False
    assert settings["idempotent_reuse"] is True


def test_rejects_non_boolean_new_inference_flags(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["idempotent_reuse"] = "true"
    with pytest.raises(InferenceContractError, match="must be boolean"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_raw_model_prediction_requires_single_unit_weight_model(
    tmp_path: Path,
) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["score_transform"] = "raw_model_prediction"
    with pytest.raises(InferenceContractError, match="exactly one model"):
        validate_inference_config("financial_rc", config, tmp_path)

    config["inference"]["model_bundle"]["models"] = [
        config["inference"]["model_bundle"]["models"][0]
    ]
    config["inference"]["model_bundle"]["models"][0]["weight"] = 1.0
    settings = validate_inference_config("financial_rc", config, tmp_path)
    assert settings["score_transform"] == "raw_model_prediction"


@pytest.mark.parametrize("value", [True, -1, "invalid"])
def test_rejects_invalid_feature_snapshot_lag(tmp_path: Path, value: object) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["feature_snapshot_lag_sessions"] = value
    with pytest.raises(InferenceContractError, match="non-negative integer"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_rejects_pit_config_until_provider_exists(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    config["inference"]["universe_snapshot_semantics"] = "pit_constituents_snapshot"
    with pytest.raises(InferenceContractError, match="provider is not implemented"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_dated_pit_semantics_accepts_current_expected_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    snapshot = tmp_path / "data" / "research" / "universe_2026-08-07.parquet"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"parquet")
    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda path: pd.DataFrame({"instrument": ["A", "B"]}),
    )
    config["inference"].update(
        {
            "universe_snapshot_semantics": "dated_pit_membership_snapshot",
            "universe_snapshot_path": str(snapshot.relative_to(tmp_path)),
        }
    )
    settings = validate_inference_config("financial_rc", config, tmp_path)
    assert settings["universe_snapshot_path"].endswith("2026-08-07.parquet")
    dates = resolve_inference_dates(
        "2026-08-07",
        None,
        OPEN_DATES,
        now=datetime.fromisoformat("2026-08-08T12:00:00+08:00"),
        universe_snapshot_semantics="dated_pit_membership_snapshot",
    )
    assert dates.signal_date == "2026-08-07"


def test_dated_pit_path_cannot_select_arbitrary_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "data" / "research" / "universe_2026-08-06.parquet"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"parquet")
    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda path: pd.DataFrame({"instrument": ["A"]}),
    )
    with pytest.raises(InferenceContractError, match="expected snapshot date"):
        load_universe_snapshot_members(
            tmp_path,
            "csi1800",
            "2026-08-07",
            universe_snapshot_semantics="dated_pit_membership_snapshot",
            universe_snapshot_path=str(snapshot.relative_to(tmp_path)),
        )


@pytest.mark.parametrize("bad_name", ["latest.parquet", "null.parquet"])
def test_rejects_non_explicit_dated_snapshot_path(
    tmp_path: Path, bad_name: str
) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    snapshot = tmp_path / "data" / "research" / f"universe_2026-08-07_{bad_name}"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"parquet")
    config["inference"].update(
        {
            "universe_snapshot_semantics": "dated_pit_membership_snapshot",
            "universe_snapshot_path": str(snapshot.relative_to(tmp_path)),
        }
    )
    with pytest.raises(InferenceContractError):
        validate_inference_config("financial_rc", config, tmp_path)


def test_rejects_duplicate_dated_snapshot_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    snapshot = tmp_path / "data" / "research" / "universe_2026-08-07.parquet"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"parquet")
    monkeypatch.setattr(
        pd,
        "read_parquet",
        lambda path: pd.DataFrame({"instrument": ["A", "A"]}),
    )
    config["inference"].update(
        {
            "universe_snapshot_semantics": "dated_pit_membership_snapshot",
            "universe_snapshot_path": str(snapshot.relative_to(tmp_path)),
        }
    )
    with pytest.raises(InferenceContractError, match="duplicate"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_rejects_symlinked_dated_snapshot_path(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    snapshot_root = tmp_path / "data" / "research"
    real = tmp_path / "real_2026-08-07.parquet"
    real.write_bytes(b"parquet")
    snapshot = snapshot_root / "universe_2026-08-07.parquet"
    snapshot.symlink_to(real)
    config["inference"].update(
        {
            "universe_snapshot_semantics": "dated_pit_membership_snapshot",
            "universe_snapshot_path": str(snapshot.relative_to(tmp_path)),
        }
    )
    with pytest.raises(InferenceContractError, match="symlink"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_rejects_mutated_scaler_artifact(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    scaler = model_root / "60d" / "hash60" / "scale.json"
    scaler.write_text('{"mutated": true}', encoding="utf-8")
    with pytest.raises(InferenceContractError, match="artifact digest mismatch"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_rejects_symlink_in_model_path(tmp_path: Path) -> None:
    model_root = tmp_path / "data" / "research" / "models"
    config = _config(model_root)
    real_tag_dir = model_root / "60d_real"
    (model_root / "60d").rename(real_tag_dir)
    (model_root / "60d").symlink_to(real_tag_dir, target_is_directory=True)
    with pytest.raises(InferenceContractError, match="must not contain symlinks"):
        validate_inference_config("financial_rc", config, tmp_path)


def test_candidate_hash_ignores_attempt_id() -> None:
    left = [{"ts_code": "000001.SZ", "rank": 1, "run_id": "attempt-a"}]
    right = [{"ts_code": "000001.SZ", "rank": 1, "run_id": "attempt-b"}]
    assert compute_candidate_hash(left) == compute_candidate_hash(right)


def test_universe_hash_is_order_independent_and_membership_sensitive() -> None:
    assert compute_universe_hash(["B", "A"]) == compute_universe_hash(["A", "B"])
    assert compute_universe_hash(["A", "B"]) != compute_universe_hash(["A", "C"])


def test_feature_snapshot_hash_is_canonical_and_value_sensitive() -> None:
    frame = pd.DataFrame(
        {
            "instrument": ["B", "A"],
            "f1": [1.0, float("nan")],
            "f2": [-0.0, 2.5],
        }
    )
    reordered = frame.iloc[::-1].reset_index(drop=True)
    assert compute_feature_snapshot_hash(frame, ["f1", "f2"]) == (
        compute_feature_snapshot_hash(reordered, ["f1", "f2"])
    )
    changed = frame.copy()
    changed.loc[0, "f1"] = 1.0000000000000002
    assert compute_feature_snapshot_hash(frame, ["f1", "f2"]) != (
        compute_feature_snapshot_hash(changed, ["f1", "f2"])
    )


def test_loads_exact_active_universe_membership(tmp_path: Path) -> None:
    snapshot = tmp_path / "data" / "qlib_bin" / "instruments" / "csi800.txt"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text(
        "A\t2026-01-01\t2026-08-07\n"
        "B\t2026-08-07\t2026-12-31\n"
        "C\t2025-01-01\t2026-08-06\n",
        encoding="utf-8",
    )
    assert load_universe_snapshot_members(tmp_path, "csi800", "2026-08-07") == [
        "A",
        "B",
    ]


def test_feature_quality_fails_closed_on_dead_model_inputs() -> None:
    frame = pd.DataFrame(
        {
            "live": [1.0, 2.0, 3.0],
            "all_missing": [float("nan")] * 3,
            "dead": [0.0, 0.0, 0.0],
        }
    )
    quality = profile_feature_quality(
        frame,
        ["live", "all_missing", "dead"],
        {"live", "dead"},
        max_missing_ratio=0.95,
        min_model_used_unique_values=2,
    )
    assert quality["excessive_missing_features"] == ["all_missing"]
    assert quality["constant_model_used_features"] == ["dead"]


def test_same_feature_set_in_different_order_is_rejected() -> None:
    with pytest.raises(InferenceContractError, match="ordered feature contract"):
        validate_ordered_model_features(
            "60d",
            ["roe", "margin", "value"],
            ["margin", "roe", "value"],
            ["roe", "margin", "value"],
        )


def test_schema_v2_training_feature_order_is_pinned(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    ordered = ["roe", "margin", "value"]
    feature_hash = hashlib.sha256(
        json.dumps(
            ordered,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (model_dir / "meta.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "ordered_features": ordered,
                "feature_list_hash": feature_hash,
            }
        ),
        encoding="utf-8",
    )
    spec = {"tag": "60d", "resolved_model_dir": model_dir}

    validate_training_feature_lineage([spec], ordered)
    with pytest.raises(InferenceContractError, match="training ordered feature"):
        validate_training_feature_lineage(
            [spec], ["margin", "roe", "value"]
        )


def test_artifact_write_never_overwrites(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate_run.json"
    _atomic_write_json(artifact, {"attempt": 1})
    with pytest.raises(FileExistsError, match="already exists"):
        _atomic_write_json(artifact, {"attempt": 2})
    assert json.loads(artifact.read_text(encoding="utf-8")) == {"attempt": 1}
