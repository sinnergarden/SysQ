"""Read-only PIT baseline certification over explicit immutable inputs.

This module deliberately does not import the ingestion producer.  It reads the
v1 audit database through SQLite URI ``mode=ro`` and writes only a new,
exclusive certification directory.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

import pandas as pd
import yaml

from qsys.feature.registry import FeatureListRegistry


SCHEMA_VERSION = "pit_baseline_certification_v1"
EVIDENCE_SNAPSHOT_SCHEMA_VERSION = "pit_evidence_snapshot_v1"
COVERAGE_COLUMNS = [
    "source", "dataset", "endpoint", "field", "instrument", "date_start",
    "date_end", "scope_kind", "evidence_run_id", "receipt_id", "mutation_id",
    "status", "reason_code",
]
EXCEPTION_COLUMNS = [
    "exception_id", "severity", "reason_code", "source", "dataset", "endpoint",
    "field", "instrument", "date_start", "date_end", "affected_features_json",
    "mutation_run_id", "mutation_id", "details_json",
]
FIELD_ALIASES = {
    "$close": "close", "$open": "open", "$high": "high", "$low": "low",
    "$volume": "volume", "vol": "volume", "$amount": "amount",
    "$factor": "factor", "adj_factor": "factor",
    "n_cashflow_act": "op_cashflow", "n_income": "net_income",
    "rzye": "margin_balance", "rzmre": "margin_buy_amount",
    "rzche": "margin_repay_amount",
}
REQUIRED_TERMINAL_GATES = frozenset(
    {"fetch", "raw_payloads", "canonical_commit", "qlib_readback", "readiness", "contiguous_range"}
)


class CertificationError(RuntimeError):
    """Invalid request or runtime condition that prevents a complete report."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normal_date(value: Any) -> str:
    text = str(value).strip().replace("-", "")
    if len(text) != 8 or not text.isdigit():
        raise CertificationError(f"invalid date: {value!r}")
    return text


