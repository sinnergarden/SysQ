"""Independent validator for frozen Alpha Map artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def validate_alpha_map(
    config_path: str | Path, *, root: str | Path = "data/research"
) -> dict[str, Any]:
    config_path = Path(config_path).resolve()
    repo_root = Path(__file__).resolve().parents[2]
    root = Path(root).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    alpha_map_id = str(config["alpha_map_id"])
    directory = root / "alpha_maps" / alpha_map_id
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = json.loads((directory / "alpha_map.json").read_text(encoding="utf-8"))

    if manifest.get("schema_version") != "alpha_map_v1":
        raise ValueError("invalid Alpha Map manifest schema")
    if manifest.get("alpha_map_id") != alpha_map_id:
        raise ValueError("Alpha Map id mismatch")
    if manifest.get("config_sha256") != _sha256(config_path):
        raise ValueError("Alpha Map config hash mismatch")
    producer_path = repo_root / "qsys/research/alpha_map.py"
    if manifest.get("producer_code_sha256") != _sha256(producer_path):
        raise ValueError("Alpha Map producer code hash mismatch")
    identity = {
        key: manifest[key]
        for key in (
            "schema_version", "alpha_map_id", "config_sha256",
            "producer_code_sha256", "catalog_identity_sha256",
            "label_suite_identity_sha256", "diagnostics_identities",
        )
    }
    if manifest.get("alpha_map_identity_sha256") != _canonical_hash(identity):
        raise ValueError("Alpha Map identity mismatch")
    for name, spec in manifest.get("outputs", {}).items():
        if _sha256(directory / name) != spec.get("sha256"):
            raise ValueError(f"Alpha Map output hash mismatch: {name}")

    if artifact.get("schema_version") != "alpha_map_v1":
        raise ValueError("invalid Alpha Map artifact schema")
    if artifact.get("alpha_map_id") != alpha_map_id:
        raise ValueError("Alpha Map artifact id mismatch")
    if artifact["catalog"]["catalog_identity_sha256"] != manifest[
        "catalog_identity_sha256"
    ]:
        raise ValueError("Alpha Map catalog identity mismatch")
    if artifact["label_suite"]["label_suite_identity_sha256"] != manifest[
        "label_suite_identity_sha256"
    ]:
        raise ValueError("Alpha Map label suite identity mismatch")

    catalog_dir = root / "feature_catalogs" / artifact["catalog"]["catalog_id"]
    if _sha256(catalog_dir / "manifest.json") != artifact["catalog"]["manifest_sha256"]:
        raise ValueError("Alpha Map feature catalog manifest changed")
    if _sha256(catalog_dir / "validation.json") != artifact["catalog"]["validation_sha256"]:
        raise ValueError("Alpha Map feature catalog validation changed")
    catalog_validation = json.loads(
        (catalog_dir / "validation.json").read_text(encoding="utf-8")
    )
    if not catalog_validation.get("validated"):
        raise ValueError("Alpha Map feature catalog validation is not passing")

    label_dir = root / "label_suites" / artifact["label_suite"]["suite_id"]
    if _sha256(label_dir / "manifest.json") != artifact["label_suite"]["manifest_sha256"]:
        raise ValueError("Alpha Map label suite manifest changed")
    if _sha256(label_dir / "validation.json") != artifact["label_suite"]["validation_sha256"]:
        raise ValueError("Alpha Map label suite validation changed")
    label_validation = json.loads(
        (label_dir / "validation.json").read_text(encoding="utf-8")
    )
    if label_validation.get("status") != "PASS" or label_validation.get("failures"):
        raise ValueError("Alpha Map label suite validation is not passing")

    expected_identities = []
    for item in manifest.get("experiments", []):
        diagnostics_id = item["diagnostics_id"]
        diagnostics_dir = root / "experiments" / diagnostics_id / "diagnostics"
        diagnostics_manifest_path = diagnostics_dir / "manifest.json"
        receipt_path = diagnostics_dir.parent / "diagnostics_validation.json"
        if _sha256(diagnostics_manifest_path) != item["diagnostics_manifest_sha256"]:
            raise ValueError(f"diagnostics manifest changed: {diagnostics_id}")
        if _sha256(receipt_path) != item["diagnostics_validation_sha256"]:
            raise ValueError(f"diagnostics validation changed: {diagnostics_id}")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        diagnostics_manifest = json.loads(
            diagnostics_manifest_path.read_text(encoding="utf-8")
        )
        if not receipt.get("validated"):
            raise ValueError(f"diagnostics validation is not passing: {diagnostics_id}")
        if receipt.get("manifest_sha256") != _sha256(diagnostics_manifest_path):
            raise ValueError(f"diagnostics receipt mismatch: {diagnostics_id}")
        for name, output in diagnostics_manifest.get("outputs", {}).items():
            if _sha256(diagnostics_dir / name) != output["sha256"]:
                raise ValueError(f"diagnostics output changed: {diagnostics_id}/{name}")
        if item.get("holdout_consumed"):
            raise ValueError(f"holdout was consumed: {diagnostics_id}")
        expected_identities.append(item["diagnostics_identity_sha256"])
    if sorted(expected_identities) != manifest["diagnostics_identities"]:
        raise ValueError("diagnostics identity set mismatch")

    rows = artifact.get("rows", [])
    with (directory / "alpha_map.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    if len(csv_rows) != len(rows) or not rows:
        raise ValueError("Alpha Map JSON/CSV row count mismatch")
    if any(row["pit_tier"] == "PIT-X" and row["promotion_eligible_count"] for row in rows):
        raise ValueError("PIT-X row is promotion eligible")
    if any(row["stage_c_status"] != "not_entered" for row in rows):
        raise ValueError("Stage C entered without a validated portfolio artifact")
    summary = artifact["summary"]
    if summary.get("holdout_consumed") or summary.get("stage_c_entered"):
        raise ValueError("Alpha Map terminal-state contract violated")
    if summary["confirmed_count"] != sum(row["promotion_eligible_count"] for row in rows):
        raise ValueError("Alpha Map confirmed count mismatch")
    required_ablations = set(config.get("required_ablation_ids", []))
    actual_ablations = {row["ablation_id"] for row in artifact.get("ablations", [])}
    if not required_ablations.issubset(actual_ablations):
        raise ValueError("Alpha Map required ablations are missing")
    if not 3 <= len(artifact.get("future_directions", [])) <= 5:
        raise ValueError("Alpha Map must contain 3 to 5 future directions")

    result = {
        "schema_version": "alpha_map_validation_v1",
        "alpha_map_id": alpha_map_id,
        "alpha_map_identity_sha256": manifest["alpha_map_identity_sha256"],
        "manifest_sha256": _sha256(manifest_path),
        "experiment_count": len(expected_identities),
        "row_count": len(rows),
        "confirmed_count": summary["confirmed_count"],
        "provisional_supported_count": summary["provisional_supported_count"],
        "holdout_consumed": False,
        "validated": True,
        "validator_code_sha256": _sha256(Path(__file__)),
    }
    _write_json(directory / "validation.json", result)
    return result
