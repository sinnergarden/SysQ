"""Aggregate validated Stage-A artifacts into one auditable Alpha Map."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml


SCHEMA_VERSION = "alpha_map_v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _bool(value: str) -> bool:
    return str(value).strip().lower() == "true"


class AlphaMap:
    def __init__(self, config_path: str | Path, *, root: str | Path = "data/research"):
        self.config_path = Path(config_path).resolve()
        self.repo_root = Path(__file__).resolve().parents[2]
        self.root = Path(root).resolve()
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        if self.config.get("schema_version") != "alpha_map_request_v1":
            raise ValueError("unsupported Alpha Map request schema")
        self.alpha_map_id = str(self.config.get("alpha_map_id") or "").strip()
        if not self.alpha_map_id:
            raise ValueError("alpha_map_id is required")
        self.output_dir = self.root / "alpha_maps" / self.alpha_map_id

    @classmethod
    def from_config(cls, path: str | Path, **kwargs: Any) -> "AlphaMap":
        return cls(path, **kwargs)

    def _validated_catalog(self) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
        catalog_id = str(self.config["catalog_id"])
        directory = self.root / "feature_catalogs" / catalog_id
        manifest_path = directory / "manifest.json"
        validation_path = directory / "validation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if not validation.get("validated"):
            raise ValueError(f"feature catalog is not validated: {catalog_id}")
        if validation.get("catalog_identity_sha256") != manifest.get(
            "catalog_identity_sha256"
        ):
            raise ValueError("feature catalog validation identity mismatch")
        for name, digest in manifest.get("artifacts", {}).items():
            if _sha256(directory / name) != digest:
                raise ValueError(f"feature catalog artifact hash mismatch: {name}")
        rows = json.loads((directory / "feature_catalog.json").read_text(encoding="utf-8"))
        return manifest, rows, _sha256(validation_path)

    def _validated_label_suite(self) -> tuple[dict[str, Any], str]:
        suite_id = str(self.config["label_suite_id"])
        directory = self.root / "label_suites" / suite_id
        manifest_path = directory / "manifest.json"
        validation_path = directory / "validation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        if validation.get("status") != "PASS" or validation.get("failures"):
            raise ValueError(f"label suite is not validated: {suite_id}")
        if validation.get("input_suite_manifest_sha256") != _sha256(manifest_path):
            raise ValueError("label suite validation manifest hash mismatch")
        if validation.get("label_suite_identity_sha256") != manifest.get(
            "label_suite_identity_sha256"
        ):
            raise ValueError("label suite validation identity mismatch")
        return manifest, _sha256(validation_path)

    def _experiment(self, spec: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        config_path = (self.repo_root / str(spec["diagnostics_config"])).resolve()
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        diagnostics_id = str(config["diagnostics_id"])
        experiment_id = str(spec.get("experiment_id") or diagnostics_id)
        directory = self.root / "experiments" / diagnostics_id / "diagnostics"
        manifest_path = directory / "manifest.json"
        receipt_path = directory.parent / "diagnostics_validation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not receipt.get("validated"):
            raise ValueError(f"diagnostics are not validated: {diagnostics_id}")
        if receipt.get("manifest_sha256") != _sha256(manifest_path):
            raise ValueError(f"diagnostics manifest hash mismatch: {diagnostics_id}")
        if manifest.get("config_sha256") != _sha256(config_path):
            raise ValueError(f"diagnostics config hash mismatch: {diagnostics_id}")
        for name, output in manifest.get("outputs", {}).items():
            if _sha256(directory / name) != output["sha256"]:
                raise ValueError(f"diagnostics output hash mismatch: {diagnostics_id}/{name}")

        protocol = json.loads(
            (directory / "stage_a_protocol.json").read_text(encoding="utf-8")
        )
        if protocol.get("holdout_consumed"):
            raise ValueError(f"untouched holdout was consumed: {diagnostics_id}")
        with (directory / "stage_a_triage.csv").open(encoding="utf-8", newline="") as handle:
            triage = list(csv.DictReader(handle))
        with (directory / "coverage.csv").open(encoding="utf-8", newline="") as handle:
            coverage = {row["feature"]: row for row in csv.DictReader(handle)}
        with (directory / "coverage_yearly.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            yearly = list(csv.DictReader(handle))

        pit_tier = str(spec["pit_tier"])
        if pit_tier not in {"PIT-A", "PIT-B", "PIT-X"}:
            raise ValueError(f"invalid PIT tier for {experiment_id}: {pit_tier}")
        families = sorted({row["feature_family"] for row in triage})
        rows: list[dict[str, Any]] = []
        for family in families:
            selected = [row for row in triage if row["feature_family"] == family]
            features = [row["feature"] for row in selected]
            coverage_values = [float(coverage[f]["coverage"]) for f in features]
            missing_values = [float(coverage[f]["missing_rate"]) for f in features]
            inf_values = [float(coverage[f]["inf_rate"]) for f in features]
            yearly_values = [
                float(row["coverage"]) for row in yearly if row["feature"] in features
            ]
            discovery = sum(_bool(row["discovery_pass"]) for row in selected)
            confirmation = sum(_bool(row["confirmation_pass"]) for row in selected)
            if pit_tier == "PIT-X":
                status = "provisional_supported" if confirmation else "provisional_rejected"
            else:
                status = "confirmed" if confirmation else "rejected"
            rows.append({
                "experiment_id": experiment_id,
                "diagnostics_id": diagnostics_id,
                "horizon_sessions": int(protocol["horizon_sessions"]),
                "feature_family": family,
                "pit_tier": pit_tier,
                "track": str(spec["track"]),
                "availability_lag_sessions": spec.get("availability_lag_sessions"),
                "feature_trial_count": len(selected),
                "discovery_candidate_count": discovery,
                "confirmation_pass_count": confirmation,
                "promotion_eligible_count": confirmation if pit_tier != "PIT-X" else 0,
                "rejected_count": len(selected) - confirmation,
                "research_status": status,
                "signal_quality": (
                    "confirmed_cross_sectional_information"
                    if confirmation and pit_tier != "PIT-X"
                    else "provisional_cross_sectional_information"
                    if confirmation
                    else "discovery_only"
                    if discovery
                    else "no_stable_evidence"
                ),
                "stability": (
                    "confirmation_pass" if confirmation else
                    "confirmation_failed" if discovery else "discovery_failed"
                ),
                "coverage_min": min(coverage_values),
                "coverage_median": sorted(coverage_values)[len(coverage_values) // 2],
                "missing_rate_max": max(missing_values),
                "inf_rate_max": max(inf_values),
                "yearly_coverage_min": min(yearly_values),
                "yearly_coverage_max": max(yearly_values),
                "stage_b_status": (
                    "eligible" if confirmation and pit_tier != "PIT-X" else "not_entered"
                ),
                "stage_c_status": "not_entered",
                "portfolio_extractability": "not_tested",
            })
        try:
            recorded_config_path = str(config_path.relative_to(self.repo_root))
        except ValueError:
            recorded_config_path = str(config_path)
        lineage = {
            "experiment_id": experiment_id,
            "diagnostics_id": diagnostics_id,
            "diagnostics_config": recorded_config_path,
            "diagnostics_manifest_sha256": _sha256(manifest_path),
            "diagnostics_validation_sha256": _sha256(receipt_path),
            "diagnostics_identity_sha256": manifest["diagnostics_identity_sha256"],
            "feature_trial_count": int(protocol["feature_trial_count"]),
            "candidate_count": int(protocol["candidate_count"]),
            "confirmed_count": int(protocol["confirmed_count"]),
            "rejected_count": int(protocol["rejected_count"]),
            "holdout_consumed": False,
        }
        return rows, lineage

    def run(self) -> dict[str, Any]:
        catalog_manifest, catalog_rows, catalog_validation_sha = self._validated_catalog()
        label_manifest, label_validation_sha = self._validated_label_suite()
        rows: list[dict[str, Any]] = []
        experiments: list[dict[str, Any]] = []
        for spec in self.config.get("experiments", []):
            experiment_rows, lineage = self._experiment(spec)
            rows.extend(experiment_rows)
            experiments.append(lineage)
        if not rows:
            raise ValueError("Alpha Map requires at least one validated experiment")

        by_experiment = {item["experiment_id"]: item for item in experiments}
        ablations = []
        for spec in self.config.get("ablations", []):
            ids = list(spec.get("experiment_ids", []))
            missing = sorted(set(ids) - set(by_experiment))
            if missing:
                raise ValueError(f"ablation references unknown experiments: {missing}")
            relevant = [row for row in rows if row["experiment_id"] in ids]
            eligible = sum(row["promotion_eligible_count"] for row in relevant)
            provisional = sum(
                row["confirmation_pass_count"]
                for row in relevant
                if row["pit_tier"] == "PIT-X"
            )
            ablations.append({
                "ablation_id": str(spec["ablation_id"]),
                "components": list(spec.get("components", [])),
                "experiment_ids": ids,
                "promotion_eligible_feature_count": eligible,
                "provisional_supported_feature_count": provisional,
                "stage_b_status": "eligible" if eligible else "not_entered",
                "stage_c_status": "not_entered",
                "reason": (
                    "confirmed_stage_a_inputs_available"
                    if eligible else "no_confirmed_stage_a_inputs"
                ),
            })

        blocked = [
            {
                "feature": row["feature_name"],
                "pit_tier": row["pit_tier"],
                "reason": row["review_notes"],
            }
            for row in catalog_rows
            if row["review_status"] == "data-blocked"
        ]
        summary = {
            "experiment_count": len(experiments),
            "family_horizon_count": len(rows),
            "feature_trial_count": sum(item["feature_trial_count"] for item in experiments),
            "discovery_candidate_count": sum(item["candidate_count"] for item in experiments),
            "confirmed_count": sum(row["promotion_eligible_count"] for row in rows),
            "provisional_supported_count": sum(
                row["confirmation_pass_count"] for row in rows if row["pit_tier"] == "PIT-X"
            ),
            "rejected_count": sum(item["rejected_count"] for item in experiments),
            "data_blocked_feature_count": len(blocked),
            "holdout_consumed": False,
            "stage_b_entered": any(row["stage_b_status"] == "eligible" for row in rows),
            "stage_c_entered": False,
        }
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "alpha_map_id": self.alpha_map_id,
            "catalog": {
                "catalog_id": catalog_manifest["catalog_id"],
                "catalog_identity_sha256": catalog_manifest["catalog_identity_sha256"],
                "manifest_sha256": _sha256(
                    self.root / "feature_catalogs" / catalog_manifest["catalog_id"] / "manifest.json"
                ),
                "validation_sha256": catalog_validation_sha,
                "unique_feature_count": catalog_manifest["summary"]["unique_feature_count"],
            },
            "label_suite": {
                "suite_id": label_manifest["suite_id"],
                "label_suite_identity_sha256": label_manifest["label_suite_identity_sha256"],
                "manifest_sha256": _sha256(
                    self.root / "label_suites" / label_manifest["suite_id"] / "manifest.json"
                ),
                "validation_sha256": label_validation_sha,
            },
            "summary": summary,
            "rows": sorted(
                rows,
                key=lambda row: (
                    row["horizon_sessions"], row["pit_tier"],
                    row["feature_family"], row["availability_lag_sessions"] or 0,
                ),
            ),
            "data_blocked_features": sorted(blocked, key=lambda row: row["feature"]),
            "ablations": ablations,
            "future_directions": list(self.config.get("future_directions", [])),
        }
        _write(
            self.output_dir / "alpha_map.json",
            json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        )
        fields = list(artifact["rows"][0])
        from io import StringIO

        stream = StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(artifact["rows"])
        _write(self.output_dir / "alpha_map.csv", stream.getvalue())

        identity = {
            "schema_version": SCHEMA_VERSION,
            "alpha_map_id": self.alpha_map_id,
            "config_sha256": _sha256(self.config_path),
            "producer_code_sha256": _sha256(Path(__file__)),
            "catalog_identity_sha256": artifact["catalog"]["catalog_identity_sha256"],
            "label_suite_identity_sha256": artifact["label_suite"]["label_suite_identity_sha256"],
            "diagnostics_identities": sorted(
                item["diagnostics_identity_sha256"] for item in experiments
            ),
        }
        manifest = {
            **identity,
            "alpha_map_identity_sha256": _canonical_hash(identity),
            "experiments": experiments,
            "outputs": {
                name: {"sha256": _sha256(self.output_dir / name)}
                for name in ("alpha_map.json", "alpha_map.csv")
            },
        }
        _write(
            self.output_dir / "manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
        return {
            "alpha_map_identity_sha256": manifest["alpha_map_identity_sha256"],
            "manifest": str(self.output_dir / "manifest.json"),
        }
