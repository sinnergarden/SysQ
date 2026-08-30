"""Independent validation for hash-bound annual feature-cache artifacts."""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from qsys.feature.registry import FeatureListRegistry
from qsys.research.pit_universe import PitUniverseStore


def _sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_identity(identity: dict[str, Any]) -> dict[str, Any]:
    artifact = dict(identity)
    artifact.pop("feature_list_id", None)
    artifact.pop("feature_list_contract", None)
    column_contract = dict(artifact.get("column_contract", {}))
    column_contract.pop("consumed_features", None)
    artifact["column_contract"] = column_contract
    return artifact


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _resolve_artifact_path(project_root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = project_root / path
    if path.is_symlink():
        raise ValueError(f"cache artifact cannot be a symlink: {path}")
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"cache artifact must be an existing regular file: {path}")
    return path


def _validate_code_dependencies(dependencies: list[dict[str, str]]) -> int:
    verified = 0
    for dependency in dependencies:
        name = str(dependency["name"])
        declared = str(dependency["sha256"])
        module = importlib.import_module(name)
        module_path = Path(str(module.__file__)).resolve()
        if _sha256_file(module_path) != declared:
            raise ValueError(f"cache code dependency hash mismatch: {name}")
        verified += 1
    return verified


def validate_annual_feature_cache(
    manifest_path: Path,
    *,
    project_root: Path,
    preheat_code_path: Path,
    generator_code_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    """Validate manifest, metadata, code/input lineage and physical Parquets."""
    manifest_path = manifest_path.resolve()
    project_root = project_root.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 2:
        raise ValueError("feature-cache manifest schema must be 2")
    shards = manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        raise ValueError("feature-cache manifest has no shards")
    if manifest.get("preheat_code_sha256") != _sha256_file(preheat_code_path):
        raise ValueError("preheat code hash mismatch")
    if manifest.get("generator_code_sha256") != _sha256_file(generator_code_path):
        raise ValueError("generator code hash mismatch")
    config_path = _resolve_artifact_path(project_root, str(manifest["config_path"]))
    if manifest.get("config_sha256") != _sha256_file(config_path):
        raise ValueError("preheat config hash mismatch")

    starts = [str(shard["start"]) for shard in shards]
    ends = [str(shard["end"]) for shard in shards]
    source_ends = [str(shard["source_coverage_end"]) for shard in shards]
    years = [int(value[:4]) for value in starts]
    if years != list(range(years[0], years[-1] + 1)):
        raise ValueError("annual cache shards are not contiguous")
    if manifest.get("cache_coverage_start") != min(starts):
        raise ValueError("manifest cache_coverage_start mismatch")
    if manifest.get("cache_coverage_end") != max(source_ends):
        raise ValueError("manifest cache_coverage_end mismatch")
    if manifest.get("cache_shard_identity_end") != max(ends):
        raise ValueError("manifest cache_shard_identity_end mismatch")

    first_identity = shards[0]["identity"]
    if first_identity["source_manifest_hash"] != manifest["source_manifest_hash"]:
        raise ValueError("manifest and cache identity source hashes differ")
    column_contract = first_identity["column_contract"]
    materialized = list(column_contract["materialized_features"])
    consumed = list(column_contract["consumed_features"])
    stored_columns = list(column_contract["stored_columns"])
    if stored_columns != ["trade_date", "instrument", *materialized]:
        raise ValueError("stored column contract is not exact")
    positions = {feature: index for index, feature in enumerate(materialized)}
    if len(positions) != len(materialized):
        raise ValueError("materialized column contract contains duplicates")
    try:
        consumed_positions = [positions[feature] for feature in consumed]
    except KeyError as exc:
        raise ValueError("consumed columns are not a materialized subset") from exc
    if consumed_positions != sorted(consumed_positions):
        raise ValueError("consumed columns are not an ordered subset")

    materialized_contract = FeatureListRegistry.contract(
        str(first_identity["feature_cache_list_id"])
    )
    consumed_contract = FeatureListRegistry.contract(
        str(first_identity["feature_list_id"])
    )
    if materialized_contract["features"] != materialized:
        raise ValueError("materialized feature registry contract mismatch")
    if consumed_contract["features"] != consumed:
        raise ValueError("consumed feature registry contract mismatch")
    for key in ("features_sha256", "feature_list_config_sha256"):
        if (
            materialized_contract[key]
            != first_identity["materialized_feature_list_contract"][key]
        ):
            raise ValueError(f"materialized feature contract {key} mismatch")
        if consumed_contract[key] != first_identity["feature_list_contract"][key]:
            raise ValueError(f"consumed feature contract {key} mismatch")

    pit_store = PitUniverseStore(str(first_identity["pit_universe_artifact"]))
    if (
        pit_store.provenance.membership_sha256
        != first_identity["pit_membership_sha256"]
    ):
        raise ValueError("PIT membership hash mismatch")
    pit_instruments = set(pit_store.instruments)
    code_dependency_count = _validate_code_dependencies(
        list(first_identity["builder_code_dependencies"])
    )

    expected_base = _artifact_identity(first_identity)
    expected_base.pop("start", None)
    expected_base.pop("end", None)
    expected_full_base = dict(first_identity)
    expected_full_base.pop("start", None)
    expected_full_base.pop("end", None)
    total_rows = 0
    duplicate_rows = 0
    global_min_coverage = 1.0
    shard_results: list[dict[str, Any]] = []
    for shard in shards:
        identity = shard["identity"]
        full_identity_base = dict(identity)
        full_identity_base.pop("start", None)
        full_identity_base.pop("end", None)
        if full_identity_base != expected_full_base:
            raise ValueError("annual shard full identities are inconsistent")
        identity_base = _artifact_identity(identity)
        identity_base.pop("start", None)
        identity_base.pop("end", None)
        if identity_base != expected_base:
            raise ValueError("annual shard artifact identities are inconsistent")
        if shard["source_manifest_hash"] != manifest["source_manifest_hash"]:
            raise ValueError("annual shard source hash mismatch")
        if identity["start"] != shard["start"] or identity["end"] != shard["end"]:
            raise ValueError("annual shard identity range mismatch")

        path = _resolve_artifact_path(project_root, str(shard["path"]))
        meta_path = Path(f"{path}.meta.json")
        if meta_path.is_symlink() or not meta_path.is_file():
            raise ValueError(f"cache metadata missing: {meta_path}")
        data_sha256 = _sha256_file(path)
        if data_sha256 != shard["data_sha256"]:
            raise ValueError(f"cache data hash mismatch: {path}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("schema_version") != 1:
            raise ValueError(f"cache metadata schema mismatch: {meta_path}")
        if meta.get("identity") != identity:
            raise ValueError(f"cache metadata identity mismatch: {meta_path}")
        if meta.get("artifact_identity") != _artifact_identity(identity):
            raise ValueError(f"cache artifact identity mismatch: {meta_path}")
        if meta.get("data_sha256") != data_sha256:
            raise ValueError(f"cache metadata data hash mismatch: {meta_path}")

        frame = pd.read_parquet(path)
        if frame.columns.tolist() != stored_columns:
            raise ValueError(f"physical cache columns violate contract: {path}")
        if len(frame) != int(shard["rows"]) or len(frame) != int(meta["rows"]):
            raise ValueError(f"cache row count mismatch: {path}")
        if len(frame.columns) != int(meta["cols"]):
            raise ValueError(f"cache column count mismatch: {path}")
        if frame[["trade_date", "instrument"]].isna().any().any():
            raise ValueError(f"cache contains null keys: {path}")
        duplicates = int(
            frame.duplicated(subset=["trade_date", "instrument"]).sum()
        )
        if duplicates:
            raise ValueError(f"cache contains duplicate keys: {path}")
        dates = frame["trade_date"].astype(str).str[:10]
        coverage_start = str(meta["source_coverage_start"])
        coverage_end = str(meta["source_coverage_end"])
        if coverage_start != str(shard["source_coverage_start"]):
            raise ValueError(f"cache source coverage start mismatch: {path}")
        if coverage_end != str(shard["source_coverage_end"]):
            raise ValueError(f"cache source coverage end mismatch: {path}")
        if dates.min() < coverage_start or dates.max() > coverage_end:
            raise ValueError(f"cache rows exceed declared source coverage: {path}")
        unknown_instruments = set(frame["instrument"].astype(str)) - pit_instruments
        if unknown_instruments:
            raise ValueError(f"cache contains instruments outside PIT union: {path}")

        coverage = frame[materialized].notna().mean()
        if (coverage <= 0).any():
            raise ValueError(f"cache contains an entirely missing feature: {path}")
        numeric = frame[materialized].select_dtypes(include=[np.number])
        if not numeric.empty and np.isinf(numeric.to_numpy()).any():
            raise ValueError(f"cache contains infinite feature values: {path}")
        min_coverage = float(coverage.min())
        global_min_coverage = min(global_min_coverage, min_coverage)
        total_rows += len(frame)
        duplicate_rows += duplicates
        shard_results.append({
            "path": str(path),
            "data_sha256": data_sha256,
            "meta_sha256": _sha256_file(meta_path),
            "rows": len(frame),
            "columns": len(frame.columns),
            "date_min": dates.min(),
            "date_max": dates.max(),
            "instrument_count": int(frame["instrument"].nunique()),
            "min_feature_coverage": min_coverage,
            "median_feature_coverage": float(coverage.median()),
        })

    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "pass",
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "checks": {
            "manifest_and_file_hashes": "pass",
            "code_dependency_hashes": "pass",
            "feature_list_contracts": "pass",
            "materialized_and_consumed_column_contracts": "pass",
            "annual_range_and_source_coverage": "pass",
            "row_keys_unique_and_inside_pit_union": "pass",
            "feature_values_nonempty_and_finite": "pass",
        },
        "summary": {
            "shard_count": len(shards),
            "total_rows": total_rows,
            "duplicate_rows": duplicate_rows,
            "materialized_feature_count": len(materialized),
            "consumed_feature_count": len(consumed),
            "stored_column_count": len(stored_columns),
            "code_dependency_count": code_dependency_count,
            "minimum_feature_coverage": global_min_coverage,
            "cache_coverage_start": manifest["cache_coverage_start"],
            "cache_coverage_end": manifest["cache_coverage_end"],
            "cache_shard_identity_end": manifest["cache_shard_identity_end"],
        },
        "shards": shard_results,
    }
    _write_json_atomic(output_path, result)
    return result
