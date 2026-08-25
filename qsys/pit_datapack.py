"""Portable directory export for one already-certified PIT baseline.

The pack is deliberately a plain directory: immutable source files, one
manifest, and one checksum list.  Qlib is excluded because it is a rebuildable
cache.  This module never fetches, repairs, trains, or certifies data.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Mapping

from qsys.pit_certification import CertificationError, sha256_file


SCHEMA_VERSION = "qsys_datapack_v1"
REQUIRED_CERTIFICATION_ARTIFACTS = {
    "audit_scope.json", "coverage.parquet", "exceptions.parquet", "evidence_snapshot.json",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.is_symlink():
            raise CertificationError(f"symlink path component is not allowed: {current}")


def _safe_file(root: Path, value: str | Path) -> Path:
    path = Path(value)
    candidate = (root / path).absolute() if not path.is_absolute() else path.absolute()
    _reject_symlink_components(candidate)
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise CertificationError(f"DataPack input escapes project root: {value}")
    if not resolved.is_file() or resolved.is_symlink():
        raise CertificationError(f"DataPack input file missing or symlinked: {value}")
    return resolved


def _safe_dir(root: Path, value: str | Path) -> Path:
    path = Path(value)
    candidate = (root / path).absolute() if not path.is_absolute() else path.absolute()
    _reject_symlink_components(candidate)
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise CertificationError(f"DataPack input escapes project root: {value}")
    if not resolved.is_dir() or resolved.is_symlink():
        raise CertificationError(f"DataPack input directory missing or symlinked: {value}")
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CertificationError(f"invalid DataPack JSON: {path}") from exc
    if not isinstance(value, dict):
        raise CertificationError(f"DataPack JSON must be an object: {path}")
    return value


def _add_file(
    files: dict[str, tuple[Path, str, int]], relative: str, source: Path,
    *, expected_sha256: str | None = None, expected_size: int | None = None,
) -> None:
    if not relative or relative.startswith("/") or ".." in Path(relative).parts:
        raise CertificationError(f"unsafe DataPack member path: {relative}")
    digest = expected_sha256 or sha256_file(source)
    size = source.stat().st_size if expected_size is None else expected_size
    if len(digest) != 64 or source.stat().st_size != size:
        raise CertificationError(f"DataPack source identity mismatch: {source}")
    if relative in files and files[relative][0] != source:
        raise CertificationError(f"duplicate DataPack member path: {relative}")
    files[relative] = (source, digest, size)


def _load_certification(certification_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    receipt_path = certification_dir / "audit_receipt.json"
    receipt = _read_json(receipt_path)
    if receipt.get("baseline_status") != "CERTIFIED":
        raise CertificationError("only a CERTIFIED baseline can be exported")
    artifacts = receipt.get("artifacts")
    if (
        not isinstance(artifacts, Mapping)
        or not REQUIRED_CERTIFICATION_ARTIFACTS.issubset(artifacts)
    ):
        raise CertificationError("certification receipt artifacts are missing")
    for name, expected in artifacts.items():
        path = _safe_file(certification_dir, str(name))
        if sha256_file(path) != expected:
            raise CertificationError(f"certification artifact mismatch: {name}")
    scope = _read_json(certification_dir / "audit_scope.json")
    if (
        scope.get("baseline_status") != "CERTIFIED"
        or scope.get("audit_id") != receipt.get("audit_id")
        or scope.get("baseline_id") != receipt.get("baseline_id")
    ):
        raise CertificationError("certification scope/receipt identity mismatch")
    identities = receipt.get("input_identities")
    if not isinstance(identities, Mapping) or any(
        scope.get(key) != value for key, value in identities.items()
    ):
        raise CertificationError("certification scope/input identity mismatch")
    if hashlib.sha256(_canonical_bytes(identities)).hexdigest() != receipt.get("audit_id"):
        raise CertificationError("certification audit_id does not match input identities")
    for receipt_key, identity_key in (
        ("selected_evidence_run_ids", "selected_evidence_run_ids"),
        ("selected_mutation_run_ids", "selected_mutation_run_ids"),
        ("evidence_query_sha256", "evidence_query_sha256"),
    ):
        if receipt.get(receipt_key) != identities.get(identity_key):
            raise CertificationError(f"certification receipt binding mismatch: {receipt_key}")
    snapshot = _read_json(certification_dir / "evidence_snapshot.json")
    query_payload = {
        key: snapshot.get(key) for key in (
            "selected_evidence_run_ids", "selected_mutation_run_ids",
            "full_mutation_ledger_sha256", "tables",
        )
    }
    query_sha = hashlib.sha256(_canonical_bytes(query_payload)).hexdigest()
    if (
        snapshot.get("schema_version") != "pit_evidence_snapshot_v1"
        or query_sha != snapshot.get("evidence_query_sha256")
        or query_sha != receipt.get("evidence_query_sha256")
        or snapshot.get("selected_evidence_run_ids") != receipt.get("selected_evidence_run_ids")
        or snapshot.get("selected_mutation_run_ids") != receipt.get("selected_mutation_run_ids")
        or snapshot.get("full_mutation_ledger_sha256") != identities.get("full_mutation_ledger_sha256")
    ):
        raise CertificationError("certification evidence snapshot binding mismatch")
    return receipt, scope


def _identity_file(project: Path, spec: Mapping[str, Any], name: str) -> Path:
    path = _safe_file(project, str(spec.get("path") or ""))
    if sha256_file(path) != str(spec.get("sha256") or ""):
        raise CertificationError(f"DataPack identity changed after certification: {name}")
    return path


def _corporate_action_files(
    *, project: Path, backtest_manifest: Mapping[str, Any],
    files: dict[str, tuple[Path, str, int]],
) -> None:
    accounting = backtest_manifest.get("accounting") or {}
    artifact_name = str(accounting.get("corporate_action_artifact") or "")
    if not artifact_name or not all(ch.isalnum() or ch in "_.-" for ch in artifact_name):
        raise CertificationError("certified backtest has no safe corporate-action artifact")
    root = _safe_dir(project, Path("data/research/corporate_actions") / artifact_name)
    manifest = _read_json(root / "manifest.json")
    embedded = accounting.get("corporate_action_manifest")
    if not isinstance(embedded, Mapping) or dict(embedded) != manifest:
        raise CertificationError("corporate-action manifest differs from certified backtest identity")
    events = _safe_file(root, "events.parquet")
    raw = _safe_file(root, str(manifest.get("source_raw_path") or ""))
    if sha256_file(events) != manifest.get("events_sha256"):
        raise CertificationError("corporate-action events hash mismatch")
    if sha256_file(raw) != manifest.get("source_raw_artifact_sha256"):
        raise CertificationError("corporate-action raw source hash mismatch")
    destination = f"data/corporate_actions/{artifact_name}"
    _add_file(files, f"{destination}/manifest.json", root / "manifest.json")
    _add_file(
        files, f"{destination}/events.parquet", events,
        expected_sha256=str(manifest["events_sha256"]),
    )
    _add_file(
        files, f"{destination}/{Path(str(manifest['source_raw_path'])).as_posix()}", raw,
        expected_sha256=str(manifest["source_raw_artifact_sha256"]),
    )


def _verify_content_members(
    *, root: Path, receipt: Mapping[str, Any], manifest_rows: Mapping[str, Mapping[str, Any]],
) -> None:
    """Require the transport to contain exactly the certified portable inputs."""

    identities = receipt["input_identities"]
    expected: dict[str, tuple[str, int | None]] = {}

    def add(relative: str, digest: Any, size: int | None = None) -> None:
        if (
            not relative or relative.startswith("/") or ".." in Path(relative).parts
            or not isinstance(digest, str) or len(digest) != 64
        ):
            raise CertificationError(f"invalid certified DataPack member identity: {relative}")
        value = (digest, size)
        if relative in expected and expected[relative] != value:
            raise CertificationError(f"conflicting certified DataPack member: {relative}")
        expected[relative] = value

    for name, digest in receipt["artifacts"].items():
        add(f"audit/{name}", digest)
    add("audit/audit_receipt.json", sha256_file(root / "audit/audit_receipt.json"))

    request = identities["request"]
    dependencies = identities["feature_dependencies"]
    add("contracts/baseline_request.yaml", request["sha256"])
    add("contracts/feature_dependencies.yaml", dependencies["sha256"])
    for spec in identities.get("source_contracts") or []:
        add(f"contracts/sources/{Path(str(spec['path'])).name}", spec["sha256"])

    baseline_identities = identities["identities"]
    for name, spec in baseline_identities.items():
        filename = Path(str(spec["path"])).name
        destination = (
            f"data/universes/{filename}"
            if str(name).startswith("universe_")
            else f"lineage/{name}/{filename}"
        )
        add(destination, spec["sha256"])

    snapshot = _read_json(root / "audit/evidence_snapshot.json")
    tables = snapshot["tables"]
    for run_id in receipt.get("selected_evidence_run_ids") or []:
        terminal_hashes = {
            str(row.get("terminal_receipt_sha256") or "")
            for row in tables.get("trusted_watermarks") or []
            if isinstance(row, Mapping) and str(row.get("run_id") or "") == str(run_id)
        }
        if len(terminal_hashes) != 1:
            raise CertificationError(f"certified terminal receipt identity missing: {run_id}")
        add(f"data/meta/source_runs/{run_id}/receipt.json", next(iter(terminal_hashes)))
    for row in tables.get("fetch_receipts") or []:
        if (
            not isinstance(row, Mapping)
            or row.get("status") not in {"success", "partial"}
            or row.get("payload_kind") != "raw_supplier"
        ):
            continue
        add(f"data/{row.get('payload_path')}", row.get("payload_sha256"))

    canonical = identities["canonical_materialization"]
    for spec in canonical["files"]:
        add(
            f"data/canonical/daily/{Path(str(spec['path'])).name}",
            spec["sha256"], int(spec["size"]),
        )

    backtest_spec = baseline_identities["backtest_manifest"]
    backtest_member = f"lineage/backtest_manifest/{Path(str(backtest_spec['path'])).name}"
    backtest = _read_json(_safe_file(root, backtest_member))
    accounting = backtest.get("accounting") or {}
    artifact_name = str(accounting.get("corporate_action_artifact") or "")
    ca = accounting.get("corporate_action_manifest")
    if (
        not artifact_name or not all(ch.isalnum() or ch in "_.-" for ch in artifact_name)
        or not isinstance(ca, Mapping)
    ):
        raise CertificationError("certified corporate-action identity is missing")
    ca_root = f"data/corporate_actions/{artifact_name}"
    ca_manifest_member = f"{ca_root}/manifest.json"
    packed_ca = _read_json(_safe_file(root, ca_manifest_member))
    if packed_ca != dict(ca):
        raise CertificationError("packed corporate-action manifest identity mismatch")
    add(ca_manifest_member, sha256_file(root / ca_manifest_member))
    add(f"{ca_root}/events.parquet", ca.get("events_sha256"))
    add(f"{ca_root}/{ca.get('source_raw_path')}", ca.get("source_raw_artifact_sha256"))

    if set(expected) != set(manifest_rows):
        raise CertificationError("DataPack portable content set is incomplete or contains extras")
    for relative, (digest, size) in expected.items():
        row = manifest_rows[relative]
        if row.get("sha256") != digest or (size is not None and row.get("size") != size):
            raise CertificationError(f"DataPack certified member identity mismatch: {relative}")


def export_certified_datapack(
    *, certification_dir: str | Path, output_dir: str | Path,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Export one explicit certification as an immutable portable directory."""

    project = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    certification = _safe_dir(project, certification_dir)
    receipt, scope = _load_certification(certification)
    identities = receipt.get("input_identities")
    if not isinstance(identities, Mapping):
        raise CertificationError("certification input identities are missing")
    files: dict[str, tuple[Path, str, int]] = {}

    for name in sorted(receipt["artifacts"]):
        _add_file(
            files, f"audit/{name}", _safe_file(project, certification / name),
            expected_sha256=str(receipt["artifacts"][name]),
        )
    receipt_digest = sha256_file(certification / "audit_receipt.json")
    _add_file(
        files, "audit/audit_receipt.json",
        _safe_file(project, certification / "audit_receipt.json"),
        expected_sha256=receipt_digest,
    )

    request_spec = identities.get("request")
    dependency_spec = identities.get("feature_dependencies")
    if not isinstance(request_spec, Mapping) or not isinstance(dependency_spec, Mapping):
        raise CertificationError("certification lacks portable request/feature identities")
    _add_file(
        files, "contracts/baseline_request.yaml", _identity_file(project, request_spec, "request"),
        expected_sha256=str(request_spec["sha256"]),
    )
    _add_file(
        files, "contracts/feature_dependencies.yaml",
        _identity_file(project, dependency_spec, "feature dependencies"),
        expected_sha256=str(dependency_spec["sha256"]),
    )
    for index, spec in enumerate(identities.get("source_contracts") or []):
        if not isinstance(spec, Mapping):
            raise CertificationError("invalid source contract identity")
        source = _identity_file(project, spec, f"source contract {index}")
        _add_file(
            files, f"contracts/sources/{source.name}", source,
            expected_sha256=str(spec["sha256"]),
        )

    baseline_identities = identities.get("identities")
    if not isinstance(baseline_identities, Mapping):
        raise CertificationError("certification baseline identities are missing")
    identity_paths: dict[str, Path] = {}
    for name, spec in sorted(baseline_identities.items()):
        if not isinstance(spec, Mapping):
            raise CertificationError(f"invalid baseline identity: {name}")
        source = _identity_file(project, spec, name)
        identity_paths[str(name)] = source
        destination = (
            f"data/universes/{source.name}"
            if str(name).startswith("universe_")
            else f"lineage/{name}/{source.name}"
        )
        _add_file(files, destination, source, expected_sha256=str(spec["sha256"]))

    evidence = _read_json(certification / "evidence_snapshot.json")
    if (
        evidence.get("schema_version") != "pit_evidence_snapshot_v1"
        or evidence.get("evidence_query_sha256") != receipt.get("evidence_query_sha256")
    ):
        raise CertificationError("evidence snapshot identity mismatch")
    tables = evidence.get("tables")
    if not isinstance(tables, Mapping):
        raise CertificationError("evidence snapshot tables are missing")
    data_root = _safe_dir(project, str(identities.get("evidence_data_root_relative") or ""))
    audit_root = _safe_dir(project, str(identities.get("audit_root_relative") or ""))
    for run_id in receipt.get("selected_evidence_run_ids") or []:
        run = str(run_id)
        if not run or not all(ch.isalnum() or ch in "_.-" for ch in run):
            raise CertificationError(f"unsafe evidence run id: {run}")
        terminal = _safe_file(project, audit_root / "source_runs" / run / "receipt.json")
        terminal_hashes = {
            str(row.get("terminal_receipt_sha256") or "")
            for row in tables.get("trusted_watermarks") or []
            if isinstance(row, Mapping) and str(row.get("run_id") or "") == run
        }
        terminal_digest = sha256_file(terminal)
        if terminal_hashes != {terminal_digest}:
            raise CertificationError(f"terminal evidence receipt changed after certification: {run}")
        _add_file(
            files, f"data/meta/source_runs/{run}/receipt.json", terminal,
            expected_sha256=terminal_digest,
        )
    for row in tables.get("fetch_receipts") or []:
        if not isinstance(row, Mapping) or row.get("status") not in {"success", "partial"}:
            continue
        if row.get("payload_kind") != "raw_supplier":
            continue
        if row.get("payload_verified") is not True:
            raise CertificationError("certified supplier payload is no longer verified")
        source = _safe_file(data_root, str(row.get("payload_path") or ""))
        expected = str(row.get("payload_sha256") or "")
        if sha256_file(source) != expected:
            raise CertificationError("certified supplier payload hash mismatch")
        _add_file(
            files, f"data/{source.relative_to(data_root).as_posix()}", source,
            expected_sha256=expected,
        )

    backtest_path = identity_paths.get("backtest_manifest")
    if backtest_path is None:
        raise CertificationError("backtest manifest identity is missing")
    backtest = _read_json(backtest_path)
    canonical = identities.get("canonical_materialization")
    if not isinstance(canonical, Mapping) or canonical.get("materialization") != "whole_consumed_instrument_files":
        raise CertificationError("certification lacks canonical materialization identity")
    canonical_files = canonical.get("files")
    if not isinstance(canonical_files, list) or not canonical_files:
        raise CertificationError("certification canonical file identities are missing")
    for spec in canonical_files:
        if not isinstance(spec, Mapping):
            raise CertificationError("invalid canonical file identity")
        source = _identity_file(project, spec, f"canonical {spec.get('instrument')}")
        if source.stat().st_size != spec.get("size"):
            raise CertificationError("canonical file size changed after certification")
        _add_file(
            files, f"data/canonical/daily/{source.name}", source,
            expected_sha256=str(spec["sha256"]), expected_size=int(spec["size"]),
        )
    _corporate_action_files(project=project, backtest_manifest=backtest, files=files)

    target = Path(output_dir).absolute()
    _reject_symlink_components(target.parent)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"DataPack output already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        entries: list[dict[str, Any]] = []
        for relative, (source, expected_digest, expected_size) in sorted(files.items()):
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            digest = sha256_file(destination)
            if digest != expected_digest or destination.stat().st_size != expected_size:
                raise CertificationError(f"DataPack source changed while copying: {relative}")
            entries.append({
                "path": relative, "sha256": digest, "size": destination.stat().st_size,
            })
        pack_id = hashlib.sha256(_canonical_bytes(entries)).hexdigest()
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "pack_id": pack_id,
            "baseline_id": receipt["baseline_id"],
            "audit_id": receipt["audit_id"],
            "certification_receipt_sha256": receipt_digest,
            "qlib_included": False,
            "canonical_materialization": "whole_consumed_instrument_files",
            "files": entries,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        checksums = [(entry["path"], entry["sha256"]) for entry in entries]
        checksums.append(("manifest.json", sha256_file(manifest_path)))
        (staging / "checksums.sha256").write_text(
            "".join(f"{digest}  {path}\n" for path, digest in sorted(checksums)),
            encoding="utf-8",
        )
        os.rename(staging, target)
        staging = None
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    return verify_datapack(target)


