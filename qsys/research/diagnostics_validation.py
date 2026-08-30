"""Independent validation of canonical ResearchDiagnostics artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def validate_research_diagnostics(
    config_path: str | Path,
    *,
    root: str | Path = "data/research",
) -> dict[str, Any]:
    """Validate identities, lineage backlinks, schemas and result finiteness."""
    from qsys.config import cfg as settings
    from qsys.data import adapter
    from qsys.feature.registry import FeatureListRegistry

    project_root = Path(__file__).resolve().parents[2]
    config_path = Path(config_path).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    output_dir = (
        Path(root).resolve()
        / "experiments"
        / str(config["experiment_id"])
        / "diagnostics"
    )
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != "research_diagnostics":
        raise ValueError("unexpected diagnostics artifact type")
    if manifest.get("schema_version") != 2:
        raise ValueError("unexpected diagnostics manifest schema")

    config_sha256 = _canonical_sha256(config)
    if manifest.get("config_sha256") != config_sha256:
        raise ValueError("diagnostics config hash mismatch")
    diagnostics_code_path = project_root / "qsys/analysis/research_diagnostics.py"
    if manifest.get("diagnostics_code_sha256") != _sha256(diagnostics_code_path):
        raise ValueError("diagnostics code hash mismatch")
    if manifest.get("adapter_code_sha256") != _sha256(Path(adapter.__file__).resolve()):
        raise ValueError("diagnostics adapter code hash mismatch")

    outputs = manifest.get("outputs", {})
    for filename, artifact in outputs.items():
        path = output_dir / filename
        if not path.is_file() or _sha256(path) != artifact.get("sha256"):
            raise ValueError(f"diagnostics output hash mismatch: {filename}")
        if path.suffix == ".csv":
            rows = max(sum(1 for _ in path.open("r", encoding="utf-8")) - 1, 0)
            if rows != int(artifact.get("row_count", -1)):
                raise ValueError(f"diagnostics output row-count mismatch: {filename}")

    lineage = manifest.get("lineage", {})
    required_lineage = {
        "feature_list", "pit_universe", "industry_taxonomy", "labels",
        "source_artifacts",
    }
    if config.get("require_feature_cache"):
        required_lineage.add("feature_cache")
    if config.get("require_feature_label_alignment"):
        required_lineage.add("feature_label_alignment")
    missing_lineage = sorted(required_lineage - set(lineage))
    if missing_lineage:
        raise ValueError(f"diagnostics lineage is incomplete: {missing_lineage}")

    feature_contract = FeatureListRegistry.contract(str(config["feature_list_id"]))
    for key in ("features_sha256", "feature_list_config_sha256", "feature_count"):
        if lineage["feature_list"].get(key) != feature_contract[key]:
            raise ValueError(f"diagnostics feature contract mismatch: {key}")

    pit_manifest = Path(lineage["pit_universe"]["manifest_path"])
    if _sha256(pit_manifest) != lineage["pit_universe"]["manifest_sha256"]:
        raise ValueError("diagnostics PIT universe manifest hash mismatch")
    industry_path = Path(lineage["industry_taxonomy"]["path"])
    if _sha256(industry_path) != lineage["industry_taxonomy"]["sha256"]:
        raise ValueError("diagnostics industry taxonomy hash mismatch")

    for label_id, label in lineage["labels"].items():
        for path_key, hash_key in (
            ("manifest_path", "manifest_sha256"),
            ("data_path", "data_sha256"),
        ):
            if _sha256(Path(label[path_key])) != label[hash_key]:
                raise ValueError(f"diagnostics label backlink mismatch: {label_id}")
    for source in lineage["source_artifacts"].values():
        source_path = Path(source["path"])
        if not source_path.is_absolute():
            source_path = Path(settings.data_root) / source_path
        if _sha256(source_path) != source["sha256"]:
            raise ValueError("diagnostics source artifact backlink mismatch")

    if config.get("require_feature_cache"):
        cache = lineage["feature_cache"]
        for path_key, hash_key in (
            ("manifest_path", "manifest_sha256"),
            ("validation_path", "validation_sha256"),
        ):
            path = Path(cache[path_key])
            if not path.is_absolute():
                path = project_root / path
            if _sha256(path) != cache[hash_key]:
                raise ValueError(f"diagnostics feature-cache backlink mismatch: {path_key}")
        for shard in cache["selected_shards"]:
            path = Path(shard["path"])
            if not path.is_absolute():
                path = project_root / path
            if _sha256(path) != shard["data_sha256"]:
                raise ValueError("diagnostics selected cache shard hash mismatch")

    if config.get("require_feature_label_alignment"):
        alignment = lineage["feature_label_alignment"]
        if alignment.get("contract") != "previous_open_session_to_execution_date_v1":
            raise ValueError("diagnostics feature-label alignment contract mismatch")
        if alignment.get("strict_prior_date_check") != "pass":
            raise ValueError("diagnostics feature-label strict-prior check failed")
        calendar_path = Path(alignment["calendar_path"])
        if not calendar_path.is_absolute():
            calendar_path = Path(settings.data_root) / calendar_path
        if _sha256(calendar_path) != alignment["calendar_sha256"]:
            raise ValueError("diagnostics alignment calendar backlink mismatch")

    feature_count = int(feature_contract["feature_count"])
    label_count = len(config.get("labels", []))
    expected_pairs = feature_count * label_count
    year_count = int(str(config["end_date"])[:4]) - int(
        str(config["start_date"])[:4]
    ) + 1
    expected_rows = {
        "coverage.csv": feature_count,
        "coverage_yearly.csv": feature_count * year_count,
        "feature_ic.csv": expected_pairs,
        "bucket_return.csv": expected_pairs,
        "exposure_breakdown.csv": expected_pairs,
    }
    for filename, count in expected_rows.items():
        if filename in outputs and int(outputs[filename]["row_count"]) != count:
            raise ValueError(f"diagnostics expected row count mismatch: {filename}")
    for filename in ("coverage.csv", "feature_ic.csv", "bucket_return.csv"):
        frame = pd.read_csv(output_dir / filename)
        numeric = frame.select_dtypes(include=[np.number])
        if not numeric.empty and np.isinf(numeric.to_numpy()).any():
            raise ValueError(f"diagnostics output contains infinity: {filename}")
    ic = pd.read_csv(output_dir / "feature_ic.csv")
    if ic.duplicated(["feature", "label_id"]).any():
        raise ValueError("diagnostics feature-IC pairs are duplicated")

    identity_payload = {
        "config_sha256": config_sha256,
        "lineage": lineage,
        "diagnostics_code_sha256": manifest["diagnostics_code_sha256"],
        "adapter_code_sha256": manifest["adapter_code_sha256"],
    }
    identity = _canonical_sha256(identity_payload)
    if identity != manifest.get("diagnostics_identity_sha256"):
        raise ValueError("diagnostics identity mismatch")
    result = {
        "schema_version": "research_diagnostics_validation_v1",
        "validated": True,
        "experiment_id": config["experiment_id"],
        "diagnostics_identity_sha256": identity,
        "manifest_sha256": _sha256(manifest_path),
        "validator_code_sha256": _sha256(Path(__file__).resolve()),
        "feature_count": feature_count,
        "label_count": label_count,
        "feature_label_pair_count": expected_pairs,
        "output_count": len(outputs),
        "strict_prior_feature_alignment": (
            lineage.get("feature_label_alignment", {}).get("strict_prior_date_check")
            == "pass"
        ),
    }
    output_path = output_dir.parent / "diagnostics_validation.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {**result, "validation": str(output_path)}