def normalize_field(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return FIELD_ALIASES.get(text, text[1:] if text.startswith("$") else text)


def stable_scope_hash(values: Iterable[str]) -> str:
    normalized = sorted({str(value).strip() for value in values if str(value).strip()})
    return _sha256_bytes(_canonical_bytes(normalized))


def _safe_path(project_root: Path, value: str | Path) -> Path:
    root = project_root.resolve()
    path = Path(value)
    resolved = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if resolved != root and root not in resolved.parents:
        raise CertificationError(f"input path escapes project root: {value}")
    if not resolved.is_file():
        raise CertificationError(f"input artifact missing: {value}")
    return resolved


def _load_yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CertificationError(f"YAML must be a mapping: {path}")
    return value


def _verify_identity(project_root: Path, spec: Mapping[str, Any], name: str) -> Path:
    path = _safe_path(project_root, str(spec.get("path") or ""))
    expected = str(spec.get("sha256") or "")
    actual = sha256_file(path)
    if len(expected) != 64 or actual != expected:
        raise CertificationError(f"{name} sha256 mismatch: expected={expected}, actual={actual}")
    return path


def _normalize_research_config(value: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(value))
    if "signal_transforms" in result and "transforms" not in result:
        result["transforms"] = result.pop("signal_transforms")
    for name in ("backtests", "signal_combinations", "strategies"):
        result.setdefault(name, [])
    result.setdefault("feature_cache_root", "data/feature_cache")
    result.setdefault("materialize_on_miss", False)
    result.setdefault("use_feature_cache", False)
    result.setdefault("write_through", False)
    result.setdefault("description", None)
    result.setdefault("title", None)
    return result


def validate_feature_dependencies(
    feature_list_id: str, dependency_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind the flat dependency assertion to the registry's ordered contract."""

    registry = FeatureListRegistry.contract(feature_list_id)
    dependency = _load_yaml(dependency_path)
    if dependency.get("schema_version") != "feature_dependency_assertion_v1":
        raise CertificationError("unsupported feature dependency schema")
    if dependency.get("feature_list_id") != feature_list_id:
        raise CertificationError("feature dependency feature_list_id mismatch")
    entries = dependency.get("features")
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise CertificationError("feature dependencies must contain a flat features list")
    names = [str(item.get("feature")) for item in entries]
    if names != registry["features"]:
        raise CertificationError("feature dependency order/content does not match FeatureListRegistry")
    if int(dependency.get("feature_count", -1)) != registry["feature_count"]:
        raise CertificationError("feature dependency count mismatch")
    if dependency.get("features_sha256") != registry["features_sha256"]:
        raise CertificationError("feature dependency ordered feature hash mismatch")
    required_keys = {"feature", "required", "dependencies"}
    for item in entries:
        missing = required_keys - set(item)
        if missing:
            raise CertificationError(f"feature dependency missing {sorted(missing)}: {item.get('feature')}")
        if item["required"] is not True:
            raise CertificationError(f"baseline feature must be required: {item['feature']}")
        scopes = item["dependencies"]
        if not isinstance(scopes, list) or not scopes:
            raise CertificationError(f"dependencies must be non-empty: {item['feature']}")
        for scope in scopes:
            if not isinstance(scope, dict):
                raise CertificationError(f"dependency scope must be a mapping: {item['feature']}")
            for key in ("source", "dataset", "endpoint", "leaf_fields", "visibility", "pit_status"):
                if key not in scope:
                    raise CertificationError(f"dependency scope missing {key}: {item['feature']}")
            if not isinstance(scope["leaf_fields"], list) or not scope["leaf_fields"]:
                raise CertificationError(f"leaf_fields must be non-empty: {item['feature']}")
            for literal in ("source", "dataset", "endpoint"):
                if not isinstance(scope[literal], str) or not scope[literal].strip():
                    raise CertificationError(f"{literal} must be a literal: {item['feature']}")
    lookback = dependency.get("lookback_contract")
    if not isinstance(lookback, dict):
        raise CertificationError("feature dependency lookback_contract missing")
    for name in ("runtime_max_lookback_trading_sessions", "input_warmup_calendar_days"):
        if not isinstance(lookback.get(name), int) or lookback[name] < 0:
            raise CertificationError(f"invalid lookback contract: {name}")
    return registry, dependency


def _checkpoint_set_payload(manifests: Sequence[tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, manifest in manifests:
        window = manifest.get("identity", {}).get("window", {})
        rows.append({
            "window_id": str(window.get("window_id")),
            "checkpoint_key": path.name.removesuffix(".manifest.json"),
            "row_count": int(manifest.get("row_count", -1)),
            "predictions_sha256": str(manifest.get("predictions_sha256")),
            "manifest_sha256": sha256_file(path),
        })
    rows.sort(key=lambda item: item["window_id"])
    return rows


def load_checkpoint_scope(
    *, checkpoint_root: Path, request: Mapping[str, Any], research_config: Mapping[str, Any]
) -> dict[str, Any]:
    if request.get("model") != {"mode": "ephemeral_per_window", "path": None}:
        raise CertificationError("baseline model must be ephemeral_per_window with path=null")
    paths = sorted(checkpoint_root.glob("**/*.manifest.json"))
    expected_count = int(request["checkpoint_count"])
    if len(paths) != expected_count:
        raise CertificationError(f"checkpoint count mismatch: expected={expected_count}, actual={len(paths)}")
    manifests: list[tuple[Path, dict[str, Any]]] = []
    for path in paths:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise CertificationError(f"invalid checkpoint manifest: {path}")
        predictions = path.with_name(str(value.get("predictions_file") or ""))
        if not predictions.is_file() or sha256_file(predictions) != value.get("predictions_sha256"):
            raise CertificationError(f"checkpoint predictions mismatch: {path}")
        manifests.append((path, value))
    set_rows = _checkpoint_set_payload(manifests)
    set_hash = _sha256_bytes(_canonical_bytes({"checkpoints": set_rows}))
    if set_hash != request["checkpoint_set_sha256"]:
        raise CertificationError("checkpoint set hash mismatch")
    identities = [item[1].get("identity", {}) for item in manifests]
    base_ids = {str(item.get("base_identity_sha256")) for item in identities}
    if base_ids != {str(request["base_identity_sha256"])}:
        raise CertificationError("checkpoint base identity mismatch")
    canonical_config = _canonical_bytes(_normalize_research_config(research_config))
    for identity in identities:
        if _canonical_bytes(_normalize_research_config(identity.get("research_config", {}))) != canonical_config:
            raise CertificationError("checkpoint parsed research config mismatch")
    windows = [dict(item.get("window") or {}) for item in identities]
    for window in windows:
        for name in ("train_start", "train_end", "predict_start", "predict_end", "window_id"):
            if not window.get(name):
                raise CertificationError(f"checkpoint window missing {name}")
    windows.sort(key=lambda item: item["window_id"])
    return {
        "checkpoint_count": len(windows),
        "checkpoint_set_sha256": set_hash,
        "base_identity_sha256": next(iter(base_ids)),
        "windows": windows,
        "train_date_start": min(item["train_start"] for item in windows),
        "train_date_end": max(item["train_end"] for item in windows),
        "predict_date_start": min(item["predict_start"] for item in windows),
        "predict_date_end": max(item["predict_end"] for item in windows),
        "model": {"mode": "ephemeral_per_window", "path": None},
    }


def _merge_instrument_spans(frame: pd.DataFrame, start: str, end: str) -> list[dict[str, str]]:
    spans: list[dict[str, str]] = []
    for instrument, group in frame.groupby("instrument", sort=True):
        values = sorted(
            (max(_normal_date(row.effective_from), start), min(_normal_date(row.effective_to), end))
            for row in group.itertuples(index=False)
            if _normal_date(row.effective_from) <= end and _normal_date(row.effective_to) >= start
        )
        merged: list[list[str]] = []
        for left, right in values:
            if left > right:
                continue
            if not merged or left > (datetime.strptime(merged[-1][1], "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d"):
                merged.append([left, right])
            elif right > merged[-1][1]:
                merged[-1][1] = right
        spans.extend(
            {"instrument": str(instrument), "date_start": left, "date_end": right}
            for left, right in merged
        )
    return spans


def load_universe_spans(
    membership_path: Path, *, feature_start: str, feature_end: str, max_lookback_days: int
) -> tuple[list[dict[str, str]], dict[str, str]]:
    frame = pd.read_parquet(membership_path)
    required = {"instrument", "effective_from", "effective_to"}
    if not required.issubset(frame.columns):
        raise CertificationError("PIT membership is missing interval columns")
    registry_end = max(_normal_date(value) for value in frame["effective_to"])
    feature_start = _normal_date(feature_start)
    feature_end = _normal_date(feature_end)
    input_floor = (
        datetime.strptime(feature_start, "%Y%m%d") - timedelta(days=max_lookback_days)
    ).strftime("%Y%m%d")
    eligible = frame[
        frame["effective_from"].map(_normal_date).le(feature_end)
        & frame["effective_to"].map(_normal_date).ge(feature_start)
    ].copy()
    eligible["effective_from"] = eligible["effective_from"].map(
        lambda value: (
            max(
                input_floor,
                (
                    datetime.strptime(_normal_date(value), "%Y%m%d")
                    - timedelta(days=max_lookback_days)
                ).strftime("%Y%m%d"),
            )
        )
    )
    eligible["effective_to"] = eligible["effective_to"].map(
        lambda value: min(_normal_date(value), feature_end)
    )
    scope_start = min(
        (str(value) for value in eligible["effective_from"]), default=feature_start
    )
    scope_end = min(_normal_date(feature_end), registry_end)
    return _merge_instrument_spans(eligible, scope_start, scope_end), {
        "date_start": scope_start, "date_end": scope_end,
        "feature_input_start": _normal_date(feature_start),
        "feature_input_end": _normal_date(feature_end),
        "lookback_calendar_days": str(max_lookback_days),
        "lookback_provenance": "explicit_feature_dependency_contract",
    }


def classify_mutation_intersection(
    mutation: Mapping[str, Any], scope: Mapping[str, Any]
) -> str:
    """Four-dimensional, fail-closed source/field/instrument/date comparison."""

    if mutation.get("mutation_type") == "noop":
        return "DISJOINT"
    dimensions: list[str] = []
    for name in ("source", "dataset", "endpoint"):
        left, right = mutation.get(name), scope.get(name)
        if name == "endpoint" and str(left) == "daily_bundle":
            # The producer deliberately records one canonical commit receipt
            # for the merged supplier bundle.  Supplier endpoints remain
            # authoritative for fetch coverage, but are not a disjointness
            # dimension for this canonical mutation envelope.
            dimensions.append("INTERSECTS")
            continue
        if left is None or right is None:
            dimensions.append("UNKNOWN")
        else:
            dimensions.append("INTERSECTS" if str(left) == str(right) else "DISJOINT")
    mutation_fields = mutation.get("fields")
    scope_field = normalize_field(scope.get("field"))
    if not isinstance(mutation_fields, (list, tuple, set)) or scope_field is None:
        dimensions.append("UNKNOWN")
    else:
        normalized = {normalize_field(item) for item in mutation_fields}
        dimensions.append("INTERSECTS" if scope_field in normalized else "DISJOINT")
    left, right = mutation.get("symbol"), scope.get("instrument")
    dimensions.append(
        "UNKNOWN" if left is None or right is None
        else ("INTERSECTS" if str(left) == str(right) else "DISJOINT")
    )
    try:
        overlaps = not (
            _normal_date(mutation["date_end"]) < _normal_date(scope["date_start"])
            or _normal_date(mutation["date_start"]) > _normal_date(scope["date_end"])
        )
        dimensions.append("INTERSECTS" if overlaps else "DISJOINT")
    except (KeyError, CertificationError):
        dimensions.append("UNKNOWN")
    if "DISJOINT" in dimensions:
        return "DISJOINT"
    if all(value == "INTERSECTS" for value in dimensions):
        return "INTERSECTS"
    return "UNKNOWN"


def _build_mutation_scope_index(
    scopes: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str, str, str], list[int]]:
    index: dict[tuple[str, str, str, str], list[int]] = {}
    for scope_index, scope in enumerate(scopes):
        field = normalize_field(scope.get("field"))
        values = (
            scope.get("source"), scope.get("dataset"), field, scope.get("instrument"),
        )
        if field is None or any(value is None or not str(value).strip() for value in values):
            continue
        key = tuple(str(value) for value in values)
        index.setdefault(key, []).append(scope_index)
    return index


def _mutation_candidate_indices(
    mutation: Mapping[str, Any],
    scope_index: Mapping[tuple[str, str, str, str], Sequence[int]],
) -> tuple[list[int], bool]:
    scalar_values = [
        mutation.get("source"), mutation.get("dataset"), mutation.get("endpoint"),
        mutation.get("symbol"), mutation.get("date_start"), mutation.get("date_end"),
    ]
    fields = mutation.get("fields")
    if (
        any(value is None or not str(value).strip() for value in scalar_values)
        or not isinstance(fields, (list, tuple, set))
        or not fields
    ):
        return [], True
    normalized_fields = {normalize_field(field) for field in fields}
    if None in normalized_fields:
        return [], True
    try:
        if _normal_date(mutation["date_start"]) > _normal_date(mutation["date_end"]):
            return [], True
    except (KeyError, CertificationError):
        return [], True
    candidates: set[int] = set()
    for field in normalized_fields:
        key = (
            str(mutation["source"]), str(mutation["dataset"]),
            str(field), str(mutation["symbol"]),
        )
        candidates.update(scope_index.get(key, ()))
    return sorted(candidates), False


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    value = dict(row)
    for source, target in (
        ("requested_scope_json", "requested_scope"),
        ("response_columns_json", "response_columns"),
        ("error_json", "error"),
        ("fields_json", "fields"),
        ("payload_json", "payload"),
    ):
        if source in value:
            raw = value.pop(source)
            value[target] = json.loads(raw) if raw else None
    return value


def read_selected_evidence(
    audit_db: Path, evidence_run_ids: Sequence[str], mutation_run_ids: Sequence[str]
) -> dict[str, Any]:
    """Read one SQLite snapshot without ever opening the producer store."""

    if not audit_db.is_file():
        raise CertificationError(f"audit database missing: {audit_db}")
    before = sha256_file(audit_db)
    uri = f"file:{quote(str(audit_db.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != 1:
            raise CertificationError(f"unsupported audit schema version {version}")
        selected_evidence = sorted(set(evidence_run_ids))
        placeholders = ",".join("?" for _ in selected_evidence)
        tables: dict[str, list[dict[str, Any]]] = {
            "fetch_receipts": [], "field_receipt_links": [], "canonical_mutations": [],
            "trusted_watermarks": [], "audit_journal": [],
        }
        if selected_evidence:
            queries = {
                "fetch_receipts": f"SELECT * FROM fetch_receipts WHERE run_id IN ({placeholders}) ORDER BY run_id,receipt_id",
                "field_receipt_links": f"SELECT * FROM field_receipt_links WHERE run_id IN ({placeholders}) ORDER BY run_id,dataset,field_name,receipt_id",
                "trusted_watermarks": f"SELECT * FROM trusted_watermarks WHERE run_id IN ({placeholders}) ORDER BY source,field_name,scope_key",
                "audit_journal": f"SELECT * FROM audit_journal WHERE run_id IN ({placeholders}) ORDER BY run_id,seq",
            }
            for name, query in queries.items():
                tables[name] = [_decode_row(row) for row in connection.execute(query, selected_evidence)]
        tables["canonical_mutations"] = [
            _decode_row(row) for row in connection.execute(
                "SELECT * FROM canonical_mutations ORDER BY run_id,mutation_id"
            )
        ]
        connection.rollback()
    finally:
        connection.close()
    after = sha256_file(audit_db)
    if after != before:
        raise CertificationError("read-only audit query changed database bytes")
    data_root = audit_db.resolve().parent.parent if audit_db.parent.name == "audit" else audit_db.resolve().parent
    for receipt in tables["fetch_receipts"]:
        if receipt.get("status") not in {"success", "partial"}:
            receipt["payload_verified"] = False
            receipt["payload_verification_reason"] = "status_has_no_payload"
            continue
        raw_path = receipt.get("payload_path")
        expected = receipt.get("payload_sha256")
        try:
            payload_path = (data_root / str(raw_path)).resolve()
            if data_root != payload_path and data_root not in payload_path.parents:
                raise ValueError("payload path escapes data root")
            verified = bool(expected) and payload_path.is_file() and sha256_file(payload_path) == expected
        except (OSError, ValueError):
            verified = False
        receipt["payload_verified"] = verified
        receipt["payload_verification_reason"] = "ok" if verified else "payload_missing_or_hash_mismatch"
    evidence_present = {
        str(row["run_id"])
        for name, rows in tables.items() if name != "canonical_mutations"
        for row in rows if row.get("run_id") is not None
    }
    mutation_present = {
        str(row["run_id"]) for row in tables["canonical_mutations"] if row.get("run_id") is not None
    }
    missing_evidence = sorted(set(evidence_run_ids) - evidence_present)
    missing_mutation = sorted(set(mutation_run_ids) - mutation_present)
    mutation_ledger_sha256 = _sha256_bytes(_canonical_bytes(tables["canonical_mutations"]))
    query_payload = {
        "selected_evidence_run_ids": sorted(set(evidence_run_ids)),
        "selected_mutation_run_ids": sorted(set(mutation_run_ids)),
        "full_mutation_ledger_sha256": mutation_ledger_sha256,
        "tables": tables,
    }
    return {
        **query_payload,
        "evidence_query_sha256": _sha256_bytes(_canonical_bytes(query_payload)),
        "audit_db_sha256": before,
        "full_mutation_ledger_sha256": mutation_ledger_sha256,
        "missing_evidence_run_ids": missing_evidence,
        "missing_mutation_run_ids": missing_mutation,
    }


def _exception(
    reason: str, severity: str, features: Iterable[str], details: Any, **location: Any,
) -> dict[str, Any]:
    value = {
        "exception_id": "",
        "reason_code": reason,
        "severity": severity,
        "source": location.get("source"),
        "dataset": location.get("dataset"),
        "endpoint": location.get("endpoint"),
        "field": location.get("field"),
        "instrument": location.get("instrument"),
        "date_start": location.get("date_start"),
        "date_end": location.get("date_end"),
        "affected_features_json": json.dumps(sorted(set(features)), ensure_ascii=False),
        "mutation_run_id": location.get("mutation_run_id"),
        "mutation_id": location.get("mutation_id"),
        "details_json": json.dumps(details, sort_keys=True, ensure_ascii=False, default=str),
    }
    value["exception_id"] = _sha256_bytes(_canonical_bytes({key: value[key] for key in value if key != "exception_id"}))
    return value


def _scope_rows(
    dependencies: Mapping[str, Any], spans: Sequence[Mapping[str, str]]
) -> tuple[list[dict[str, Any]], dict[tuple[str, str, str, str], list[str]]]:
    keys: dict[tuple[str, str, str, str], list[str]] = {}
    for item in dependencies["features"]:
        for dependency in item["dependencies"]:
            for field in dependency["leaf_fields"]:
                key = (
                    str(dependency["source"]), str(dependency["dataset"]),
                    str(dependency["endpoint"]), str(normalize_field(field)),
                )
                keys.setdefault(key, []).append(str(item["feature"]))
    rows: list[dict[str, Any]] = []
    for (source, dataset, endpoint, field), _features in sorted(keys.items()):
        for span in spans:
            rows.append({
                "source": source, "dataset": dataset, "endpoint": endpoint,
                "field": field, "instrument": span["instrument"],
                "date_start": span["date_start"], "date_end": span["date_end"],
                "scope_kind": "feature_dependency",
            })
    return rows, keys


def _terminal_proof_valid(
    *, audit_db: Path, watermark: Mapping[str, Any], receipt: Mapping[str, Any],
    link: Mapping[str, Any],
    scope_start: str, scope_end: str, consumed_instruments: Sequence[str],
) -> bool:
    run_id = str(watermark.get("run_id") or "")
    if (
        not run_id or run_id in {".", ".."}
        or not all(char.isalnum() or char in "_.-" for char in run_id)
    ):
        return False
    audit_root = audit_db.resolve().parent
    terminal_path = audit_root / "source_runs" / run_id / "receipt.json"
    try:
        _reject_symlink_components(terminal_path.absolute())
        resolved = terminal_path.resolve(strict=True)
        if audit_root != resolved and audit_root not in resolved.parents:
            return False
        expected_terminal_sha = str(watermark.get("terminal_receipt_sha256") or "")
        if len(expected_terminal_sha) != 64 or sha256_file(resolved) != expected_terminal_sha:
            return False
        terminal = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, CertificationError):
        return False
    gates = terminal.get("terminal_gates")
    if (
        terminal.get("run_id") != run_id
        or terminal.get("trust_state") != "trusted"
        or not isinstance(gates, dict)
        or set(gates) != REQUIRED_TERMINAL_GATES
        or any(gates[name] is not True for name in REQUIRED_TERMINAL_GATES)
    ):
        return False
    terminal_fetches = terminal.get("fetch_receipts")
    if not isinstance(terminal_fetches, list):
        return False
    embedded = next(
        (row for row in terminal_fetches if row.get("receipt_id") == receipt.get("receipt_id")),
        None,
    )
    if embedded is None:
        return False
    terminal_links = terminal.get("field_receipt_links")
    expected_link = (
        str(link.get("run_id") or ""), str(link.get("dataset") or ""),
        normalize_field(link.get("field_name")), str(link.get("receipt_id") or ""),
    )
    if (
        not isinstance(terminal_links, list)
        or None in expected_link
        or not all(expected_link[index] for index in (0, 1, 3))
        or not any(
            isinstance(row, dict)
            and (
                str(row.get("run_id") or ""), str(row.get("dataset") or ""),
                normalize_field(row.get("field_name")), str(row.get("receipt_id") or ""),
            ) == expected_link
            for row in terminal_links
        )
    ):
        return False
    for name in (
        "run_id", "source", "endpoint", "status", "payload_kind", "payload_path",
        "payload_sha256", "response_date_min", "response_date_max",
    ):
        if embedded.get(name) != receipt.get(name):
            return False
    requested = receipt.get("requested_scope")
    if embedded.get("requested_scope") != requested or not isinstance(requested, dict):
        return False
    if (
        receipt.get("status") != "success"
        or receipt.get("payload_kind") != "raw_supplier"
        or receipt.get("payload_verified") is not True
    ):
        return False
    try:
        requested_start = _normal_date(requested["date_start"])
        requested_end = _normal_date(requested["date_end"])
        response_start = _normal_date(receipt["response_date_min"])
        response_end = _normal_date(receipt["response_date_max"])
    except (KeyError, CertificationError):
        return False
    expected_instruments = sorted({str(value).strip() for value in consumed_instruments if str(value).strip()})
    return (
        requested_start <= scope_start <= scope_end <= requested_end
        and requested_start <= response_start <= response_end <= requested_end
        and requested.get("symbol_count") == len(expected_instruments)
        and requested.get("symbols_sha256") == stable_scope_hash(expected_instruments)
    )


def _coverage_for_scopes(
    scopes: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any], scope_key: str,
    *, audit_db: Path, consumed_instruments: Sequence[str],
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    receipts = {row["receipt_id"]: row for row in evidence["tables"]["fetch_receipts"]}
    links = evidence["tables"]["field_receipt_links"]
    watermarks = evidence["tables"]["trusted_watermarks"]
    scope_start = min(str(scope["date_start"]) for scope in scopes)
    scope_end = max(str(scope["date_end"]) for scope in scopes)
    candidates: dict[tuple[str, str, str, str], list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]] = {}
    for link in links:
        receipt = receipts.get(link["receipt_id"])
        if receipt is None:
            continue
        field = normalize_field(link["field_name"])
        watermark = next((
            row for row in watermarks
            if row["run_id"] == link["run_id"] and row["source"] == receipt["source"]
            and normalize_field(row["field_name"]) == field and row["scope_key"] == scope_key
        ), None)
        if watermark is not None and _terminal_proof_valid(
            audit_db=audit_db, watermark=watermark, receipt=receipt, link=link,
            scope_start=scope_start, scope_end=scope_end,
            consumed_instruments=consumed_instruments,
        ):
            key = (receipt["source"], link["dataset"], receipt["endpoint"], str(field))
            candidates.setdefault(key, []).append((link, receipt, watermark))
    rows: list[dict[str, Any]] = []
    proofs: dict[int, list[dict[str, Any]]] = {}
    for scope_index, scope in enumerate(scopes):
        key = (scope["source"], scope["dataset"], scope["endpoint"], scope["field"])
        chosen = None
        valid_proofs: list[dict[str, Any]] = []
        for link, receipt, watermark in candidates.get(key, []):
            response_start = receipt.get("response_date_min")
            response_end = receipt.get("response_date_max")
            if (
                receipt.get("status") == "success" and receipt.get("payload_verified")
                and response_start and response_end
                and _range_covers(
                    watermark.get("range_start"), watermark.get("trusted_through"),
                    scope.get("date_start"), scope.get("date_end"),
                )
                and watermark.get("terminal_receipt_sha256")
            ):
                proof = {"link": link, "receipt": receipt, "watermark": watermark}
                valid_proofs.append(proof)
                if chosen is None:
                    chosen = (link, receipt)
        if valid_proofs:
            proofs[scope_index] = valid_proofs
        rows.append({
            **scope,
            "evidence_run_id": chosen[0]["run_id"] if chosen else None,
            "receipt_id": chosen[1]["receipt_id"] if chosen else None,
            "mutation_id": None,
            "status": "COVERED" if chosen else "MISSING",
            "reason_code": "EVIDENCE_INTERVAL_COVERED" if chosen else "FIELD_EVIDENCE_NOT_COVERING_INTERVAL",
        })
    return rows, proofs


def _range_covers(
    range_start: Any, range_end: Any, covered_start: Any, covered_end: Any,
) -> bool:
    try:
        return (
            _normal_date(range_start) <= _normal_date(covered_start)
            and _normal_date(range_end) >= _normal_date(covered_end)
        )
    except CertificationError:
        return False


def _aware_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _proof_accounts_mutation(
    proof: Mapping[str, Any], mutation: Mapping[str, Any],
) -> bool:
    watermark = proof.get("watermark")
    if not isinstance(watermark, Mapping) or not _range_covers(
        watermark.get("range_start"), watermark.get("trusted_through"),
        mutation.get("date_start"), mutation.get("date_end"),
    ):
        return False
    watermark_time = _aware_utc(watermark.get("updated_at"))
    mutation_time = _aware_utc(mutation.get("ingested_at"))
    return (
        watermark_time is not None
        and mutation_time is not None
        and watermark_time >= mutation_time
    )


def _artifact_identities(project_root: Path, request: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Path]]:
    identities: dict[str, Any] = {}
    paths: dict[str, Path] = {}
    for name in ("research_config", "signal_manifest", "signal_predictions", "universe_manifest", "universe_membership", "universe_registry", "backtest_manifest"):
        spec = request["identities"][name]
        path = _verify_identity(project_root, spec, name)
        paths[name] = path
        identities[name] = {"path": str(spec["path"]), "sha256": str(spec["sha256"])}
    backtest = json.loads(paths["backtest_manifest"].read_text(encoding="utf-8"))
    for name, artifact in (backtest.get("artifacts") or {}).items():
        path = paths["backtest_manifest"].parent / str(artifact.get("path") or "")
        if not path.is_file() or sha256_file(path) != artifact.get("sha256"):
            raise CertificationError(f"backtest embedded artifact mismatch: {name}")
    signal = json.loads(paths["signal_manifest"].read_text(encoding="utf-8"))
    if signal.get("predictions_sha256") != identities["signal_predictions"]["sha256"]:
        raise CertificationError("signal manifest predictions identity mismatch")
    universe = json.loads(paths["universe_manifest"].read_text(encoding="utf-8"))
    if universe.get("membership_sha256") != identities["universe_membership"]["sha256"]:
        raise CertificationError("universe manifest membership identity mismatch")
    if universe.get("registry_sha256") != identities["universe_registry"]["sha256"]:
        raise CertificationError("universe manifest registry identity mismatch")
    return identities, paths


def _canonical_materialization_identity(
    project_root: Path, backtest_manifest_path: Path, instruments: Sequence[str],
) -> dict[str, Any]:
    backtest = json.loads(backtest_manifest_path.read_text(encoding="utf-8"))
    relative_root = str((backtest.get("accounting") or {}).get("canonical_data_root") or "")
    root = (project_root / relative_root).absolute()
    _reject_symlink_components(root)
    resolved_root = root.resolve()
    if (
        not relative_root or not resolved_root.is_dir()
        or (resolved_root != project_root and project_root not in resolved_root.parents)
    ):
        raise CertificationError("certified backtest canonical_data_root is missing or unsafe")
    files: list[dict[str, Any]] = []
    for instrument in sorted(set(instruments)):
        if (
            not instrument or instrument in {".", ".."}
            or Path(instrument).name != instrument
            or not all(ch.isalnum() or ch in "._-" for ch in instrument)
        ):
            raise CertificationError(f"unsafe consumed canonical instrument: {instrument!r}")
        candidate = (resolved_root / f"{instrument}.feather").absolute()
        _reject_symlink_components(candidate)
        path = candidate.resolve()
        if resolved_root not in path.parents or not path.is_file() or path.is_symlink():
            raise CertificationError(f"consumed canonical instrument file missing: {instrument}")
        files.append({
            "instrument": instrument,
            "path": path.relative_to(project_root).as_posix(),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        })
    return {
        "root": resolved_root.relative_to(project_root).as_posix(),
        "materialization": "whole_consumed_instrument_files",
        "files": files,
    }


def _validate_cross_artifact_lineage(
    *, project: Path, request: Mapping[str, Any], identities: Mapping[str, Any],
    paths: Mapping[str, Path], research_config: Mapping[str, Any],
    registry: Mapping[str, Any], dependencies: Mapping[str, Any],
    checkpoint_scope: Mapping[str, Any],
) -> None:
    signal = json.loads(paths["signal_manifest"].read_text(encoding="utf-8"))
    backtest = json.loads(paths["backtest_manifest"].read_text(encoding="utf-8"))
    universe = json.loads(paths["universe_manifest"].read_text(encoding="utf-8"))
    prediction_path = (paths["signal_manifest"].parent / str(signal.get("predictions_file") or "")).resolve()
    if prediction_path != paths["signal_predictions"].resolve():
        raise CertificationError("signal manifest predictions path mismatch")
    if signal.get("window_checkpoint_set_sha256") != checkpoint_scope["checkpoint_set_sha256"]:
        raise CertificationError("signal manifest checkpoint set identity mismatch")
    signal_features = signal.get("feature_list_contract") or {}
    for name in ("feature_list_id", "feature_count", "features_sha256", "feature_list_config_sha256"):
        if signal_features.get(name) != registry.get(name):
            raise CertificationError(f"signal manifest feature contract mismatch: {name}")
    sources = backtest.get("signal_sources")
    if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
        raise CertificationError("backtest must bind exactly one selected signal source")
    source = sources[0]
    declared_source = str((request.get("source_manifest") or {}).get("sha256") or "")
    source_backlinks = {
        "research_config": str(research_config.get("source_manifest_hash") or ""),
        "signal_manifest": str(signal.get("source_manifest_hash") or ""),
        "backtest_signal_source": str(source.get("source_manifest_hash") or ""),
    }
    if declared_source:
        missing = sorted(name for name, value in source_backlinks.items() if not value)
        if missing:
            raise CertificationError(
                f"source manifest backlinks missing: {','.join(missing)}"
            )
        if any(value != declared_source for value in source_backlinks.values()):
            raise CertificationError("source manifest backlinks mismatch")
    elif len({value for value in source_backlinks.values() if value}) > 1:
        raise CertificationError("source manifest backlinks mismatch")
    expected_signal = {
        "manifest_sha256": identities["signal_manifest"]["sha256"],
        "predictions_sha256": identities["signal_predictions"]["sha256"],
        "signal_id": signal.get("signal_id"),
        "signal_run_id": signal.get("signal_run_id"),
        "source_manifest_hash": signal.get("source_manifest_hash"),
    }
    if any(source.get(name) != value for name, value in expected_signal.items()):
        raise CertificationError("backtest signal source lineage mismatch")
    if (
        backtest.get("signal_id") != signal.get("signal_id")
        or backtest.get("signal_run_id") != signal.get("signal_run_id")
    ):
        raise CertificationError("backtest selected signal identity mismatch")
    pit_universe = backtest.get("pit_execution_universe") or {}
    expected_universe_id = universe.get("universe_id")
    if (
        pit_universe.get("manifest_sha256") != identities["universe_manifest"]["sha256"]
        or pit_universe.get("membership_sha256") != identities["universe_membership"]["sha256"]
        or pit_universe.get("universe_id") != expected_universe_id
        or pit_universe.get("artifact") != expected_universe_id
    ):
        raise CertificationError("backtest PIT universe lineage mismatch")
    generator_params = next((
        item.get("params") or {} for item in research_config.get("generators", [])
        if isinstance(item, dict) and (item.get("params") or {}).get("feature_list_id") == registry["feature_list_id"]
    ), {})
    feature_lineage = signal.get("feature_source_lineage") or {}
    sidecars = (
        ("holder_num", "shareholder_holdernumber", "shareholder_holder_path", "shareholder_holder_sha256"),
        ("top10_holder_ratio", "shareholder_top10", "shareholder_top10_path", "shareholder_top10_sha256"),
    )
    dependency_datasets = {
        str(dependency.get("dataset") or "")
        for feature in dependencies.get("features", [])
        for dependency in feature.get("dependencies", [])
        if isinstance(feature, Mapping) and isinstance(dependency, Mapping)
    }
    required_sidecars = {
        lineage_name for lineage_name, dataset, _path_key, _sha_key in sidecars
        if dataset in dependency_datasets
    }
    if generator_params or feature_lineage or required_sidecars:
        for lineage_name, _dataset, path_key, sha_key in sidecars:
            if required_sidecars and lineage_name not in required_sidecars:
                continue
            lineage = feature_lineage.get(lineage_name)
            config_path = generator_params.get(path_key)
            config_sha = generator_params.get(sha_key)
            if not isinstance(lineage, dict) or not config_path or not config_sha:
                raise CertificationError(f"shareholder sidecar lineage missing: {lineage_name}")
            config_artifact = _verify_identity(
                project, {"path": config_path, "sha256": config_sha}, f"{lineage_name} config sidecar",
            )
            signal_artifact = _verify_identity(project, lineage, f"{lineage_name} signal sidecar")
            if config_artifact != signal_artifact or config_sha != lineage.get("sha256"):
                raise CertificationError(f"shareholder sidecar lineage mismatch: {lineage_name}")


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.is_symlink():
            raise CertificationError(f"output path contains symlink: {current}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def certify_pit_baseline(
    *, request_path: str | Path, audit_db: str | Path, output_root: str | Path,
    evidence_run_ids: Sequence[str] = (), mutation_run_ids: Sequence[str] = (),
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    project = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    request_file = _safe_path(project, request_path)
    request = _load_yaml(request_file)
    if request.get("schema_version") != "pit_baseline_request_v1":
        raise CertificationError("unsupported baseline request schema")
    baseline_id = str(request.get("baseline_id") or "")
    if not baseline_id or not all(ch.isalnum() or ch in "_.-" for ch in baseline_id):
        raise CertificationError("unsafe baseline_id")
    identities, paths = _artifact_identities(project, request)
    dependency_path = _safe_path(project, request["feature_dependencies_path"])
    registry, dependencies = validate_feature_dependencies(
        str(request["feature_list_id"]), dependency_path
    )
    research_config = _load_yaml(paths["research_config"])
    checkpoint_root = (project / request["checkpoints"]["path"]).resolve()
    if project not in checkpoint_root.parents or not checkpoint_root.is_dir():
        raise CertificationError("checkpoint root missing or outside project")
    checkpoint_scope = load_checkpoint_scope(
        checkpoint_root=checkpoint_root,
        request=request["checkpoints"],
        research_config=research_config,
    )
    _validate_cross_artifact_lineage(
        project=project, request=request, identities=identities, paths=paths,
        research_config=research_config, registry=registry, dependencies=dependencies,
        checkpoint_scope=checkpoint_scope,
    )
    feature_input = request["feature_input_scope"]
    if _normal_date(feature_input["date_start"]) != _normal_date(checkpoint_scope["train_date_start"]):
        raise CertificationError("feature input start is not bound to checkpoint train_start")
    if _normal_date(feature_input["date_end"]) != _normal_date(checkpoint_scope["predict_date_end"]):
        raise CertificationError("feature input end is not bound to checkpoint predict_end")
    max_lookback = int(dependencies["lookback_contract"]["input_warmup_calendar_days"])
    spans, interval_boundary = load_universe_spans(
        paths["universe_membership"],
        feature_start=feature_input["date_start"],
        feature_end=feature_input["date_end"],
        max_lookback_days=max_lookback,
    )
    evidence = read_selected_evidence(
        Path(audit_db), sorted(set(evidence_run_ids)), sorted(set(mutation_run_ids))
    )
    scopes, dependency_features = _scope_rows(dependencies, spans)
    consumed_instruments = sorted({str(span["instrument"]) for span in spans})
    coverage, coverage_proofs = _coverage_for_scopes(
        scopes, evidence, str(request["scope_key"]), audit_db=Path(audit_db),
        consumed_instruments=consumed_instruments,
    )
    exceptions: list[dict[str, Any]] = []
    if not evidence_run_ids:
        exceptions.append(_exception("NO_EVIDENCE_RUNS_SELECTED", "BLOCKING", registry["features"], {}))
    if evidence["missing_evidence_run_ids"]:
        exceptions.append(_exception("EVIDENCE_RUN_MISSING", "BLOCKING", registry["features"], evidence["missing_evidence_run_ids"]))
    if evidence["missing_mutation_run_ids"]:
        exceptions.append(_exception("MUTATION_RUN_MISSING", "BLOCKING", registry["features"], evidence["missing_mutation_run_ids"]))
    source_manifest = request.get("source_manifest") or {}
    if source_manifest.get("sha256") and not source_manifest.get("path"):
        exceptions.append(_exception("UNRESOLVED_SOURCE_MANIFEST", "BLOCKING", registry["features"], source_manifest))
    elif source_manifest.get("path"):
        verified_source_manifest = _verify_identity(project, source_manifest, "source_manifest")
        identities["source_manifest"] = {
            "path": str(source_manifest["path"]),
            "sha256": sha256_file(verified_source_manifest),
        }
    for item in dependencies["features"]:
        for dependency in item["dependencies"]:
            for code in dependency.get("blocker_codes", []):
                location = {
                    "source": dependency["source"], "dataset": dependency["dataset"],
                    "endpoint": dependency["endpoint"],
                }
                exceptions.append(_exception(
                    str(code), "BLOCKING", [item["feature"]],
                    {**location, "pit_status": dependency["pit_status"]}, **location,
                ))
    missing_keys = {
        (row["source"], row["dataset"], row["endpoint"], row["field"])
        for row in coverage if row["status"] != "COVERED"
    }
    for key in sorted(missing_keys):
        exceptions.append(_exception(
            "FEATURE_DEPENDENCY_EVIDENCE_GAP", "BLOCKING", dependency_features.get(key, []),
            {"source": key[0], "dataset": key[1], "endpoint": key[2], "field": key[3]},
            source=key[0], dataset=key[1], endpoint=key[2], field=key[3],
        ))
    out_of_scope: list[str] = []
    mutation_rows = [
        row for row in evidence["tables"]["canonical_mutations"]
        if row.get("mutation_type") != "noop"
    ]
    mutation_scope_index = _build_mutation_scope_index(scopes)
    for mutation in mutation_rows:
        candidate_indices, ambiguous = _mutation_candidate_indices(
            mutation, mutation_scope_index,
        )
        candidate_results = [
            (index, classify_mutation_intersection(mutation, scopes[index]))
            for index in candidate_indices
        ]
        intersecting = [index for index, result in candidate_results if result == "INTERSECTS"]
        if ambiguous or any(result == "UNKNOWN" for _index, result in candidate_results):
            status = "UNKNOWN"
        elif not intersecting:
            status = "DISJOINT"
        elif all(
            any(
                _proof_accounts_mutation(proof, mutation)
                for proof in coverage_proofs.get(index, [])
            )
            for index in intersecting
        ):
            status = "ACCOUNTED"
        else:
            status = "INTERSECTS"
        if status == "DISJOINT":
            out_of_scope.append(str(mutation["mutation_id"]))
        elif status != "ACCOUNTED":
            affected = sorted({
                feature
                for index, result in candidate_results
                if result in {"INTERSECTS", "UNKNOWN"}
                for scope in (scopes[index],)
                for feature in dependency_features.get((scope["source"], scope["dataset"], scope["endpoint"], scope["field"]), [])
            })
            if ambiguous:
                affected = sorted(registry["features"])
            exceptions.append(_exception(
                "CANONICAL_MUTATION_INTERSECTS" if status == "INTERSECTS" else "CANONICAL_MUTATION_SCOPE_UNKNOWN",
                "REAUDIT", affected, {"mutation_id": mutation["mutation_id"], "run_id": mutation["run_id"]},
                source=mutation.get("source"), dataset=mutation.get("dataset"),
                endpoint=mutation.get("endpoint"), instrument=mutation.get("symbol"),
                date_start=mutation.get("date_start"), date_end=mutation.get("date_end"),
                mutation_run_id=mutation.get("run_id"), mutation_id=mutation.get("mutation_id"),
            ))
        mutation_fields = mutation.get("fields")
        displayed_fields = (
            ",".join(sorted(str(normalize_field(item)) for item in mutation_fields))
            if isinstance(mutation_fields, (list, tuple, set)) else None
        )
        coverage.append({
            "source": mutation.get("source"), "dataset": mutation.get("dataset"),
            "endpoint": mutation.get("endpoint"),
            "field": displayed_fields,
            "instrument": mutation.get("symbol"), "date_start": mutation.get("date_start"),
            "date_end": mutation.get("date_end"), "scope_kind": "canonical_mutation",
            "evidence_run_id": mutation.get("run_id"), "receipt_id": mutation.get("fetch_receipt_id"),
            "mutation_id": mutation.get("mutation_id"), "status": status,
            "reason_code": f"MUTATION_{status}",
        })
    has_blocker = any(item["severity"] == "BLOCKING" for item in exceptions)
    has_reaudit = any(item["severity"] == "REAUDIT" for item in exceptions)
    canonical_materialization: dict[str, Any] | None = None
    if not has_blocker and not has_reaudit:
        try:
            canonical_materialization = _canonical_materialization_identity(
                project, paths["backtest_manifest"], consumed_instruments,
            )
        except CertificationError as exc:
            exceptions.append(_exception(
                "CANONICAL_MATERIALIZATION_UNBOUND", "BLOCKING", registry["features"],
                {"error": str(exc)},
            ))
            has_blocker = True
    baseline_status = "BLOCKED" if has_blocker else ("REAUDIT_REQUIRED" if has_reaudit else "CERTIFIED")
    dependency_sha = sha256_file(dependency_path)
    try:
        request_relative = request_file.relative_to(project).as_posix()
        dependency_relative = dependency_path.relative_to(project).as_posix()
        audit_db_relative = Path(audit_db).resolve().relative_to(project).as_posix()
    except ValueError as exc:
        raise CertificationError("certification inputs must remain inside project root") from exc
    audit_db_path = Path(audit_db).resolve()
    data_root = audit_db_path.parent.parent if audit_db_path.parent.name == "audit" else audit_db_path.parent
    try:
        data_root_relative = data_root.relative_to(project).as_posix() or "."
        audit_root_relative = audit_db_path.parent.relative_to(project).as_posix() or "."
    except ValueError as exc:
        raise CertificationError("audit data root must remain inside project root") from exc
    source_contracts: list[dict[str, str]] = []
    for spec in (request.get("portable_datapack") or {}).get("source_contracts", []):
        if not isinstance(spec, Mapping):
            raise CertificationError("portable_datapack source_contracts must be identity mappings")
        path = _verify_identity(project, spec, "portable_datapack source contract")
        source_contracts.append({
            "path": path.relative_to(project).as_posix(),
            "sha256": sha256_file(path),
        })
    identity_payload = {
        "schema_version": SCHEMA_VERSION,
        "baseline_id": baseline_id,
        "request_sha256": sha256_file(request_file),
        "request": {"path": request_relative, "sha256": sha256_file(request_file)},
        "identities": identities,
        "feature_list_config_sha256": registry["feature_list_config_sha256"],
        "features_sha256": registry["features_sha256"],
        "feature_dependencies_sha256": dependency_sha,
        "feature_dependencies": {"path": dependency_relative, "sha256": dependency_sha},
        "source_contracts": source_contracts,
        "audit_db_relative": audit_db_relative,
        "audit_root_relative": audit_root_relative,
        "evidence_data_root_relative": data_root_relative,
        "canonical_materialization": canonical_materialization,
        "checkpoint_set_sha256": checkpoint_scope["checkpoint_set_sha256"],
        "selected_evidence_run_ids": sorted(set(evidence_run_ids)),
        "selected_mutation_run_ids": sorted(set(mutation_run_ids)),
        "evidence_query_sha256": evidence["evidence_query_sha256"],
        "full_mutation_ledger_sha256": evidence["full_mutation_ledger_sha256"],
    }
    audit_id = _sha256_bytes(_canonical_bytes(identity_payload))
    interval_summaries = []
    max_semantic_sessions = int(
        dependencies["lookback_contract"]["runtime_max_lookback_trading_sessions"]
    )
    for key, features in sorted(dependency_features.items()):
        interval_summaries.append({
            "source": key[0], "dataset": key[1], "endpoint": key[2], "field": key[3],
            "affected_features": sorted(set(features)), "instrument_span_count": len(spans),
            "max_semantic_lookback_trading_sessions": max_semantic_sessions,
            **interval_boundary,
        })
    audit_scope = {
        **identity_payload,
        "audit_id": audit_id,
        "baseline_status": baseline_status,
        "feature_list_id": registry["feature_list_id"],
        "ordered_features": registry["features"],
        "feature_count": registry["feature_count"],
        "feature_dependency_version": dependencies.get("dependency_version"),
        "checkpoint_scope": checkpoint_scope,
        "universe": {
            "manifest_sha256": identities["universe_manifest"]["sha256"],
            "membership_sha256": identities["universe_membership"]["sha256"],
            "registry_sha256": identities["universe_registry"]["sha256"],
            "instrument_span_count": len(spans),
        },
        "interval_scopes": interval_summaries,
        "selected_runs": {
            "evidence": sorted(set(evidence_run_ids)),
            "mutation": sorted(set(mutation_run_ids)),
        },
        "out_of_scope_mutation_ids": sorted(out_of_scope),
        "source_manifest": source_manifest,
    }
    root = Path(output_root)
    _reject_symlink_components(root.absolute())
    root.mkdir(parents=True, exist_ok=True)
    baseline_root = root / baseline_id
    if baseline_root.is_symlink():
        raise CertificationError("baseline output root is a symlink")
    baseline_root.mkdir(exist_ok=True)
    target = baseline_root / audit_id
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"certification output already exists: {target}")
    staging = Path(tempfile.mkdtemp(prefix=f".{audit_id}.", dir=baseline_root))
    try:
        scope_path = staging / "audit_scope.json"
        coverage_path = staging / "coverage.parquet"
        exceptions_path = staging / "exceptions.parquet"
        evidence_path = staging / "evidence_snapshot.json"
        receipt_path = staging / "audit_receipt.json"
        _write_json(scope_path, audit_scope)
        pd.DataFrame(coverage, columns=COVERAGE_COLUMNS).to_parquet(coverage_path, index=False)
        pd.DataFrame(exceptions, columns=EXCEPTION_COLUMNS).to_parquet(exceptions_path, index=False)
        _write_json(evidence_path, {
            "schema_version": EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
            "audit_db_sha256_at_query": evidence["audit_db_sha256"],
            "evidence_query_sha256": evidence["evidence_query_sha256"],
            "selected_evidence_run_ids": sorted(set(evidence_run_ids)),
            "selected_mutation_run_ids": sorted(set(mutation_run_ids)),
            "full_mutation_ledger_sha256": evidence["full_mutation_ledger_sha256"],
            "tables": evidence["tables"],
        })
        try:
            git_revision = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=project, check=True,
                capture_output=True, text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            git_revision = None
        artifact_hashes = {
            "audit_scope.json": sha256_file(scope_path),
            "coverage.parquet": sha256_file(coverage_path),
            "exceptions.parquet": sha256_file(exceptions_path),
            "evidence_snapshot.json": sha256_file(evidence_path),
        }
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "audit_id": audit_id,
            "baseline_id": baseline_id,
            "baseline_status": baseline_status,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "git_revision": git_revision,
            "input_identities": identity_payload,
            "selected_evidence_run_ids": sorted(set(evidence_run_ids)),
            "selected_mutation_run_ids": sorted(set(mutation_run_ids)),
            "evidence_query_sha256": evidence["evidence_query_sha256"],
            "artifacts": artifact_hashes,
        }
        _write_json(receipt_path, receipt)
        if any(sha256_file(staging / name) != digest for name, digest in artifact_hashes.items()):
            raise CertificationError("staged certification artifact hash mismatch")
        for staged_path in (scope_path, coverage_path, exceptions_path, evidence_path, receipt_path):
            staged_fd = os.open(staged_path, os.O_RDONLY)
            try:
                os.fsync(staged_fd)
            finally:
                os.close(staged_fd)
        staging_fd = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(staging_fd)
        finally:
            os.close(staging_fd)
        directory_fd = os.open(baseline_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            fcntl.flock(directory_fd, fcntl.LOCK_EX)
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"certification output already exists: {target}")
            os.rename(staging, target)
            staging = None
            os.fsync(directory_fd)
        finally:
            fcntl.flock(directory_fd, fcntl.LOCK_UN)
            os.close(directory_fd)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    receipt_path = target / "audit_receipt.json"
    return {
        "status": baseline_status, "audit_id": audit_id,
        "output_dir": str(target), "receipt_path": str(receipt_path),
        "exception_count": len(exceptions), "coverage_row_count": len(coverage),
    }
