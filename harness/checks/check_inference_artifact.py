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
from typing import Any

REQUIRED_TOP_LEVEL = {
    "run_id",
    "strategy_id",
    "signal_date",
    "data_date",
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


def check_payload(payload: Any) -> list[str]:
    """Return all contract violations found in an inference payload."""

    violations: list[str] = []
    if not isinstance(payload, dict):
        return ["Top-level must be a JSON object (dict)"]

    candidate_markers = {"config_hash", "candidate_hash", "date_contract"}
    is_candidate_run = payload.get("artifact_type") == "candidate_run" or any(
        marker in payload for marker in candidate_markers
    )
    if is_candidate_run:
        if payload.get("artifact_type") != "candidate_run":
            violations.append(
                "Canonical candidate markers require artifact_type=candidate_run"
            )
        if payload.get("schema_version") != 1:
            violations.append("CandidateRun schema_version must be 1")
        if payload.get("usage") != "human_research_only":
            violations.append("CandidateRun usage must be human_research_only")

    for field in sorted(REQUIRED_TOP_LEVEL):
        if payload.get(field) in (None, ""):
            violations.append(f"Missing top-level field: {field}")

    signal_date = _date(payload.get("signal_date"), "signal_date", violations)
    data_date = _date(payload.get("data_date"), "data_date", violations)
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

    date_contract = payload.get("date_contract")
    if isinstance(date_contract, dict):
        if date_contract.get("execution_rule") != "next_open_session":
            violations.append("date_contract.execution_rule must be next_open_session")
        if date_contract.get("mode") != "postclose_for_next_open_session":
            violations.append(
                "date_contract.mode must be postclose_for_next_open_session"
            )
    elif is_candidate_run:
        violations.append("CandidateRun missing date_contract")
    if (
        is_candidate_run
        and isinstance(date_contract, dict)
        and date_contract.get("mode") == "postclose_for_next_open_session"
        and data_date
        and signal_date
        and data_date != signal_date
    ):
        violations.append("Post-close CandidateRun data_date must equal signal_date")

    source = payload.get("source")
    if not isinstance(source, dict):
        violations.append("Missing source provenance object")
        source = {}
    if is_candidate_run:
        if source.get("engine") != "pinned_model_blend_v1":
            violations.append("source.engine must be pinned_model_blend_v1")
        if not source.get("model_bundle_id"):
            violations.append("CandidateRun source missing model_bundle_id")
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
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            violations.append(f"source.models[{index}] must be an object")
            continue
        prefix = f"source.models[{index}]"
        for field in (
            "tag",
            "model_hash",
            "model_dir",
            "label_id",
            "feature_list_id",
            "train_start",
            "train_end",
            "horizon",
            "weight",
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

    if model_weights and abs(sum(model_weights.values()) - 1.0) > 1e-9:
        violations.append(
            f"source.models weights must sum to 1.0, got {sum(model_weights.values())}"
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
                "models": bundle_models,
            }
        )
        if source.get("model_bundle_hash") != expected_bundle_hash:
            violations.append(
                "source.model_bundle_hash does not match pinned model bundle"
            )

    for field in ("config_hash", "feature_list_hash", "candidate_hash"):
        if is_candidate_run and not _is_sha256(payload.get(field)):
            violations.append(f"CandidateRun {field} must be a SHA-256 hex digest")
    for field in ("feature_list_id", "universe", "universe_snapshot_semantics"):
        if is_candidate_run and not payload.get(field):
            violations.append(f"CandidateRun missing {field}")

    blend = payload.get("blend")
    if not isinstance(blend, dict):
        violations.append("Missing blend provenance object")
        blend = {}
    if is_candidate_run:
        if blend.get("score_transform") != "daily_cs_zscore_unclipped_ddof0":
            violations.append(
                "blend.score_transform must be daily_cs_zscore_unclipped_ddof0"
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
        elif data_quality.get("feature_snapshot_date") != data_date:
            violations.append("data_quality.feature_snapshot_date must equal data_date")

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
        for field, expected in (
            ("run_id", payload.get("run_id")),
            ("strategy_id", payload.get("strategy_id")),
            ("signal_date", signal_date),
            ("data_date", data_date),
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


def check_artifact(artifact_path: str) -> list[str]:
    """Load *artifact_path* and return all contract violations."""

    path = Path(artifact_path)
    if not path.exists():
        return [f"File not found: {artifact_path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        return [f"Cannot parse JSON: {exc}"]
    return check_payload(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check inference artifact contract")
    parser.add_argument("--artifact", required=True, help="Path to JSON artifact")
    args = parser.parse_args()
    violations = check_artifact(args.artifact)
    if violations:
        print(f"❌ Inference artifact check FAILED ({len(violations)} issue(s)):\n")
        for violation in violations:
            print(f"  • {violation}")
        return 1
    print("✅ Inference artifact contract is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
