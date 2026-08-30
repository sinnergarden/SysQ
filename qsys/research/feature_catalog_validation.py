"""Independent validator for the static feature-universe catalog."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_feature_catalog(
    config_path: str | Path,
    *,
    root: str | Path = "data/research",
) -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    config_path = Path(config_path).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    catalog_id = str(config["catalog_id"])
    output_dir = Path(root).resolve() / "feature_catalogs" / catalog_id
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "feature_universe_catalog_manifest_v1":
        raise ValueError("unexpected feature catalog manifest schema")
    if manifest.get("catalog_id") != catalog_id:
        raise ValueError("feature catalog id mismatch")

    artifact_hashes = manifest.get("artifacts", {})
    for filename, expected_hash in artifact_hashes.items():
        path = output_dir / filename
        if not path.is_file() or _sha256(path) != expected_hash:
            raise ValueError(f"feature catalog artifact hash mismatch: {filename}")
    for relative, expected_hash in manifest.get("inputs", {}).items():
        path = repo_root / relative
        if not path.is_file() or _sha256(path) != expected_hash:
            raise ValueError(f"feature catalog input hash mismatch: {relative}")

    rows = json.loads((output_dir / "feature_catalog.json").read_text(encoding="utf-8"))
    csv_rows = _read_csv(output_dir / "feature_catalog.csv")
    tiering = _read_csv(output_dir / "pit_tiering.csv")
    coverage = _read_csv(output_dir / "config_coverage.csv")
    summary = json.loads((output_dir / "review_summary.json").read_text(encoding="utf-8"))
    if len(rows) != len(csv_rows):
        raise ValueError("catalog CSV/JSON row-count mismatch")
    names = [str(row["feature_name"]) for row in rows]
    if len(names) != len(set(names)):
        raise ValueError("duplicate feature names in catalog")
    if len(rows) < int(config.get("expected_min_unique_features", 300)):
        raise ValueError("catalog does not meet minimum universe size")
    if _canonical_sha256(rows) != summary["catalog_rows_sha256"]:
        raise ValueError("catalog canonical row identity mismatch")

    runtime = set()
    from qsys.feature.library import FeatureLibrary
    from qsys.feature.registry import FEATURE_GROUPS

    runtime.update(FeatureLibrary.get_semantic_all_features_config())
    registry = {
        feature
        for payload in FEATURE_GROUPS.values()
        for feature in payload.get("features", [])
    }
    feature_root = repo_root / str(config.get("feature_config_root", "configs/features"))
    config_features: set[str] = set()
    config_paths: set[str] = set()
    for path in sorted(feature_root.rglob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        config_features.update(payload.get("features", []))
        config_paths.add(path.relative_to(repo_root).as_posix())
    expected_names = runtime | registry | config_features
    if set(names) != expected_names:
        missing = sorted(expected_names - set(names))[:10]
        extra = sorted(set(names) - expected_names)[:10]
        raise ValueError(f"catalog universe mismatch: missing={missing}, extra={extra}")
    if {row["config_path"] for row in coverage} != config_paths:
        raise ValueError("feature config coverage is incomplete")

    disputed = config.get("disputed_features", {})
    expected_financial = {str(item["feature"]) for item in disputed.get("financial", [])}
    expected_shareholder = {str(item["feature"]) for item in disputed.get("shareholder", [])}
    actual_financial = {
        row["feature_name"] for row in tiering if row["category"] == "financial"
    }
    actual_shareholder = {
        row["feature_name"] for row in tiering if row["category"] == "shareholder"
    }
    if actual_financial != expected_financial or actual_shareholder != expected_shareholder:
        raise ValueError("disputed feature tiering does not match the declared scope")
    if any(row["pit_tier"] != "PIT-B" for row in tiering if row["category"] == "financial"):
        raise ValueError("financial disputed features must be PIT-B")
    if any(row["pit_tier"] != "PIT-X" for row in tiering if row["category"] == "shareholder"):
        raise ValueError("shareholder disputed features must be PIT-X")
    if _canonical_sha256(tiering) != summary["pit_tiering_rows_sha256"]:
        raise ValueError("PIT tiering canonical identity mismatch")

    identity_basis = {
        "catalog_id": catalog_id,
        "inputs": manifest["inputs"],
        "artifact_hashes": manifest["artifacts"],
        "catalog_rows_sha256": summary["catalog_rows_sha256"],
        "pit_tiering_rows_sha256": summary["pit_tiering_rows_sha256"],
    }
    identity = _canonical_sha256(identity_basis)
    if identity != manifest["catalog_identity_sha256"]:
        raise ValueError("feature catalog identity mismatch")
    validation = {
        "schema_version": "feature_universe_catalog_validation_v1",
        "catalog_id": catalog_id,
        "catalog_identity_sha256": identity,
        "validated": True,
        "unique_feature_count": len(rows),
        "runtime_feature_count": len(runtime),
        "registry_unique_feature_count": len(registry),
        "config_feature_unique_count": len(config_features),
        "feature_config_count": len(config_paths),
        "financial_pit_b_count": len(actual_financial),
        "shareholder_pit_x_count": len(actual_shareholder),
        "future_reference_failures": sum(
            row["future_reference_check"] != "pass" for row in rows
        ),
        "label_contamination_failures": sum(
            row["label_contamination_check"] != "pass" for row in rows
        ),
        "artifact_hashes": artifact_hashes,
    }
    validation_path = output_dir / "validation.json"
    validation_path.write_text(
        json.dumps(validation, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**validation, "validation": str(validation_path)}
