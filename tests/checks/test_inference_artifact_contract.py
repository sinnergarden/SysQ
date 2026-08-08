from __future__ import annotations

from copy import deepcopy

from harness.checks.check_inference_artifact import _canonical_hash as contract_hash
from harness.checks.check_inference_artifact import check_payload
from qsys.signal.model_blend_inference import compute_candidate_hash


def _valid_payload() -> dict:
    candidates = [
        {
            "ts_code": "000001.SZ",
            "name": "Example",
            "industry": "Bank",
            "rank": 1,
            "ranking_score": 1.25,
            "model_rank_gap": 2,
            "feature_coverage": 0.95,
            "listed_days": 1000,
            "eligibility": {"passed": True, "reasons": []},
            "models": [
                {"tag": "60d", "weight": 0.5, "score": 1.0, "rank": 3},
                {"tag": "180d", "weight": 0.5, "score": 1.5, "rank": 1},
            ],
            "data_date": "2026-08-07",
            "signal_date": "2026-08-07",
            "execution_date": "2026-08-10",
            "strategy_id": "financial_rc",
            "run_id": "infer_financial_rc_20260807_test",
        }
    ]
    payload = {
        "schema_version": 1,
        "artifact_type": "candidate_run",
        "usage": "human_research_only",
        "run_id": "infer_financial_rc_20260807_test",
        "strategy_id": "financial_rc",
        "created_at": "2026-08-08T07:27:04Z",
        "signal_date": "2026-08-07",
        "data_date": "2026-08-07",
        "execution_date": "2026-08-10",
        "date_contract": {
            "mode": "postclose_for_next_open_session",
            "execution_rule": "next_open_session",
        },
        "universe": "csi800",
        "universe_snapshot_semantics": "current_constituents_snapshot",
        "config_hash": "a" * 64,
        "feature_list_id": "features_v1",
        "feature_list_hash": "b" * 64,
        "candidate_hash": compute_candidate_hash(candidates),
        "candidate_count": 1,
        "source": {
            "engine": "pinned_model_blend_v1",
            "model_bundle_id": "bundle_v1",
            "feature_list_id": "features_v1",
            "models": [
                {
                    "tag": "60d",
                    "model_hash": "hash60",
                    "model_dir": "data/research/models/60/hash60",
                    "label_id": "ret60",
                    "feature_list_id": "features_v1",
                    "train_start": "2024-01-01",
                    "train_end": "2026-04-01",
                    "horizon": 60,
                    "weight": 0.5,
                    "artifact_sha256": {
                        "model.txt": "1" * 64,
                        "center.json": "2" * 64,
                        "scale.json": "3" * 64,
                        "meta.json": "4" * 64,
                    },
                },
                {
                    "tag": "180d",
                    "model_hash": "hash180",
                    "model_dir": "data/research/models/180/hash180",
                    "label_id": "ret180",
                    "feature_list_id": "features_v1",
                    "train_start": "2023-01-01",
                    "train_end": "2025-10-01",
                    "horizon": 180,
                    "weight": 0.5,
                    "artifact_sha256": {
                        "model.txt": "5" * 64,
                        "center.json": "6" * 64,
                        "scale.json": "7" * 64,
                        "meta.json": "8" * 64,
                    },
                },
            ],
        },
        "blend": {
            "score_transform": "daily_cs_zscore_unclipped_ddof0",
            "model_tags": ["60d", "180d"],
            "weights": {"60d": 0.5, "180d": 0.5},
        },
        "data_quality": {
            "status": "pass",
            "feature_snapshot_date": "2026-08-07",
        },
        "candidates": candidates,
    }
    payload["top_k"] = len(candidates)
    payload["source"]["model_bundle_hash"] = contract_hash(
        {
            "bundle_id": payload["source"]["model_bundle_id"],
            "feature_list_id": payload["source"]["feature_list_id"],
            "models": [
                {
                    key: model[key]
                    for key in (
                        "tag",
                        "weight",
                        "horizon",
                        "label_id",
                        "model_hash",
                        "model_dir",
                        "artifact_sha256",
                    )
                }
                for model in payload["source"]["models"]
            ],
        }
    )
    return payload


def test_valid_candidate_run_passes() -> None:
    assert check_payload(_valid_payload()) == []


def test_same_day_execution_is_rejected() -> None:
    payload = _valid_payload()
    payload["execution_date"] = "2026-08-07"
    payload["candidates"][0]["execution_date"] = "2026-08-07"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    violations = check_payload(payload)
    assert any(
        "signal_date must be before execution_date" in item for item in violations
    )


def test_blend_weight_drift_is_rejected() -> None:
    payload = _valid_payload()
    payload["blend"]["weights"] = {"60d": 0.3, "180d": 0.7}
    violations = check_payload(payload)
    assert "blend.weights do not match source.models weights" in violations


def test_postclose_data_date_drift_is_rejected() -> None:
    payload = _valid_payload()
    payload["data_date"] = "2026-08-06"
    payload["candidates"][0]["data_date"] = "2026-08-06"
    payload["data_quality"]["feature_snapshot_date"] = "2026-08-06"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert "Post-close CandidateRun data_date must equal signal_date" in check_payload(
        payload
    )


def test_naive_creation_timestamp_is_rejected() -> None:
    payload = _valid_payload()
    payload["created_at"] = "2026-08-08T07:27:04"
    assert "created_at must include a UTC offset" in check_payload(payload)


def test_candidate_model_weight_drift_is_rejected() -> None:
    payload = _valid_payload()
    payload["candidates"][0]["models"][0]["weight"] = 0.3
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert any(
        "weight does not match source.models" in violation
        for violation in check_payload(payload)
    )


def test_source_model_tampering_breaks_bundle_hash() -> None:
    payload = _valid_payload()
    payload["source"]["models"][0]["artifact_sha256"]["scale.json"] = "f" * 64
    assert (
        "source.model_bundle_hash does not match pinned model bundle"
        in check_payload(payload)
    )


def test_candidate_contract_cannot_downgrade_to_legacy() -> None:
    payload = _valid_payload()
    del payload["artifact_type"]
    assert any(
        "require artifact_type=candidate_run" in violation
        for violation in check_payload(payload)
    )


def test_candidate_tampering_is_rejected() -> None:
    payload = _valid_payload()
    tampered = deepcopy(payload)
    tampered["candidates"][0]["ranking_score"] = 999.0
    assert "candidate_hash does not match candidate content" in check_payload(tampered)
