from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta

from harness.checks.check_inference_artifact import _canonical_hash as contract_hash
from harness.checks.check_inference_artifact import check_payload
from qsys.signal.model_blend_inference import compute_candidate_hash


def _weekdays(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    final = date.fromisoformat(end)
    result: list[str] = []
    while current <= final:
        if current.weekday() < 5:
            result.append(current.isoformat())
        current += timedelta(days=1)
    return result


OPEN_DATES = _weekdays("2025-09-29", "2026-08-11")


def _maturity_sessions(train_end: str, signal_date: str) -> int:
    return sum(train_end < session <= signal_date for session in OPEN_DATES)


def _check(payload: dict) -> list[str]:
    return check_payload(payload, open_dates=OPEN_DATES)


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
            "run_anchor_at": "2026-08-08T07:27:04Z",
            "market_close_cutoff": "18:00",
            "expected_completed_date": "2026-08-07",
            "execution_rule": "next_open_session",
            "calendar_source": "data/meta.db:trade_cal",
        },
        "universe": "csi800",
        "universe_snapshot_semantics": "current_constituents_snapshot",
        "universe_hash": "c" * 64,
        "config_hash": "a" * 64,
        "feature_list_id": "features_v1",
        "feature_list_hash": "b" * 64,
        "feature_snapshot_hash": "d" * 64,
        "feature_availability": {
            "margin": {
                "source": "tushare.margin_detail",
                "lag_sessions": 1,
                "availability_rule": "previous_open_session",
                "as_of_date": "2026-08-06",
            }
        },
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
                    "maturity_sessions": _maturity_sessions(
                        "2026-04-01", "2026-08-07"
                    ),
                    "ordered_feature_list_hash": "b" * 64,
                    "feature_availability": {
                        "margin": {
                            "source": "tushare.margin_detail",
                            "lag_sessions": 1,
                            "availability_rule": "previous_open_session",
                        }
                    },
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
                    "maturity_sessions": _maturity_sessions(
                        "2025-10-01", "2026-08-07"
                    ),
                    "ordered_feature_list_hash": "b" * 64,
                    "feature_availability": {
                        "margin": {
                            "source": "tushare.margin_detail",
                            "lag_sessions": 1,
                            "availability_rule": "previous_open_session",
                        }
                    },
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
            "feature_missing_ratio": {"f1": 0.0},
            "feature_unique_non_null": {"f1": 800},
            "model_used_features": ["f1"],
            "excessive_missing_features": [],
            "constant_model_used_features": [],
        },
        "candidates": candidates,
    }
    payload["top_k"] = len(candidates)
    payload["source"]["model_bundle_hash"] = contract_hash(
        {
            "bundle_id": payload["source"]["model_bundle_id"],
            "feature_list_id": payload["source"]["feature_list_id"],
            "feature_availability": {
                "margin": {
                    key: value
                    for key, value in payload["feature_availability"]["margin"].items()
                    if key != "as_of_date"
                }
            },
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
    assert _check(_valid_payload()) == []


def test_same_day_execution_is_rejected() -> None:
    payload = _valid_payload()
    payload["execution_date"] = "2026-08-07"
    payload["candidates"][0]["execution_date"] = "2026-08-07"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    violations = _check(payload)
    assert any(
        "signal_date must be before execution_date" in item for item in violations
    )


def test_blend_weight_drift_is_rejected() -> None:
    payload = _valid_payload()
    payload["blend"]["weights"] = {"60d": 0.3, "180d": 0.7}
    violations = _check(payload)
    assert "blend.weights do not match source.models weights" in violations


def test_postclose_data_date_drift_is_rejected() -> None:
    payload = _valid_payload()
    payload["data_date"] = "2026-08-06"
    payload["candidates"][0]["data_date"] = "2026-08-06"
    payload["data_quality"]["feature_snapshot_date"] = "2026-08-06"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert "Post-close CandidateRun data_date must equal signal_date" in _check(payload)


def test_naive_creation_timestamp_is_rejected() -> None:
    payload = _valid_payload()
    payload["created_at"] = "2026-08-08T07:27:04"
    assert "created_at must include a UTC offset" in _check(payload)


def test_run_anchor_must_equal_created_at() -> None:
    payload = _valid_payload()
    payload["date_contract"]["run_anchor_at"] = "2026-08-08T07:27:05Z"
    assert (
        "date_contract.run_anchor_at must equal top-level created_at"
        in _check(payload)
    )


def test_pre_cutoff_run_anchor_is_self_consistent() -> None:
    payload = _valid_payload()
    payload["created_at"] = "2026-08-07T09:59:00Z"
    payload["signal_date"] = "2026-08-06"
    payload["data_date"] = "2026-08-06"
    payload["execution_date"] = "2026-08-07"
    payload["date_contract"]["run_anchor_at"] = payload["created_at"]
    payload["date_contract"]["expected_completed_date"] = "2026-08-06"
    payload["feature_availability"]["margin"]["as_of_date"] = "2026-08-05"
    payload["data_quality"]["feature_snapshot_date"] = "2026-08-06"
    for model in payload["source"]["models"]:
        model["maturity_sessions"] = _maturity_sessions(
            model["train_end"], "2026-08-06"
        )
    candidate = payload["candidates"][0]
    candidate["signal_date"] = "2026-08-06"
    candidate["data_date"] = "2026-08-06"
    candidate["execution_date"] = "2026-08-07"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert _check(payload) == []


def test_candidate_model_weight_drift_is_rejected() -> None:
    payload = _valid_payload()
    payload["candidates"][0]["models"][0]["weight"] = 0.3
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert any(
        "weight does not match source.models" in violation
        for violation in _check(payload)
    )


def test_source_model_tampering_breaks_bundle_hash() -> None:
    payload = _valid_payload()
    payload["source"]["models"][0]["artifact_sha256"]["scale.json"] = "f" * 64
    assert (
        "source.model_bundle_hash does not match pinned model bundle"
        in _check(payload)
    )


def test_candidate_contract_cannot_downgrade_to_legacy() -> None:
    payload = _valid_payload()
    del payload["artifact_type"]
    assert any(
        "artifact_type=candidate_run" in violation for violation in _check(payload)
    )


def test_legacy_payload_without_date_contract_reports_instead_of_crashing() -> None:
    payload = _valid_payload()
    del payload["artifact_type"]
    del payload["date_contract"]
    assert any(
        "artifact_type=candidate_run" in violation for violation in _check(payload)
    )


def test_candidate_tampering_is_rejected() -> None:
    payload = _valid_payload()
    tampered = deepcopy(payload)
    tampered["candidates"][0]["ranking_score"] = 999.0
    assert "candidate_hash does not match candidate content" in _check(tampered)


def test_friday_to_tuesday_execution_is_rejected() -> None:
    payload = _valid_payload()
    payload["execution_date"] = "2026-08-11"
    payload["candidates"][0]["execution_date"] = "2026-08-11"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert any(
        "execution_date is not the next open session" in violation
        for violation in _check(payload)
    )


def test_immature_model_label_is_rejected_independently() -> None:
    payload = _valid_payload()
    model = payload["source"]["models"][1]
    model["train_end"] = "2026-07-31"
    model["maturity_sessions"] = _maturity_sessions("2026-07-31", "2026-08-07")
    assert any(
        "label maturity violation" in violation for violation in _check(payload)
    )


def test_historical_current_snapshot_artifact_is_rejected() -> None:
    payload = _valid_payload()
    payload["signal_date"] = "2026-08-06"
    payload["data_date"] = "2026-08-06"
    payload["execution_date"] = "2026-08-07"
    payload["data_quality"]["feature_snapshot_date"] = "2026-08-06"
    candidate = payload["candidates"][0]
    candidate["signal_date"] = "2026-08-06"
    candidate["data_date"] = "2026-08-06"
    candidate["execution_date"] = "2026-08-07"
    payload["candidate_hash"] = compute_candidate_hash(payload["candidates"])
    assert any(
        "cannot represent a historical signal_date" in violation
        for violation in _check(payload)
    )


def test_input_snapshot_hashes_are_required() -> None:
    payload = _valid_payload()
    del payload["universe_hash"]
    del payload["feature_snapshot_hash"]
    violations = _check(payload)
    assert "CandidateRun universe_hash must be a SHA-256 hex digest" in violations
    assert (
        "CandidateRun feature_snapshot_hash must be a SHA-256 hex digest"
        in violations
    )


def test_ordered_feature_hash_must_match_top_level_hash() -> None:
    payload = _valid_payload()
    payload["source"]["models"][0]["ordered_feature_list_hash"] = "f" * 64
    assert any(
        "ordered_feature_list_hash must match" in violation
        for violation in _check(payload)
    )


def test_pit_artifact_is_rejected_until_provider_exists() -> None:
    payload = _valid_payload()
    payload["universe_snapshot_semantics"] = "pit_constituents_snapshot"
    assert any(
        "PIT universe provider is not implemented" in violation
        for violation in _check(payload)
    )


def test_declared_feature_quality_issues_are_rejected() -> None:
    payload = _valid_payload()
    payload["data_quality"]["constant_model_used_features"] = ["dead_factor"]
    assert (
        "CandidateRun data_quality.constant_model_used_features must be empty"
        in _check(payload)
    )


def test_margin_asof_must_be_exact_previous_open_session() -> None:
    payload = _valid_payload()
    payload["feature_availability"]["margin"]["as_of_date"] = "2026-08-05"
    assert any(
        "margin.as_of_date does not match calendar" in violation
        for violation in _check(payload)
    )


def test_model_margin_availability_must_match_run_contract() -> None:
    payload = _valid_payload()
    payload["source"]["models"][0]["feature_availability"]["margin"][
        "lag_sessions"
    ] = 2
    assert any(
        "feature_availability must match top-level contract" in violation
        for violation in _check(payload)
    )
