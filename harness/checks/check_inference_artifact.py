#!/usr/bin/env python3
"""Validate inference provenance, date semantics, and candidate integrity."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.signal.model_blend_inference import (
    InferenceContractError,
    load_open_dates,
    resolve_expected_completed_session,
    resolve_next_open_session,
    validate_label_maturity,
)
from qsys.feature.availability import (
    MARGIN_SOURCE,
    normalise_feature_availability,
    resolve_lagged_open_session,
)
from qsys.feature.freshness import normalise_shareholder_freshness

REQUIRED_TOP_LEVEL = {
    "run_id",
    "strategy_id",
    "signal_date",
    "data_date",
    "decision_date",
    "execution_date",
    "created_at",
}


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_hash(candidates: list[dict[str, Any]]) -> str:
    material = [
        {
            key: value
            for key, value in row.items()
            if key not in {"run_id", "created_at"}
        }
        for row in candidates
    ]
    return _canonical_hash(material)


def _date(value: Any, field: str, violations: list[str]) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        violations.append(f"Invalid {field}: {value!r}; expected YYYY-MM-DD")
        return None


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text.lower())


def _contains_latest(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_latest(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_latest(item) for item in value)
    return isinstance(value, str) and "latest" in value.lower()


def check_payload(
    payload: Any,
    *,
    open_dates: Sequence[str] | None = None,
) -> list[str]:
    """Return all contract violations found in an inference payload."""

    violations: list[str] = []
    if not isinstance(payload, dict):
        return ["Top-level must be a JSON object (dict)"]

    is_candidate_run = payload.get("artifact_type") == "candidate_run"
    if not is_candidate_run:
        violations.append("Inference artifact must use artifact_type=candidate_run")
    if payload.get("schema_version") != 1:
        violations.append("CandidateRun schema_version must be 1")
    if payload.get("usage") != "human_research_only":
        violations.append("CandidateRun usage must be human_research_only")

    for field in sorted(REQUIRED_TOP_LEVEL):
        if payload.get(field) in (None, ""):
            violations.append(f"Missing top-level field: {field}")

    signal_date = _date(payload.get("signal_date"), "signal_date", violations)
    data_date = _date(payload.get("data_date"), "data_date", violations)
    decision_date = _date(payload.get("decision_date"), "decision_date", violations)
    execution_date = _date(payload.get("execution_date"), "execution_date", violations)
    if data_date and signal_date and data_date > signal_date:
        violations.append(
            f"Lookahead violation: data_date={data_date} is after signal_date={signal_date}"
        )
    if signal_date and execution_date and signal_date >= execution_date:
        violations.append(
            "Date semantic violation: signal_date must be before execution_date "
            f"({signal_date} >= {execution_date})"
        )
    if signal_date and decision_date and signal_date > decision_date:
        violations.append(
            "Date semantic violation: signal_date must not be after decision_date "
            f"({signal_date} > {decision_date})"
        )
    if decision_date and execution_date and decision_date >= execution_date:
        violations.append(
            "Date semantic violation: decision_date must be before execution_date "
            f"({decision_date} >= {execution_date})"
        )

    created_at: datetime | None = None
    try:
        created_at = datetime.fromisoformat(
            str(payload.get("created_at", "")).replace("Z", "+00:00")
        )
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            violations.append("created_at must include a UTC offset")
    except ValueError:
        violations.append(
            f"Invalid created_at timestamp: {payload.get('created_at')!r}"
        )

    expected_completed_date: str | None = None
    market_close_cutoff = ""
    feature_snapshot_lag_sessions: int | None = None
    date_contract = payload.get("date_contract")
    if isinstance(date_contract, dict):
        if date_contract.get("execution_rule") != "next_open_session":
            violations.append("date_contract.execution_rule must be next_open_session")
        if date_contract.get("mode") != "aligned_feature_snapshot_for_next_open_session":
            violations.append(
                "date_contract.mode must be "
                "aligned_feature_snapshot_for_next_open_session"
            )
        if date_contract.get("run_anchor_at") != payload.get("created_at"):
            violations.append(
                "date_contract.run_anchor_at must equal top-level created_at"
            )
        expected_completed_date = _date(
            date_contract.get("expected_completed_date"),
            "date_contract.expected_completed_date",
            violations,
        )
        market_close_cutoff = str(
            date_contract.get("market_close_cutoff") or ""
        )
        try:
            feature_snapshot_lag_sessions = int(
                date_contract.get("feature_snapshot_lag_sessions")
            )
            if feature_snapshot_lag_sessions < 0 or isinstance(
                date_contract.get("feature_snapshot_lag_sessions"), bool
            ):
                raise ValueError
        except (TypeError, ValueError):
            feature_snapshot_lag_sessions = None
            violations.append(
                "date_contract.feature_snapshot_lag_sessions must be a "
                "non-negative integer"
            )
        if not market_close_cutoff:
            violations.append("date_contract.market_close_cutoff is required")
        if date_contract.get("calendar_source") != "data/meta.db:trade_cal":
            violations.append(
                "date_contract.calendar_source must be data/meta.db:trade_cal"
            )
    elif is_candidate_run:
        violations.append("CandidateRun missing date_contract")
    if (
        is_candidate_run
        and isinstance(date_contract, dict)
        and date_contract.get("mode")
        == "aligned_feature_snapshot_for_next_open_session"
        and data_date
        and signal_date
        and data_date != signal_date
    ):
        violations.append("Aligned CandidateRun data_date must equal signal_date")

    sessions = sorted(set(open_dates or []))
    if is_candidate_run and not sessions:
        violations.append(
            "Independent calendar validation requires authoritative open dates"
        )
    if sessions and decision_date and execution_date:
        try:
            expected_execution = resolve_next_open_session(decision_date, sessions)
            if execution_date != expected_execution:
                violations.append(
                    "execution_date is not the next open session: "
                    f"expected={expected_execution}, got={execution_date}"
                )
        except InferenceContractError as exc:
            violations.append(f"Cannot validate next-open execution: {exc}")
    if sessions and created_at and market_close_cutoff:
        try:
            independently_completed = resolve_expected_completed_session(
                sessions,
                now=created_at,
                market_close_cutoff=market_close_cutoff,
            )
            if expected_completed_date != independently_completed:
                violations.append(
                    "date_contract.expected_completed_date does not match calendar: "
                    f"expected={independently_completed}, got={expected_completed_date}"
                )
        except InferenceContractError as exc:
            violations.append(f"Cannot validate completed-session boundary: {exc}")
    if decision_date and expected_completed_date and decision_date != expected_completed_date:
        violations.append(
            "decision_date must equal date_contract.expected_completed_date: "
            f"decision_date={decision_date}, expected_completed={expected_completed_date}"
        )
    if (
        sessions
        and decision_date
        and signal_date
        and feature_snapshot_lag_sessions is not None
    ):
        try:
            expected_signal = resolve_lagged_open_session(
                decision_date,
                sessions,
                feature_snapshot_lag_sessions,
            )
            if signal_date != expected_signal:
                violations.append(
                    "signal_date does not match aligned feature snapshot lag: "
                    f"expected={expected_signal}, got={signal_date}"
                )
        except ValueError as exc:
            violations.append(f"Cannot validate aligned feature session: {exc}")
    if (
        payload.get("universe_snapshot_semantics")
        == "current_constituents_snapshot"
        and decision_date
        and expected_completed_date
        and decision_date != expected_completed_date
    ):
        violations.append(
            "current_constituents_snapshot must be anchored to decision_date: "
            f"decision_date={decision_date}, expected_completed={expected_completed_date}"
        )

    declared_feature_availability = payload.get("feature_availability")
    feature_availability_contract: dict[str, Any] | None = None
    margin_asof_date: str | None = None
    if not isinstance(declared_feature_availability, dict):
        violations.append("CandidateRun missing feature_availability")
    else:
        margin = declared_feature_availability.get("margin")
        if not isinstance(margin, dict):
            violations.append("feature_availability.margin must be an object")
        else:
            try:
                feature_availability_contract = normalise_feature_availability(
                    {"margin": margin}
                )
            except ValueError as exc:
                violations.append(f"Invalid feature availability: {exc}")
            margin_asof_date = _date(
                margin.get("as_of_date"),
                "feature_availability.margin.as_of_date",
                violations,
            )
            if margin.get("source") != MARGIN_SOURCE:
                violations.append(
                    f"feature_availability.margin.source must be {MARGIN_SOURCE}"
                )
    if (
        sessions
        and signal_date
        and feature_availability_contract is not None
        and margin_asof_date
    ):
        try:
            expected_margin_asof = resolve_lagged_open_session(
                signal_date,
                sessions,
                feature_availability_contract["margin"]["lag_sessions"],
            )
            if margin_asof_date != expected_margin_asof:
                violations.append(
                    "feature_availability.margin.as_of_date does not match calendar: "
                    f"expected={expected_margin_asof}, got={margin_asof_date}"
                )
        except ValueError as exc:
            violations.append(f"Cannot validate margin availability: {exc}")

    source = payload.get("source")
    if not isinstance(source, dict):
        violations.append("Missing source provenance object")
        source = {}
    if is_candidate_run:
        if source.get("engine") != "pinned_model_blend_v1":
            violations.append("source.engine must be pinned_model_blend_v1")
        if not source.get("model_bundle_id"):
            violations.append("CandidateRun source missing model_bundle_id")
        elif payload.get("strategy_id") == "s180_top10" and not _is_sha256(
            source.get("model_bundle_id")
        ):
            violations.append(
                "s180_top10 source.model_bundle_id must be the 64-char registry bundle hash"
            )
        if not _is_sha256(source.get("model_bundle_hash")):
            violations.append("CandidateRun source.model_bundle_hash must be SHA-256")
        if source.get("feature_list_id") != payload.get("feature_list_id"):
            violations.append(
                "source.feature_list_id must match top-level feature_list_id"
            )
    models = source.get("models")
    if not isinstance(models, list) or not models:
        violations.append("source.models must be a non-empty list")
        models = []

    model_tags: list[str] = []
    model_weights: dict[str, float] = {}
    shareholder_contract: dict[str, Any] | None = None
    if payload.get("strategy_id") == "financial_rc":
        feature_sources = payload.get("feature_sources")
        shareholder_source = (
            feature_sources.get("shareholder")
            if isinstance(feature_sources, dict)
            else None
        )
        if not isinstance(shareholder_source, dict):
            violations.append("financial_rc missing feature_sources.shareholder")
        else:
            if shareholder_source.get("status") != "pass":
                violations.append("feature_sources.shareholder.status must be pass")
            if shareholder_source.get("as_of_date") != data_date:
                violations.append(
                    "feature_sources.shareholder.as_of_date must equal data_date"
                )
            if shareholder_source.get("availability_rule") != "announcement_date_asof":
                violations.append(
                    "feature_sources.shareholder availability_rule must be "
                    "announcement_date_asof"
                )
            if not _is_sha256(shareholder_source.get("snapshot_hash")):
                violations.append(
                    "feature_sources.shareholder.snapshot_hash must be SHA-256"
                )
            if shareholder_source.get("violations"):
                violations.append("feature_sources.shareholder.violations must be empty")
            shareholder_sources = shareholder_source.get("sources")
            if not isinstance(shareholder_sources, dict) or set(shareholder_sources) != {
                "holder_num", "top10_holder_ratio"
            }:
                violations.append(
                    "feature_sources.shareholder.sources must pin both sidecars"
                )
            else:
                for name, item in shareholder_sources.items():
                    if not isinstance(item, dict):
                        violations.append(
                            f"feature_sources.shareholder.sources.{name} must be an object"
                        )
                        continue
                    for field in ("file_sha256", "asof_snapshot_hash"):
                        if not _is_sha256(item.get(field)):
                            violations.append(
                                f"feature_sources.shareholder.sources.{name}.{field} "
                                "must be SHA-256"
                            )
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            violations.append(f"source.models[{index}] must be an object")
            continue
        prefix = f"source.models[{index}]"
        for field in (
            "tag",
            "model_hash",
            "artifact_id",
            "model_dir",
            "label_id",
            "feature_list_id",
            "train_start",
            "train_end",
            "horizon",
            "weight",
            "maturity_sessions",
            "ordered_feature_list_hash",
        ):
            if model.get(field) in (None, ""):
                violations.append(f"{prefix} missing {field}")
        artifact_sha256 = model.get("artifact_sha256")
        if is_candidate_run:
            expected_artifacts = {"model.txt", "center.json", "scale.json", "meta.json"}
            if (
                not isinstance(artifact_sha256, dict)
                or set(artifact_sha256) != expected_artifacts
            ):
                violations.append(
                    f"{prefix}.artifact_sha256 must pin model, scaler, and metadata files"
                )
            else:
                for filename, digest in artifact_sha256.items():
                    if not _is_sha256(digest):
                        violations.append(
                            f"{prefix}.artifact_sha256[{filename!r}] must be SHA-256"
                        )
        tag = str(model.get("tag") or "")
        if is_candidate_run and model.get("feature_list_id") != payload.get(
            "feature_list_id"
        ):
            violations.append(
                f"{prefix}.feature_list_id must match top-level feature_list_id"
            )
        if is_candidate_run and model.get("ordered_feature_list_hash") != payload.get(
            "feature_list_hash"
        ):
            violations.append(
                f"{prefix}.ordered_feature_list_hash must match top-level "
                "feature_list_hash"
            )
        if is_candidate_run and feature_availability_contract is not None:
            if model.get("feature_availability") != feature_availability_contract:
                violations.append(
                    f"{prefix}.feature_availability must match top-level contract"
                )
        if is_candidate_run and payload.get("strategy_id") in {
            "financial_rc",
            "s180_top10",
        }:
            try:
                observed_contract = normalise_shareholder_freshness(
                    model.get("shareholder_freshness_contract")
                )
            except ValueError as exc:
                violations.append(f"{prefix} invalid shareholder freshness: {exc}")
            else:
                if shareholder_contract is None:
                    shareholder_contract = observed_contract
                elif observed_contract != shareholder_contract:
                    violations.append(
                        f"{prefix}.shareholder_freshness_contract differs across models"
                    )
        if tag:
            if tag in model_tags:
                violations.append(f"Duplicate model tag: {tag}")
            model_tags.append(tag)
        try:
            weight = float(model.get("weight"))
            if weight <= 0:
                raise ValueError
            model_weights[tag] = weight
        except (TypeError, ValueError):
            violations.append(f"{prefix}.weight must be positive")
        train_start = _date(
            model.get("train_start"), f"{prefix}.train_start", violations
        )
        train_end = _date(model.get("train_end"), f"{prefix}.train_end", violations)
        if train_start and train_end and train_start > train_end:
            violations.append(f"{prefix} train_start is after train_end")
        if train_end and signal_date and train_end >= signal_date:
            violations.append(f"{prefix} train_end must be before signal_date")
        try:
            horizon = int(model.get("horizon"))
            if horizon <= 0 or isinstance(model.get("horizon"), bool):
                raise ValueError
        except (TypeError, ValueError):
            violations.append(f"{prefix}.horizon must be a positive integer")
            horizon = None
        if sessions and train_end and signal_date and horizon:
            try:
                observed_maturity = validate_label_maturity(
                    train_end=train_end,
                    signal_date=signal_date,
                    horizon=horizon,
                    open_dates=sessions,
                )
                try:
                    declared_maturity = int(model.get("maturity_sessions"))
                except (TypeError, ValueError):
                    declared_maturity = -1
                if declared_maturity != observed_maturity:
                    violations.append(
                        f"{prefix}.maturity_sessions does not match calendar: "
                        f"expected={observed_maturity}, got={model.get('maturity_sessions')!r}"
                    )
            except InferenceContractError as exc:
                violations.append(f"{prefix} label maturity violation: {exc}")

    if model_weights and abs(sum(model_weights.values()) - 1.0) > 1e-9:
        violations.append(
            f"source.models weights must sum to 1.0, got {sum(model_weights.values())}"
        )
    if is_candidate_run and payload.get("strategy_id") == "s180_top10":
        if model_tags != ["180d"] or model_weights != {"180d": 1.0}:
            violations.append(
                "s180_top10 requires exactly one 180d model with weight=1.0"
            )
    if _contains_latest(source):
        violations.append(
            "Forbidden implicit latest model resolution in source provenance"
        )
    if is_candidate_run and models:
        bundle_models = [
            {
                key: model.get(key)
                for key in (
                    "tag",
                    "weight",
                    "horizon",
                    "label_id",
                    "model_hash",
                    "artifact_id",
                    "model_dir",
                    "artifact_sha256",
                )
            }
            for model in models
            if isinstance(model, dict)
        ]
        expected_bundle_hash = _canonical_hash(
            {
                "bundle_id": source.get("model_bundle_id"),
                "feature_list_id": source.get("feature_list_id"),
                "feature_availability": feature_availability_contract,
                "shareholder_freshness": shareholder_contract,
                "models": bundle_models,
            }
        )
        if source.get("model_bundle_hash") != expected_bundle_hash:
            violations.append(
                "source.model_bundle_hash does not match pinned model bundle"
            )

    for field in (
        "config_hash",
        "feature_list_hash",
        "universe_hash",
        "feature_snapshot_hash",
        "candidate_hash",
    ):
        if is_candidate_run and not _is_sha256(payload.get(field)):
            violations.append(f"CandidateRun {field} must be a SHA-256 hex digest")
    for field in ("feature_list_id", "universe", "universe_snapshot_semantics"):
        if is_candidate_run and not payload.get(field):
            violations.append(f"CandidateRun missing {field}")
    if is_candidate_run:
        expected_semantics = (
            "dated_pit_membership_snapshot"
            if payload.get("strategy_id") == "s180_top10"
            else "current_constituents_snapshot"
        )
        if payload.get("universe_snapshot_semantics") != expected_semantics:
            if payload.get("strategy_id") == "s180_top10":
                violations.append(
                    "s180_top10 requires dated_pit_membership_snapshot"
                )
            else:
                violations.append(
                    "CandidateRun only supports current_constituents_snapshot; "
                    "the PIT universe provider is not implemented"
                )

    blend = payload.get("blend")
    if not isinstance(blend, dict):
        violations.append("Missing blend provenance object")
        blend = {}
    if is_candidate_run:
        expected_transform = (
            "raw_model_prediction"
            if payload.get("strategy_id") == "s180_top10"
            else "daily_cs_zscore_unclipped_ddof0"
        )
        if blend.get("score_transform") != expected_transform:
            violations.append(
                f"blend.score_transform must be {expected_transform}"
            )
        if blend.get("model_tags") != model_tags:
            violations.append("blend.model_tags must match source.models order")
    blend_weights = blend.get("weights")
    if not isinstance(blend_weights, dict):
        violations.append("blend.weights must be an object")
    else:
        try:
            normalised_blend = {
                str(key): float(value) for key, value in blend_weights.items()
            }
            if abs(sum(normalised_blend.values()) - 1.0) > 1e-9:
                violations.append("blend.weights must sum to 1.0")
            if model_weights and normalised_blend != model_weights:
                violations.append("blend.weights do not match source.models weights")
        except (TypeError, ValueError):
            violations.append("blend.weights values must be numeric")

    data_quality = payload.get("data_quality")
    if is_candidate_run:
        if not isinstance(data_quality, dict) or data_quality.get("status") != "pass":
            violations.append("CandidateRun data_quality.status must be pass")
        else:
            if data_quality.get("feature_snapshot_date") != data_date:
                violations.append(
                    "data_quality.feature_snapshot_date must equal data_date"
                )
            for field in (
                "feature_missing_ratio",
                "feature_unique_non_null",
                "model_used_features",
                "excessive_missing_features",
                "constant_model_used_features",
            ):
                if field not in data_quality:
                    violations.append(f"CandidateRun data_quality missing {field}")
            for field in (
                "excessive_missing_features",
                "constant_model_used_features",
            ):
                if data_quality.get(field):
                    violations.append(
                        f"CandidateRun data_quality.{field} must be empty"
                    )
            if payload.get("strategy_id") == "financial_rc":
                shareholder_quality = data_quality.get(
                    "shareholder_feature_freshness"
                )
                if (
                    not isinstance(shareholder_quality, dict)
                    or shareholder_quality.get("status") != "pass"
                    or shareholder_quality.get("violations")
                ):
                    violations.append(
                        "data_quality.shareholder_feature_freshness must pass"
                    )
                ineligible = data_quality.get("ineligible_instruments")
                try:
                    dropped_rows = int(data_quality.get("dropped_rows"))
                except (TypeError, ValueError):
                    dropped_rows = -1
                    violations.append("data_quality.dropped_rows must be an integer")
                if not isinstance(ineligible, list):
                    violations.append(
                        "data_quality.ineligible_instruments must be a list"
                    )
                elif len(ineligible) != dropped_rows:
                    violations.append(
                        "data_quality.ineligible_instruments must enumerate every dropped row"
                    )
                else:
                    observed_drop_reasons: dict[str, int] = {}
                    instruments: list[str] = []
                    for index, item in enumerate(ineligible):
                        if not isinstance(item, dict) or not item.get("ts_code"):
                            violations.append(
                                f"data_quality.ineligible_instruments[{index}] is invalid"
                            )
                            continue
                        instruments.append(str(item["ts_code"]))
                        reasons = item.get("reasons")
                        if not isinstance(reasons, list) or not reasons:
                            violations.append(
                                f"data_quality.ineligible_instruments[{index}].reasons "
                                "must be non-empty"
                            )
                            continue
                        for reason in set(map(str, reasons)):
                            observed_drop_reasons[reason] = (
                                observed_drop_reasons.get(reason, 0) + 1
                            )
                    if len(instruments) != len(set(instruments)):
                        violations.append(
                            "data_quality.ineligible_instruments contains duplicates"
                        )
                    if observed_drop_reasons != data_quality.get("drop_reasons"):
                        violations.append(
                            "data_quality.drop_reasons does not match enumerated rows"
                        )

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        violations.append("candidates must be a non-empty list")
        candidates = []
    ranks: list[int] = []
    for index, row in enumerate(candidates):
        if not isinstance(row, dict):
            violations.append(f"Candidate row {index} must be an object")
            continue
        prefix = f"Candidate row {index}"
        for field in ("ts_code", "rank", "ranking_score"):
            if row.get(field) in (None, ""):
                violations.append(f"{prefix} missing {field}")
        try:
            rank = int(row.get("rank"))
            ranks.append(rank)
        except (TypeError, ValueError):
            violations.append(f"{prefix} rank must be an integer")
        try:
            score = float(row.get("ranking_score"))
            if not math.isfinite(score):
                raise ValueError
        except (TypeError, ValueError):
            violations.append(f"{prefix} ranking_score must be finite")
        if is_candidate_run and payload.get("strategy_id") == "s180_top10":
            try:
                raw_prediction = float(row.get("raw_prediction"))
                if not math.isfinite(raw_prediction):
                    raise ValueError
                if raw_prediction != score:
                    violations.append(
                        f"{prefix} raw_prediction must equal ranking_score for s180_top10"
                    )
            except (TypeError, ValueError):
                violations.append(
                    f"{prefix} raw_prediction must be finite for s180_top10"
                )
        for field, expected in (
            ("run_id", payload.get("run_id")),
            ("strategy_id", payload.get("strategy_id")),
            ("signal_date", signal_date),
            ("data_date", data_date),
            ("decision_date", decision_date),
            ("execution_date", execution_date),
        ):
            if is_candidate_run and row.get(field) != expected:
                violations.append(f"{prefix} {field} does not match top-level value")
        row_models = row.get("models")
        if is_candidate_run:
            if not isinstance(row_models, list):
                violations.append(f"{prefix} models must be a list")
            else:
                row_tags = [
                    str(item.get("tag"))
                    for item in row_models
                    if isinstance(item, dict)
                ]
                if row_tags != model_tags or len(row_models) != len(model_tags):
                    violations.append(
                        f"{prefix} model tags/order do not match source.models"
                    )
                for model_index, model_row in enumerate(row_models):
                    if not isinstance(model_row, dict):
                        violations.append(
                            f"{prefix} models[{model_index}] must be an object"
                        )
                        continue
                    tag = str(model_row.get("tag") or "")
                    try:
                        row_weight = float(model_row.get("weight"))
                        if tag not in model_weights or row_weight != model_weights[tag]:
                            raise ValueError
                    except (TypeError, ValueError):
                        violations.append(
                            f"{prefix} models[{model_index}] weight does not match source.models"
                        )
                    try:
                        row_score = float(model_row.get("score"))
                        if not math.isfinite(row_score):
                            raise ValueError
                    except (TypeError, ValueError):
                        violations.append(
                            f"{prefix} models[{model_index}] score must be finite"
                        )
                    try:
                        row_rank = int(model_row.get("rank"))
                        if row_rank <= 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        violations.append(
                            f"{prefix} models[{model_index}] rank must be positive"
                        )

    if ranks and ranks != list(range(1, len(ranks) + 1)):
        violations.append(
            "Candidate ranks must be unique, ordered, and contiguous from 1"
        )
    if payload.get("candidate_count") is not None and payload.get(
        "candidate_count"
    ) != len(candidates):
        violations.append("candidate_count does not match candidates length")
    if is_candidate_run and payload.get("top_k") != len(candidates):
        violations.append("top_k does not match candidates length")
    if payload.get("candidate_hash") and payload.get(
        "candidate_hash"
    ) != _candidate_hash(candidates):
        violations.append("candidate_hash does not match candidate content")

    return violations


def check_artifact(
    artifact_path: str,
    *,
    project_root: Path = PROJECT_ROOT,
) -> list[str]:
    """Load *artifact_path* and return all contract violations."""

    path = Path(artifact_path)
    if not path.exists():
        return [f"File not found: {artifact_path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return [f"Cannot parse JSON: {exc}"]
    try:
        open_dates = load_open_dates(Path(project_root))
    except InferenceContractError as exc:
        return [f"Cannot load authoritative calendar: {exc}"]
    return check_payload(payload, open_dates=open_dates)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check inference artifact contract")
    parser.add_argument("--artifact", required=True, help="Path to JSON artifact")
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help="Project root containing data/meta.db (default: repository root)",
    )
    args = parser.parse_args()
    violations = check_artifact(
        args.artifact,
        project_root=Path(args.project_root).resolve(),
    )
    if violations:
        print(f"❌ Inference artifact check FAILED ({len(violations)} issue(s)):\n")
        for violation in violations:
            print(f"  • {violation}")
        return 1
    print("✅ Inference artifact contract is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
