"""Manifest generation for resolved FeatureSets.

Manifest is **audit-only** — never used for fault tolerance.
If resolver fails, no ``status="ok"`` manifest is written.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from qsys.feature.build_plan import FeatureBuildPlan
from qsys.feature.resolver_v2 import ResolvedFeatureSet


def build_feature_manifest(
    resolved: ResolvedFeatureSet,
    plan: FeatureBuildPlan,
) -> dict:
    """Build a manifest dict from a resolved feature set and its build plan.

    Returns a dict ready for JSON serialization.

    Rules:
    1. ``manifest_version`` = 1
    2. ``resolved_features`` count must equal ``feature_ids`` count
    3. Status is ``"ok"`` only if the resolver succeeded (no exceptions)
    """
    if len(resolved.resolved_features) != len(resolved.feature_ids):
        raise ValueError(
            f"resolved_features count ({len(resolved.resolved_features)}) "
            f"does not match feature_ids count ({len(resolved.feature_ids)})"
        )

    return {
        "manifest_version": 1,
        "feature_set_id": resolved.feature_set_id,
        "source_path": resolved.source_path,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "resolved_features": list(resolved.resolved_features),
        "feature_ids": list(resolved.feature_ids),
        "raw_features": list(resolved.raw_features),
        "derived_features": list(resolved.derived_features),
        "required_transforms": list(resolved.required_transforms),
        "unresolved_transforms": list(plan.unresolved_transforms),
        "warnings": list(plan.warnings),
        "spec_sources": [
            {"name": s["name"], "source": s["source"]}
            for s in resolved.spec_sources
        ],
        "status": "ok",
    }


def write_feature_manifest(
    manifest: dict,
    output_dir: str | Path,
) -> Path:
    """Write a manifest JSON file.

    Path: ``{output_dir}/{feature_set_id}.json``
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    feature_set_id = manifest["feature_set_id"]
    path = output_dir / f"{feature_set_id}.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
