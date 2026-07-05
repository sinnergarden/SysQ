#!/usr/bin/env python3
"""Check inference artifact provenance completeness.

Supports JSON artifacts.  Verifies that the artifact contains
minimum provenance fields so outputs are traceable.

Usage:
    python harness/checks/check_inference_artifact.py --artifact <path>

Expected JSON schema (top-level fields):
    run_id, strategy_id, signal_date, execution_date,
    model_id or model_path, train_start, train_end,
    feature_snapshot, created_at

If artifact contains candidate rows, each should have:
    ts_code, score, rank, signal_date, strategy_id,
    model_id or model_path, run_id

Exit:
    0 = PASS (all required fields present)
    1 = FAIL (missing fields listed)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_TOP_LEVEL = {
    "run_id",
    "strategy_id",
    "signal_date",
    "execution_date",
}

REQUIRED_MODEL_LINEAGE = {
    "train_start",
    "train_end",
}

REQUIRED_CANDIDATE_FIELDS = {
    "ts_code",
    "rank",
}
# Accept either "score" or "ranking_score" in candidate rows
CANDIDATE_SCORE_ALIAS = {"score", "ranking_score"}


def _has_model_id_or_path(payload: dict) -> bool:
    """Check if artifact identifies the model used."""
    for key in ("model_id", "model_path", "model_hash", "model_dir"):
        val = payload.get(key)
        if val and isinstance(val, str) and len(val) > 0:
            return True
    # Also check inside source.models or nested source block
    if "source" in payload:
        src = payload["source"]
        if isinstance(src, dict):
            models = src.get("models", [])
            if isinstance(models, list):
                for m in models:
                    for key in ("model_hash", "model_id", "model_path", "model_dir"):
                        if m.get(key):
                            return True
            for key in ("model_hash", "model_id", "model_path", "model_dir"):
                if src.get(key):
                    return True
    return False


def check_artifact(artifact_path: str) -> list[str]:
    """Check provenance completeness. Returns list of missing field descriptions."""
    violations: list[str] = []
    path = Path(artifact_path)

    if not path.exists():
        violations.append(f"File not found: {artifact_path}")
        return violations

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        violations.append(f"Cannot parse JSON: {e}")
        return violations

    if not isinstance(payload, dict):
        violations.append("Top-level must be a JSON object (dict)")
        return violations

    # Check top-level fields
    for field in REQUIRED_TOP_LEVEL:
        if field not in payload or payload[field] is None:
            violations.append(f"Missing top-level field: {field}")

    # Check model lineage
    if not _has_model_id_or_path(payload):
        violations.append("Missing model identification (model_id/model_path/model_hash/model_dir in payload or source.models)")

    for field in REQUIRED_MODEL_LINEAGE:
        val = payload.get(field)
        if not val:
            # Also check source.models[0].train_start
            found = False
            src = payload.get("source")
            if isinstance(src, dict):
                models = src.get("models", [])
                if isinstance(models, list) and len(models) > 0:
                    for m in models:
                        if m.get(field):
                            found = True
                            break
            if not found:
                violations.append(f"Missing model lineage: {field} (in payload or source.models)")

    if "feature_snapshot" not in payload:
        # Accept feature_list_id as proxy
        if "feature_list_id" not in payload:
            violations.append("Missing feature identification (feature_snapshot or feature_list_id)")

    if "created_at" not in payload:
        violations.append("Missing created_at timestamp")

    # Check candidates — validate ALL rows, not just first
    candidates = payload.get("candidates")
    if isinstance(candidates, list) and len(candidates) > 0:
        for idx, row in enumerate(candidates):
            if not isinstance(row, dict):
                violations.append(f"Candidate row {idx} must be a dict")
                continue
            for field in REQUIRED_CANDIDATE_FIELDS:
                if field not in row or row[field] is None:
                    violations.append(f"Candidate row {idx} missing required field: {field}")
            # Check score or ranking_score
            if not (row.get("score") is not None or row.get("ranking_score") is not None):
                violations.append(f"Candidate row {idx} missing score field (score or ranking_score)")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Check inference artifact provenance")
    parser.add_argument("--artifact", required=True, help="Path to JSON artifact")
    args = parser.parse_args()

    violations = check_artifact(args.artifact)

    if violations:
        print(f"❌ Inference artifact check FAILED ({len(violations)} issue(s)):\n")
        for v in violations:
            print(f"  • {v}")
        print()
        return 1

    print("✅ Inference artifact provenance is complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
