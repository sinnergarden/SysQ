from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from qsys.signal.model_blend_inference import InferenceDates, InferenceRunResult
from qsys.signal.top10_run import (
    Top10RunError,
    _append_registry_entry,
    _canonical_hash,
    _file_sha256,
    _select_model_entry,
    run_top10_signal,
    validate_top10_run_artifact,
)


def test_same_date_retrain_appends_revision_and_selects_newest(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    first = {
        "as_of_date": "2026-08-21",
        "bundle_hash": "a" * 64,
        "bundle_path": "a.json",
        "bundle_file_sha256": "1" * 64,
        "train_start": "2023-01-01",
        "train_end": "2025-11-21",
    }
    registry = {"schema_version": 1, "strategy_id": "s180_top10", "entries": []}
    published_first = _append_registry_entry(registry_path, registry, first)
    second = {**first, "bundle_hash": "b" * 64, "bundle_path": "b.json"}
    published_second = _append_registry_entry(registry_path, registry, second)

    assert published_first["revision"] == 1
    assert published_second["revision"] == 2
    assert _select_model_entry(registry, decision_date="2026-08-21") == published_second


def _config(root: Path) -> dict:
    return {
        "strategy_id": "s180_top10",
        "account_id": "research_s180_top10",
        "top10_run": {
            "retrain_interval_sessions": 20,
            "top_k": 10,
            "refresh_labels_on_retrain": True,
            "label_config": "configs/labels/fwd_ret_180d_raw_pit_csi1800.yaml",
            "model_registry": "data/research/top10/s180_top10/model_registry.json",
            "bundle_root": "data/research/models/bundles/s180_top10",
            "state_root": "runs/top10/s180_top10",
            "membership_snapshot_root": "data/research/universes/csi1800_pit_daily",
            "lock_path": "runs/top10/s180_top10/run.lock",
        },
        "training": {"models": [{"label_id": "l180", "horizon": 180}]},
        "inference": {
            "market_close_cutoff": "18:00",
            "feature_snapshot_lag_sessions": 1,
            "universe_snapshot_semantics": "dated_pit_membership_snapshot",
        },
    }


def _dates() -> InferenceDates:
    return InferenceDates(
        signal_date="2026-08-20",
        data_date="2026-08-20",
        decision_date="2026-08-21",
        execution_date="2026-08-24",
        expected_completed_date="2026-08-21",
    )


def _candidate_payload() -> dict:
    candidates = [
        {
            "rank": index + 1,
            "ts_code": f"{index + 1:06d}.SZ",
            "name": f"N{index + 1}",
            "raw_prediction": float(10 - index),
        }
        for index in range(10)
    ]
    return {
        "signal_date": "2026-08-20",
        "data_date": "2026-08-20",
        "decision_date": "2026-08-21",
        "execution_date": "2026-08-24",
        "candidate_hash": "c" * 64,
        "feature_snapshot_hash": "f" * 64,
        "universe_hash": "u" * 64,
        "source": {"model_bundle_hash": "b" * 64},
        "blend": {"score_transform": "raw_model_prediction"},
        "data_quality": {"eligible_rows": 1798},
        "candidates": candidates,
    }


def _write_candidate(root: Path) -> InferenceRunResult:
    path = (
        root
        / "outputs/2026-08-20/s180_top10/infer_s180_top10_content/candidate_run.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _candidate_payload()
    path.write_text(json.dumps(payload), encoding="utf-8")
    return InferenceRunResult(artifact_path=path, payload=payload)


def _prepare_inputs(root: Path) -> None:
    snapshot = (
        root
        / "data/research/universes/csi1800_pit_daily/20260821/membership.parquet"
    )
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(b"PAR1-test")
    label_config = root / "configs/labels/fwd_ret_180d_raw_pit_csi1800.yaml"
    label_config.parent.mkdir(parents=True, exist_ok=True)
    label_config.write_text("label_id: l180\n", encoding="utf-8")


def test_initial_run_trains_then_publishes_verified_top10(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    inference_result = _write_candidate(tmp_path)
    entry = {
        "as_of_date": "2026-08-21",
        "bundle_hash": "b" * 64,
        "bundle_path": "data/research/models/bundles/s180_top10/b.json",
        "bundle_file_sha256": "d" * 64,
        "train_start": "2023-10-26",
        "train_end": "2025-11-21",
    }
    bundle = {"bundle_hash": "b" * 64, "models": [{"tag": "180d"}]}
    label_path = tmp_path / "data/research/labels/l180/labels.parquet"
    label_path.parent.mkdir(parents=True, exist_ok=True)
    label_path.write_bytes(b"labels")

    with patch("qsys.signal.top10_run.load_open_dates", return_value=["2026-08-20", "2026-08-21", "2026-08-24"]), \
         patch("qsys.signal.top10_run.resolve_inference_dates", return_value=_dates()), \
         patch("qsys.signal.top10_run._refresh_label", return_value=label_path), \
         patch("qsys.signal.top10_run._train_and_register", return_value=(entry, bundle)) as train, \
         patch("qsys.signal.top10_run._inference_config", return_value={}), \
         patch("qsys.signal.top10_run.run_candidate_inference", return_value=inference_result):
        result = run_top10_signal(
            strategy_config=_config(tmp_path),
            project_root=tmp_path,
            now=datetime(2026, 8, 21, 20, 0),
        )

    assert train.call_count == 1
    assert result.payload["status"] == "complete"
    assert result.payload["training"]["retrained"] is True
    assert len(result.payload["top10"]) == 10
    assert validate_top10_run_artifact(result.artifact_path)["run_identity"]
    state = json.loads(
        (tmp_path / "runs/top10/s180_top10/2026-08-21/state.json").read_text()
    )
    assert state["stage"] == "complete"
    assert state["status"] == "complete"


def test_model_inside_20_sessions_is_reused(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    inference_result = _write_candidate(tmp_path)
    bundle = {"models": [{"tag": "180d"}]}
    bundle["bundle_hash"] = _canonical_hash(bundle)
    inference_result.payload["source"]["model_bundle_hash"] = bundle["bundle_hash"]
    inference_result.artifact_path.write_text(
        json.dumps(inference_result.payload), encoding="utf-8"
    )
    bundle_path = tmp_path / "data/research/models/bundles/s180_top10/b.json"
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    entry = {
        "as_of_date": "2026-08-20",
        "bundle_hash": bundle["bundle_hash"],
        "bundle_path": str(bundle_path.relative_to(tmp_path)),
        "bundle_file_sha256": _file_sha256(bundle_path),
        "train_start": "2023-10-25",
        "train_end": "2025-11-20",
    }
    registry_path = tmp_path / "data/research/top10/s180_top10/model_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {"schema_version": 1, "strategy_id": "s180_top10", "entries": [entry]}
        ),
        encoding="utf-8",
    )

    with patch("qsys.signal.top10_run.load_open_dates", return_value=["2026-08-20", "2026-08-21", "2026-08-24"]), \
         patch("qsys.signal.top10_run.resolve_inference_dates", return_value=_dates()), \
         patch("qsys.signal.top10_run._refresh_label") as refresh, \
         patch("qsys.signal.top10_run._train_and_register") as train, \
         patch("qsys.signal.top10_run._inference_config", return_value={}), \
         patch("qsys.signal.top10_run.run_candidate_inference", return_value=inference_result):
        result = run_top10_signal(
            strategy_config=_config(tmp_path), project_root=tmp_path
        )

    refresh.assert_not_called()
    train.assert_not_called()
    assert result.payload["training"] == {
        "retrained": False,
        "reason": "model_reused:1/20",
        "sessions_since_model": 1,
    }


def test_force_retrain_requires_reason(tmp_path: Path) -> None:
    with pytest.raises(Top10RunError, match="requires a non-empty reason"):
        run_top10_signal(
            strategy_config=_config(tmp_path),
            project_root=tmp_path,
            force_retrain=True,
        )


def test_validator_rejects_unsorted_raw_predictions(tmp_path: Path) -> None:
    _prepare_inputs(tmp_path)
    inference_result = _write_candidate(tmp_path)
    payload = _candidate_payload()
    candidate_path = inference_result.artifact_path
    artifact = candidate_path.with_name("top10_run.json")
    top10 = [
        {
            "rank": row["rank"],
            "ts_code": row["ts_code"],
            "name": row["name"],
            "raw_prediction": row["raw_prediction"],
        }
        for row in payload["candidates"]
    ]
    top10[0]["raw_prediction"], top10[1]["raw_prediction"] = (
        top10[1]["raw_prediction"],
        top10[0]["raw_prediction"],
    )
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "artifact_type": "s180_top10_signal_run",
                "status": "complete",
                "strategy_id": "s180_top10",
                "run_identity": "r" * 64,
                "signal_date": "2026-08-20",
                "data_date": "2026-08-20",
                "decision_date": "2026-08-21",
                "execution_date": "2026-08-24",
                "candidate_artifact": str(candidate_path.relative_to(tmp_path)),
                "candidate_artifact_sha256": _file_sha256(candidate_path),
                "model": {"bundle_hash": "b" * 64},
                "quality_gate": {
                    "candidate_hash": "c" * 64,
                    "score_transform": "raw_model_prediction",
                },
                "top10": top10,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Top10RunError, match="not sorted"):
        validate_top10_run_artifact(artifact)