def verify_datapack(path: str | Path) -> dict[str, Any]:
    """Verify checksums and the certified identity of an unpacked DataPack."""

    candidate = Path(path).absolute()
    _reject_symlink_components(candidate)
    root = candidate.resolve()
    if not root.is_dir() or root.is_symlink():
        raise CertificationError(f"DataPack directory missing or symlinked: {path}")
    manifest = _read_json(root / "manifest.json")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("qlib_included") is not False:
        raise CertificationError("unsupported or non-canonical DataPack manifest")
    checksum_path = root / "checksums.sha256"
    checksums: dict[str, str] = {}
    for line in checksum_path.read_text(encoding="utf-8").splitlines():
        digest, separator, relative = line.partition("  ")
        if separator != "  " or len(digest) != 64 or relative in checksums:
            raise CertificationError("invalid DataPack checksum list")
        checksums[relative] = digest
    actual_files = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*") if item.is_file() and not item.is_symlink()
    }
    expected_files = set(checksums) | {"checksums.sha256"}
    if actual_files != expected_files:
        raise CertificationError("DataPack file set does not match checksums")
    if any(Path(relative).parts[:2] == ("data", "qlib_bin") for relative in actual_files):
        raise CertificationError("Qlib cache is forbidden in the canonical DataPack")
    for relative, expected in checksums.items():
        source = _safe_file(root, relative)
        if sha256_file(source) != expected:
            raise CertificationError(f"DataPack checksum mismatch: {relative}")
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise CertificationError("DataPack manifest file list is missing")
    if hashlib.sha256(_canonical_bytes(entries)).hexdigest() != manifest.get("pack_id"):
        raise CertificationError("DataPack pack_id mismatch")
    manifest_rows = {
        str(row.get("path")): row for row in entries if isinstance(row, Mapping)
    }
    manifest_members = set(manifest_rows)
    if len(manifest_rows) != len(entries) or manifest_members != set(checksums) - {"manifest.json"}:
        raise CertificationError("DataPack manifest/checksum membership mismatch")
    if any(Path(relative).parts[:2] == ("data", "qlib_bin") for relative in manifest_members):
        raise CertificationError("Qlib cache is forbidden in a canonical DataPack")
    for row in entries:
        if (
            not isinstance(row, Mapping)
            or checksums.get(str(row.get("path"))) != row.get("sha256")
            or _safe_file(root, str(row.get("path") or "")).stat().st_size != row.get("size")
        ):
            raise CertificationError("DataPack manifest file identity mismatch")
    receipt, _scope = _load_certification(root / "audit")
    if (
        receipt.get("baseline_status") != "CERTIFIED"
        or receipt.get("audit_id") != manifest.get("audit_id")
        or receipt.get("baseline_id") != manifest.get("baseline_id")
        or sha256_file(root / "audit/audit_receipt.json")
        != manifest.get("certification_receipt_sha256")
    ):
        raise CertificationError("DataPack certification identity mismatch")
    _verify_content_members(root=root, receipt=receipt, manifest_rows=manifest_rows)
    return {
        "status": "VERIFIED", "pack_id": manifest["pack_id"],
        "baseline_id": manifest["baseline_id"], "audit_id": manifest["audit_id"],
        "file_count": len(entries), "path": str(root),
    }
