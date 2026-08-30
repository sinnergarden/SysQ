"""Read-only PIT baseline certification over explicit immutable inputs.

This module deliberately does not import the ingestion producer.  It reads the
v1 audit database through SQLite URI ``mode=ro`` and writes only a new,
exclusive certification directory.
"""

from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
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
SOURCE_REVISION_AUDIT_SCHEMA_VERSION = "source_revision_capability_audit_v1"
FINANCIAL_LATEST_KNOWN_CONTRACT = (
    "financial_latest_known_actual_publication_v1"
)
SHAREHOLDER_VINTAGE_CONTRACT = "shareholder_observed_vintage_revision_v1"
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
_NONCANONICAL_SIDECAR_DATASET_ALIASES = frozenset({
    "income", "shareholder", "holder_num", "top10_holder_ratio",
})
REQUIRED_TERMINAL_GATES = frozenset(
    {"fetch", "raw_payloads", "canonical_commit", "qlib_readback", "readiness", "contiguous_range"}
)
MAX_MUTATION_DETAIL_ROWS = 1_000

FINANCIAL_REVISION_VALUE_FIELDS = {
    "income": ("n_income", "revenue", "oper_cost"),
    "balancesheet": (
        "total_assets", "total_hldr_eqy_exc_min_int", "total_cur_assets",
        "total_cur_liab",
    ),
    "cashflow": ("n_cashflow_act",),
    "fina_indicator": ("roe", "grossprofit_margin", "debt_to_assets"),
}
FINANCIAL_STATEMENT_ENDPOINTS = frozenset(
    {"income", "balancesheet", "cashflow"}
)
FINANCIAL_STATEMENT_LOGICAL_KEY = (
    "ts_code", "end_date", "report_type", "comp_type", "end_type",
)
FINANCIAL_INDICATOR_LOGICAL_KEY = ("ts_code", "end_date")
FINANCIAL_EVENT_COLUMNS = [
    "endpoint", "receipt_id", "observed_at", "ts_code", "end_date",
    "logical_key_json", "logical_key_sha256", "publication_date",
    "publication_evidence", "event_kind", "update_flag", "value_json",
    "value_sha256", "capability_status",
]
SOURCE_REVISION_EXCEPTION_COLUMNS = [
    "source", "dataset", "endpoint", "reason_code", "instrument",
    "logical_key_sha256", "publication_date", "row_count", "details_json",
]
SHAREHOLDER_VINTAGE_COLUMNS = [
    "kind", "inst", "ann_date", "end_date", "value", "value_sha256",
    "vintage_id", "source_run_id", "terminal_receipt_sha256", "observed_at",
    "vintage_count", "event_value_revision_count",
    "revision_visibility_status",
]
ASOF_SAMPLE_COLUMNS = [
    "sample_type", "endpoint", "logical_key_sha256", "publication_date",
    "trade_date", "expected_value_sha256", "observed_value_sha256", "status",
]


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
            if "evidence_date_floor" in scope:
                _normal_date(scope["evidence_date_floor"])
    lookback = dependency.get("lookback_contract")
    if not isinstance(lookback, dict):
        raise CertificationError("feature dependency lookback_contract missing")
    for name in ("runtime_max_lookback_trading_sessions", "input_warmup_calendar_days"):
        if not isinstance(lookback.get(name), int) or lookback[name] < 0:
            raise CertificationError(f"invalid lookback contract: {name}")
    semantic_blockers = dependency.get("semantic_blockers", [])
    if not isinstance(semantic_blockers, list):
        raise CertificationError("feature dependency semantic_blockers must be a list")
    for blocker in semantic_blockers:
        if not isinstance(blocker, dict):
            raise CertificationError("semantic blocker must be a mapping")
        if not all(
            isinstance(blocker.get(name), str) and blocker[name].strip()
            for name in ("code", "required_contract", "reason")
        ):
            raise CertificationError("semantic blocker identity is incomplete")
        endpoints = blocker.get("endpoints")
        if (
            not isinstance(endpoints, list)
            or not endpoints
            or any(not isinstance(value, str) or not value.strip() for value in endpoints)
        ):
            raise CertificationError("semantic blocker endpoints must be literals")
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
    if any(str(value) != str(value).strip() for value in scalar_values):
        return [], True
    symbol = str(mutation.get("symbol") or "")
    if (
        symbol != symbol.upper()
        or Path(symbol).name != symbol
        or not all(char.isalnum() or char in "._-" for char in symbol)
    ):
        return [], True
    if str(mutation.get("dataset")) in _NONCANONICAL_SIDECAR_DATASET_ALIASES:
        return [], True
    normalized_fields = {normalize_field(field) for field in fields}
    if None in normalized_fields or any(
        str(field) != str(field).strip() for field in fields
    ):
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


def iter_canonical_mutations(audit_db: Path) -> Iterable[dict[str, Any]]:
    """Stream the append-only mutation ledger without materializing it."""

    uri = f"file:{quote(str(audit_db.resolve()))}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN")
        for row in connection.execute(
            "SELECT * FROM canonical_mutations ORDER BY run_id,mutation_id"
        ):
            yield _decode_row(row)
        connection.rollback()
    finally:
        connection.close()


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
    missing_evidence = sorted(set(evidence_run_ids) - evidence_present)
    return {
        "selected_evidence_run_ids": sorted(set(evidence_run_ids)),
        "selected_mutation_run_ids": sorted(set(mutation_run_ids)),
        "tables": tables,
        "audit_db_sha256": before,
        "missing_evidence_run_ids": missing_evidence,
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
    evidence_date_floors: dict[tuple[str, str, str, str], str] = {}
    for item in dependencies["features"]:
        for dependency in item["dependencies"]:
            for field in dependency["leaf_fields"]:
                key = (
                    str(dependency["source"]), str(dependency["dataset"]),
                    str(dependency["endpoint"]), str(normalize_field(field)),
                )
                keys.setdefault(key, []).append(str(item["feature"]))
                if dependency.get("evidence_date_floor"):
                    value = _normal_date(dependency["evidence_date_floor"])
                    previous = evidence_date_floors.get(key)
                    if previous is not None and previous != value:
                        raise CertificationError(f"conflicting evidence_date_floor for dependency {key}")
                    evidence_date_floors[key] = value
    rows: list[dict[str, Any]] = []
    for (source, dataset, endpoint, field), _features in sorted(keys.items()):
        for span in spans:
            floor = evidence_date_floors.get((source, dataset, endpoint, field))
            if floor is not None and span["date_end"] < floor:
                continue
            rows.append({
                "source": source, "dataset": dataset, "endpoint": endpoint,
                "field": field, "instrument": span["instrument"],
                "date_start": max(span["date_start"], floor or span["date_start"]),
                "date_end": span["date_end"],
                "scope_kind": "feature_dependency",
            })
    return rows, keys


def _trusted_terminal_index(
    *, audit_db: Path, watermark: Mapping[str, Any], terminal_cache: dict[str, Any],
) -> dict[str, Any] | None:
    run_id = str(watermark.get("run_id") or "")
    if (
        not run_id or run_id in {".", ".."}
        or not all(char.isalnum() or char in "_.-" for char in run_id)
    ):
        return None
    expected_terminal_sha = str(watermark.get("terminal_receipt_sha256") or "")
    if len(expected_terminal_sha) != 64:
        return None
    cache = terminal_cache
    cache_key = (run_id, expected_terminal_sha)
    terminal_index = cache.get(cache_key)
    if terminal_index is None:
        audit_root = audit_db.resolve().parent
        terminal_path = audit_root / "source_runs" / run_id / "receipt.json"
        try:
            _reject_symlink_components(terminal_path.absolute())
            resolved = terminal_path.resolve(strict=True)
            if audit_root != resolved and audit_root not in resolved.parents:
                return None
            if sha256_file(resolved) != expected_terminal_sha:
                return None
            terminal = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, CertificationError):
            return None
        gates = terminal.get("terminal_gates")
        terminal_fetches = terminal.get("fetch_receipts")
        terminal_links = terminal.get("field_receipt_links")
        if (
            terminal.get("run_id") != run_id
            or terminal.get("trust_state") != "trusted"
            or not isinstance(gates, dict)
            or set(gates) != REQUIRED_TERMINAL_GATES
            or any(gates[name] is not True for name in REQUIRED_TERMINAL_GATES)
            or not isinstance(terminal_fetches, list)
            or not isinstance(terminal_links, list)
        ):
            return None
        terminal_index = {
            "terminal": terminal,
            "fetches": {
                str(row.get("receipt_id")): row
                for row in terminal_fetches
                if isinstance(row, dict) and row.get("receipt_id")
            },
            "links": {
                (
                    str(row.get("run_id") or ""),
                    str(row.get("dataset") or ""),
                    normalize_field(row.get("field_name")),
                    str(row.get("receipt_id") or ""),
                )
                for row in terminal_links
                if isinstance(row, dict)
            },
        }
        cache[cache_key] = terminal_index
    return terminal_index


def _raw_supplier_receipt_valid(receipt: Mapping[str, Any]) -> bool:
    """Accept either a verified payload or an immutable proof of an empty response."""

    requested = receipt.get("requested_scope")
    if receipt.get("payload_kind") != "raw_supplier" or not isinstance(requested, Mapping):
        return False
    try:
        requested_start = _normal_date(requested["date_start"])
        requested_end = _normal_date(requested["date_end"])
    except (KeyError, CertificationError):
        return False
    if requested_start > requested_end:
        return False
    if receipt.get("status") == "empty":
        return (
            receipt.get("returned_rows") == 0
            and receipt.get("payload_path") is None
            and receipt.get("payload_sha256") is None
            and receipt.get("response_date_min") is None
            and receipt.get("response_date_max") is None
            and len(str(receipt.get("response_hash") or "")) == 64
            and isinstance(receipt.get("response_columns"), list)
        )
    if (
        receipt.get("status") != "success"
        or receipt.get("payload_verified") is not True
        or type(receipt.get("returned_rows")) is not int
        or receipt.get("returned_rows") <= 0
        or len(str(receipt.get("response_hash") or "")) != 64
        or not isinstance(receipt.get("response_columns"), list)
    ):
        return False
    try:
        response_start = _normal_date(receipt["response_date_min"])
        response_end = _normal_date(receipt["response_date_max"])
    except (KeyError, CertificationError):
        return False
    if response_start > response_end:
        return False
    request_variant = requested.get("request_variant")
    query_axis = requested.get("query_axis")
    if request_variant == "history_bak_basic_industry_v1":
        return (
            receipt.get("endpoint") == "bak_basic"
            and query_axis == "all_history"
            and requested.get("availability_cutoff") == requested_end
        )
    if request_variant == "financial_first_available_v1":
        endpoint = receipt.get("endpoint")
        expected_axis = (
            "report_period_query_axis"
            if endpoint == "fina_indicator" else "announcement_date_query_axis"
        )
        return (
            endpoint in {"income", "balancesheet", "cashflow", "fina_indicator"}
            and query_axis in {expected_axis, "exact_announcement_date_query_axis"}
            and requested.get("availability_cutoff") == requested_end
            and (
                query_axis != "exact_announcement_date_query_axis"
                or requested_start == requested_end
            )
        )
    return requested_start <= response_start <= response_end <= requested_end


def _terminal_proof_valid(
    *, audit_db: Path, watermark: Mapping[str, Any], receipt: Mapping[str, Any],
    link: Mapping[str, Any],
    scope_start: str, scope_end: str, consumed_instruments: Sequence[str],
    terminal_cache: dict[str, Any] | None = None,
) -> bool:
    cache = terminal_cache if terminal_cache is not None else {}
    terminal_index = _trusted_terminal_index(
        audit_db=audit_db, watermark=watermark, terminal_cache=cache,
    )
    if terminal_index is None:
        return False
    embedded = terminal_index["fetches"].get(str(receipt.get("receipt_id") or ""))
    if embedded is None:
        return False
    expected_link = (
        str(link.get("run_id") or ""), str(link.get("dataset") or ""),
        normalize_field(link.get("field_name")), str(link.get("receipt_id") or ""),
    )
    if (
        None in expected_link
        or not all(expected_link[index] for index in (0, 1, 3))
        or expected_link not in terminal_index["links"]
    ):
        return False
    for name in (
        "run_id", "source", "endpoint", "status", "payload_kind", "payload_path",
        "payload_sha256", "returned_rows", "response_hash", "response_columns",
        "response_date_min", "response_date_max",
    ):
        if embedded.get(name) != receipt.get(name):
            return False
    requested = receipt.get("requested_scope")
    if embedded.get("requested_scope") != requested or not isinstance(requested, dict):
        return False
    if not _raw_supplier_receipt_valid(receipt):
        return False
    try:
        requested_start = _normal_date(requested["date_start"])
        requested_end = _normal_date(requested["date_end"])
    except (KeyError, CertificationError):
        return False
    if requested_start > requested_end:
        return False
    expected_instruments = sorted({str(value).strip() for value in consumed_instruments if str(value).strip()})
    symbols = requested.get("symbols")
    if symbols is None:
        requested_symbols = expected_instruments
    elif isinstance(symbols, list):
        requested_symbols = sorted({str(value).strip() for value in symbols if str(value).strip()})
    else:
        return False
    return (
        requested_start <= requested_end
        and set(requested_symbols).issubset(expected_instruments)
        and requested.get("symbol_count") == len(requested_symbols)
        and requested.get("symbols_sha256") == stable_scope_hash(requested_symbols)
    )


def _source_receipt_index(
    *, audit_db: Path, run_id: str, receipt_sha256: str,
    source_cache: dict[tuple[str, str], Any],
) -> dict[str, Any] | None:
    cache_key = (run_id, receipt_sha256)
    if cache_key in source_cache:
        return source_cache[cache_key]
    if (
        not run_id or run_id in {".", ".."}
        or not all(char.isalnum() or char in "_.-" for char in run_id)
        or len(receipt_sha256) != 64
    ):
        source_cache[cache_key] = None
        return None
    audit_root = audit_db.resolve().parent
    path = audit_root / "source_runs" / run_id / "receipt.json"
    try:
        _reject_symlink_components(path.absolute())
        resolved = path.resolve(strict=True)
        if audit_root != resolved and audit_root not in resolved.parents:
            raise CertificationError("source receipt escapes audit root")
        if sha256_file(resolved) != receipt_sha256:
            raise CertificationError("source receipt sha256 mismatch")
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError, CertificationError):
        source_cache[cache_key] = None
        return None
    fetches = value.get("fetch_receipts")
    links = value.get("field_receipt_links")
    if (
        value.get("schema_version") != 1 or value.get("run_id") != run_id
        or not isinstance(fetches, list) or not isinstance(links, list)
    ):
        source_cache[cache_key] = None
        return None
    result = {
        "receipt": value,
        "fetches": {
            str(row.get("receipt_id")): row
            for row in fetches
            if isinstance(row, Mapping) and row.get("receipt_id")
        },
        "links": {
            (
                str(row.get("run_id") or ""), str(row.get("dataset") or ""),
                normalize_field(row.get("field_name")),
                str(row.get("receipt_id") or ""),
            )
            for row in links
            if isinstance(row, Mapping)
        },
    }
    source_cache[cache_key] = result
    return result


def _verified_terminal_readback_fields(
    *, audit_db: Path, terminal: Mapping[str, Any],
) -> frozenset[str] | None:
    readbacks = [
        event.get("payload")
        for event in terminal.get("audit_journal") or []
        if isinstance(event, Mapping)
        and event.get("event_type") == "qlib_readback"
        and isinstance(event.get("payload"), Mapping)
    ]
    if not readbacks:
        return None
    readback = readbacks[-1]
    if readback.get("status") != "success" or int(readback.get("mismatch_count", -1)) != 0:
        return None
    artifact_path = readback.get("artifact_path")
    artifact_sha = str(readback.get("artifact_sha256") or "")
    data_root = audit_db.resolve().parent.parent
    try:
        resolved = (data_root / str(artifact_path)).resolve(strict=True)
        if data_root != resolved and data_root not in resolved.parents:
            return None
        if len(artifact_sha) != 64 or sha256_file(resolved) != artifact_sha:
            return None
    except (OSError, ValueError):
        return None
    fields = frozenset(
        field for field in map(normalize_field, readback.get("verified_fields") or [])
        if field is not None
    )
    return fields or None


def _inherited_terminal_proof_valid(
    *, audit_db: Path, watermark: Mapping[str, Any], receipt: Mapping[str, Any],
    link: Mapping[str, Any], scope: Mapping[str, Any],
    consumed_instruments: Sequence[str], terminal_cache: dict[str, Any],
    inherited_cache: dict[Any, Any], required_dataset: str,
) -> bool:
    """Validate raw evidence explicitly inherited by a later trusted terminal."""

    terminal_index = _trusted_terminal_index(
        audit_db=audit_db, watermark=watermark, terminal_cache=terminal_cache,
    )
    if terminal_index is None:
        return False
    terminal = terminal_index["terminal"]
    terminal_key = (
        str(terminal.get("run_id") or ""),
        str(watermark.get("terminal_receipt_sha256") or ""),
    )
    inheritance_index = inherited_cache.get(terminal_key)
    if inheritance_index is None:
        source_cache = inherited_cache.setdefault("source_receipts", {})
        by_receipt: dict[tuple[str, str], list[dict[str, Any]]] = {}
        invalid = False
        for event in terminal.get("audit_journal") or []:
            if not isinstance(event, Mapping) or event.get("event_type") != "history_scope_inherited":
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                invalid = True
                break
            source_run = str(payload.get("source_run_id") or "")
            source_sha = str(payload.get("source_receipt_sha256") or "")
            source_index = _source_receipt_index(
                audit_db=audit_db, run_id=source_run, receipt_sha256=source_sha,
                source_cache=source_cache,
            )
            receipt_ids = payload.get("receipt_ids")
            semantic_fields = frozenset(
                field for field in map(
                    normalize_field, payload.get("canonical_semantic_fields") or []
                ) if field is not None
            )
            if (
                source_index is None or not isinstance(receipt_ids, list)
                or not receipt_ids or len(receipt_ids) != len(set(receipt_ids))
                or payload.get("canonical_semantic_contract") != "bounded_canonical_values_v1"
                or not semantic_fields
            ):
                invalid = True
                break
            source_fetches = source_index["fetches"]
            embedded = [source_fetches.get(str(receipt_id)) for receipt_id in receipt_ids]
            if any(item is None for item in embedded):
                invalid = True
                break
            symbols = sorted({
                str(symbol)
                for item in embedded if isinstance(item, Mapping)
                for symbol in ((item.get("requested_scope") or {}).get("symbols") or [])
            })
            if (
                int(payload.get("symbol_count", -1)) != len(symbols)
                or payload.get("symbols_sha256") != stable_scope_hash(symbols)
            ):
                invalid = True
                break
            normalized = {
                "payload": payload,
                "semantic_fields": semantic_fields,
                "symbols": frozenset(symbols),
                "source_index": source_index,
            }
            for receipt_id in receipt_ids:
                by_receipt.setdefault((source_run, str(receipt_id)), []).append(normalized)
        if invalid:
            by_receipt = {}
        inheritance_index = {
            "by_receipt": by_receipt,
            "readback_fields": _verified_terminal_readback_fields(
                audit_db=audit_db, terminal=terminal,
            ),
        }
        inherited_cache[terminal_key] = inheritance_index
    candidates = inheritance_index["by_receipt"].get(
        (str(link.get("run_id") or ""), str(receipt.get("receipt_id") or "")), []
    )
    field = normalize_field(link.get("field_name"))
    instrument = str(scope.get("instrument") or "")
    expected_instruments = (
        consumed_instruments if isinstance(consumed_instruments, frozenset)
        else frozenset(
            str(value).strip() for value in consumed_instruments if str(value).strip()
        )
    )
    requested = receipt.get("requested_scope")
    requested_symbols = (
        frozenset(str(value) for value in requested.get("symbols") or [])
        if isinstance(requested, Mapping) else frozenset()
    )
    if (
        field is None or not instrument or instrument not in expected_instruments
        or not requested_symbols or not requested_symbols.issubset(expected_instruments)
        or not _raw_supplier_receipt_valid(receipt)
    ):
        return False
    expected_link = (
        str(link.get("run_id") or ""), str(link.get("dataset") or ""), field,
        str(link.get("receipt_id") or ""),
    )
    for candidate in candidates:
        payload = candidate["payload"]
        source_index = candidate["source_index"]
        embedded = source_index["fetches"].get(str(receipt.get("receipt_id") or ""))
        if (
            embedded is None or expected_link not in source_index["links"]
            or instrument not in candidate["symbols"]
            or not requested_symbols.issubset(candidate["symbols"])
            or (
                required_dataset != "income_sidecar"
                and field not in candidate["semantic_fields"]
            )
            or payload.get("source") != receipt.get("source")
            or payload.get("scope_key") != watermark.get("scope_key")
            or not _range_covers(
                payload.get("range_start"), payload.get("range_end"),
                scope.get("date_start"), scope.get("date_end"),
            )
        ):
            continue
        if any(
            embedded.get(name) != receipt.get(name)
            for name in (
                "run_id", "source", "endpoint", "status", "payload_kind",
                "payload_path", "payload_sha256", "returned_rows", "response_hash",
                "response_columns", "response_date_min", "response_date_max",
            )
        ) or embedded.get("requested_scope") != requested:
            continue
        if required_dataset == "canonical_daily" and (
            inheritance_index["readback_fields"] is None
            or field not in inheritance_index["readback_fields"]
        ):
            continue
        return True
    return False


def _proofs_cover_scope(
    proofs: Sequence[Mapping[str, Any]], scope: Mapping[str, Any],
    *, requested_symbols_cache: Mapping[str, frozenset[str] | None] | None = None,
) -> list[dict[str, Any]]:
    """Return the exact receipt shards whose requested intervals cover a scope."""

    instrument = str(scope["instrument"])
    start = _normal_date(scope["date_start"])
    end = _normal_date(scope["date_end"])
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for proof in proofs:
        receipt = proof.get("receipt")
        requested = receipt.get("requested_scope") if isinstance(receipt, Mapping) else None
        if not isinstance(requested, Mapping):
            continue
        symbols = requested.get("symbols")
        receipt_id = str(receipt.get("receipt_id") or "")
        requested_symbols = (
            requested_symbols_cache.get(receipt_id)
            if requested_symbols_cache is not None
            else None if symbols is None
            else frozenset(str(value) for value in symbols)
        )
        if symbols is not None and (
            requested_symbols is None or instrument not in requested_symbols
        ):
            continue
        try:
            left = max(start, _normal_date(requested["date_start"]))
            right = min(end, _normal_date(requested["date_end"]))
        except (KeyError, CertificationError):
            continue
        if left <= right:
            candidates.append((left, right, dict(proof)))
    candidates.sort(key=lambda item: (item[0], item[1]))
    cursor = start
    used: list[dict[str, Any]] = []
    for left, right, proof in candidates:
        if left > cursor:
            break
        if right < cursor:
            continue
        used.append(proof)
        if right >= end:
            return used
        cursor = (datetime.strptime(right, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    return []


def _coverage_for_scopes(
    scopes: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any], scope_key: str,
    *, audit_db: Path, consumed_instruments: Sequence[str],
    income_sidecar_receipts: frozenset[tuple[str, str]] = frozenset(),
    income_sidecar_identity: Mapping[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]]]:
    receipts = {row["receipt_id"]: row for row in evidence["tables"]["fetch_receipts"]}
    links = evidence["tables"]["field_receipt_links"]
    watermarks = evidence["tables"]["trusted_watermarks"]
    scope_start = min(str(scope["date_start"]) for scope in scopes)
    scope_end = max(str(scope["date_end"]) for scope in scopes)
    candidate_type = tuple[
        int, dict[str, Any], dict[str, Any], dict[str, Any], str,
    ]
    universal_candidates: dict[tuple[str, str, str, str], list[candidate_type]] = {}
    broad_candidates: dict[tuple[str, str, str, str], list[candidate_type]] = {}
    instrument_candidates: dict[
        tuple[tuple[str, str, str, str], str], list[candidate_type]
    ] = {}
    requested_symbols_cache: dict[str, frozenset[str] | None] = {}
    consumed_instrument_set = frozenset(str(value) for value in consumed_instruments)
    terminal_cache: dict[str, Any] = {}
    inherited_cache: dict[Any, Any] = {}
    inherited_valid_cache: dict[tuple[str, str, str, str, str, str], bool] = {}
    watermark_index: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for watermark in watermarks:
        field = normalize_field(watermark.get("field_name"))
        if field is not None:
            watermark_index.setdefault(
                (str(watermark.get("source")), field, str(watermark.get("scope_key"))), []
            ).append(watermark)
    income_terminal_watermarks = [
        watermark for watermark in watermarks
        if income_sidecar_identity
        and str(watermark.get("run_id") or "")
        == str(income_sidecar_identity.get("source_run_id") or "")
        and str(watermark.get("terminal_receipt_sha256") or "")
        == str(income_sidecar_identity.get("terminal_receipt_sha256") or "")
        and str(watermark.get("scope_key") or "") == scope_key
        and _range_covers(
            watermark.get("range_start"), watermark.get("trusted_through"),
            income_sidecar_identity.get("range_start"),
            income_sidecar_identity.get("range_end"),
        )
    ]

    def add_candidate(
        key: tuple[str, str, str, str], candidate: candidate_type,
        normalized_symbols: frozenset[str] | None,
    ) -> None:
        if normalized_symbols is None or consumed_instrument_set.issubset(normalized_symbols):
            universal_candidates.setdefault(key, []).append(candidate)
        elif len(normalized_symbols) <= 16:
            for instrument in normalized_symbols:
                instrument_candidates.setdefault((key, instrument), []).append(candidate)
        else:
            broad_candidates.setdefault(key, []).append(candidate)

    for order, link in enumerate(links):
        receipt = receipts.get(link["receipt_id"])
        if receipt is None:
            continue
        field = normalize_field(link["field_name"])
        direct_watermark = next((
            row for row in watermarks
            if row["run_id"] == link["run_id"] and row["source"] == receipt["source"]
            and normalize_field(row["field_name"]) == field and row["scope_key"] == scope_key
        ), None)
        direct_valid = direct_watermark is not None and _terminal_proof_valid(
            audit_db=audit_db, watermark=direct_watermark, receipt=receipt, link=link,
            scope_start=scope_start, scope_end=scope_end,
            consumed_instruments=consumed_instruments,
            terminal_cache=terminal_cache,
        )
        requested = receipt.get("requested_scope") or {}
        symbols = requested.get("symbols")
        normalized_symbols = (
            None if symbols is None else frozenset(str(value) for value in symbols)
        )
        receipt_id = str(receipt.get("receipt_id") or "")
        requested_symbols_cache[receipt_id] = normalized_symbols
        actual_key = (
            str(receipt["source"]), str(link["dataset"]),
            str(receipt["endpoint"]), str(field),
        )
        manifest_income_receipt = (
            str(link.get("run_id")), receipt_id
        ) in income_sidecar_receipts
        keys = [
            actual_key
        ] if actual_key[1] != "income_sidecar" or manifest_income_receipt else []
        if (
            str(link.get("dataset")) == "canonical_daily"
            and str(receipt.get("endpoint")) == "income"
            and manifest_income_receipt
        ):
            keys.append((actual_key[0], "income_sidecar", actual_key[2], actual_key[3]))
        if direct_valid:
            for key in keys:
                add_candidate(
                    key, (order, link, receipt, direct_watermark, "direct"),
                    normalized_symbols,
                )
            continue
        field_inherited_watermarks = list(watermark_index.get(
            (str(receipt.get("source")), str(field), scope_key), []
        ))
        for key in keys:
            inherited_watermarks = list(field_inherited_watermarks)
            if manifest_income_receipt and key[1] == "income_sidecar":
                # The DB watermark key omits dataset, so a later shareholder ann_date
                # can replace the income ann_date row.  The immutable sidecar manifest
                # binds its fields and receipts as one artifact; use any exact backlink
                # to that same certifying terminal for those manifest-listed receipts.
                inherited_watermarks.extend(
                    watermark for watermark in income_terminal_watermarks
                    if watermark not in inherited_watermarks
                )
            for inherited_watermark in inherited_watermarks:
                proof_key = (
                    str(inherited_watermark.get("run_id")), str(link.get("run_id")),
                    receipt_id, str(link.get("dataset")), str(field), key[1],
                )
                valid = inherited_valid_cache.get(proof_key)
                if valid is None:
                    try:
                        probe_start = max(scope_start, _normal_date(requested["date_start"]))
                        probe_end = min(scope_end, _normal_date(requested["date_end"]))
                    except (KeyError, CertificationError):
                        probe_start, probe_end = "1", "0"
                    valid = (
                        probe_start <= probe_end and bool(normalized_symbols)
                        and _inherited_terminal_proof_valid(
                            audit_db=audit_db, watermark=inherited_watermark,
                            receipt=receipt, link=link,
                            scope={
                                "instrument": min(normalized_symbols) if normalized_symbols else "",
                                "date_start": probe_start,
                                "date_end": probe_end,
                            },
                            consumed_instruments=consumed_instrument_set,
                            terminal_cache=terminal_cache, inherited_cache=inherited_cache,
                            required_dataset=key[1],
                        )
                    )
                    inherited_valid_cache[proof_key] = valid
                if valid:
                    add_candidate(
                        key, (order, link, receipt, inherited_watermark, "inherited"),
                        normalized_symbols,
                    )
    rows: list[dict[str, Any]] = []
    proofs: dict[int, list[dict[str, Any]]] = {}
    universal_coverage_cache: dict[
        tuple[tuple[str, str, str, str], str, str], list[dict[str, Any]]
    ] = {}
    instrument_candidate_keys = {index_key[0] for index_key in instrument_candidates}
    for scope_index, scope in enumerate(scopes):
        key = (scope["source"], scope["dataset"], scope["endpoint"], scope["field"])
        chosen = None
        valid_proofs: list[dict[str, Any]] = []
        candidates = sorted(
            universal_candidates.get(key, [])
            + broad_candidates.get(key, [])
            + instrument_candidates.get((key, str(scope["instrument"])), []),
            key=lambda item: item[0],
        )
        cacheable = (
            bool(universal_candidates.get(key))
            and not broad_candidates.get(key)
            and key not in instrument_candidate_keys
        )
        cache_key = (
            key, str(scope["date_start"]), str(scope["date_end"]),
        )
        covering = universal_coverage_cache.get(cache_key) if cacheable else None
        if covering is None:
            for _order, link, receipt, watermark, _proof_mode in candidates:
                if (
                    receipt.get("status") in {"success", "empty"}
                    and _range_covers(
                        watermark.get("range_start"), watermark.get("trusted_through"),
                        scope.get("date_start"), scope.get("date_end"),
                    )
                    and watermark.get("terminal_receipt_sha256")
                ):
                    valid_proofs.append(
                        {"link": link, "receipt": receipt, "watermark": watermark}
                    )
            covering = _proofs_cover_scope(
                valid_proofs, scope,
                requested_symbols_cache=requested_symbols_cache,
            )
            if cacheable:
                universal_coverage_cache[cache_key] = covering
        if covering:
            proofs[scope_index] = covering
            chosen = (covering[0]["link"], covering[0]["receipt"])
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
    *, required_sidecar_identity: Mapping[str, Any] | None = None,
) -> bool:
    watermark = proof.get("watermark")
    if not isinstance(watermark, Mapping) or not _range_covers(
        watermark.get("range_start"), watermark.get("trusted_through"),
        mutation.get("date_start"), mutation.get("date_end"),
    ):
        return False
    if required_sidecar_identity is not None and (
        str(watermark.get("run_id") or "")
        != str(required_sidecar_identity.get("source_run_id") or "")
        or str(watermark.get("terminal_receipt_sha256") or "")
        != str(required_sidecar_identity.get("terminal_receipt_sha256") or "")
    ):
        return False
    watermark_time = _aware_utc(watermark.get("updated_at"))
    mutation_time = _aware_utc(mutation.get("ingested_at"))
    return (
        watermark_time is not None
        and mutation_time is not None
        and watermark_time >= mutation_time
    )


def _sidecar_identity_for_scope(
    scope: Mapping[str, Any], consumed_sidecars: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    dataset = str(scope.get("dataset") or "")
    if dataset == "income_sidecar":
        value = consumed_sidecars.get("income")
        return value if isinstance(value, Mapping) else None
    if dataset in {"shareholder_holdernumber", "shareholder_top10"}:
        value = consumed_sidecars.get("shareholder")
        return value if isinstance(value, Mapping) else None
    return None


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
) -> dict[str, Any]:
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
    return {
        "generator_params": dict(generator_params),
        "feature_source_lineage": dict(feature_lineage),
        "dependency_datasets": sorted(dependency_datasets),
    }


def _sidecar_watermark_backlink(
    *, identity: Mapping[str, Any], evidence: Mapping[str, Any],
    expected_scope_key: str,
) -> None:
    run_id = str(identity.get("source_run_id") or "")
    terminal_sha = str(identity.get("terminal_receipt_sha256") or "")
    range_start = _normal_date(identity.get("range_start"))
    range_end = _normal_date(identity.get("range_end"))
    if run_id not in set(evidence.get("selected_evidence_run_ids") or []):
        raise CertificationError("consumed sidecar source run was not selected")
    matching = [
        row for row in (evidence.get("tables") or {}).get("trusted_watermarks") or []
        if isinstance(row, Mapping)
        and row.get("source") == "tushare"
        and str(row.get("run_id") or "") == run_id
        and str(row.get("terminal_receipt_sha256") or "") == terminal_sha
        and str(row.get("scope_key") or "") == expected_scope_key
        and _normal_date(row.get("range_start")) <= range_start
        and _normal_date(row.get("range_end") or row.get("trusted_through")) >= range_end
        and _normal_date(row.get("trusted_through")) >= range_end
    ]
    if not matching:
        raise CertificationError("consumed sidecar terminal watermark backlink missing")


def _request_identity_spec(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CertificationError(f"consumed sidecar identity missing: {label}")
    return value


def _validate_income_consumed_sidecar(
    *, project: Path, spec: Mapping[str, Any], generator_params: Mapping[str, Any],
    feature_lineage: Mapping[str, Any], request_scope_key: str,
    consumed_symbols: Sequence[str], consumed_start: str, consumed_end: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    from qsys.data.income_sidecar import (
        INCOME_SOURCE_MODE_AUDITED,
        IncomeSidecarError,
        validate_income_sidecar_identity,
    )

    artifact_spec = _request_identity_spec(spec.get("artifact"), label="income artifact")
    manifest_spec = _request_identity_spec(spec.get("manifest"), label="income manifest")
    artifact = _verify_identity(project, artifact_spec, "income request sidecar")
    manifest_path = _verify_identity(project, manifest_spec, "income request manifest")
    config_values = {
        "artifact": (
            generator_params.get("income_sidecar_path"),
            generator_params.get("income_sidecar_sha256"),
        ),
        "manifest": (
            generator_params.get("income_sidecar_manifest_path"),
            generator_params.get("income_sidecar_manifest_sha256"),
        ),
    }
    if generator_params.get("income_source_mode") != INCOME_SOURCE_MODE_AUDITED:
        raise CertificationError("certified income sidecar requires audited generator mode")
    for name, (path_value, digest) in config_values.items():
        config_path = _verify_identity(
            project, {"path": path_value, "sha256": digest},
            f"income config {name}",
        )
        expected_path = artifact if name == "artifact" else manifest_path
        expected_sha = artifact_spec["sha256"] if name == "artifact" else manifest_spec["sha256"]
        if config_path != expected_path or str(digest) != str(expected_sha):
            raise CertificationError(f"income request/config identity mismatch: {name}")
    required_history_start = str(spec.get("required_history_start") or "")
    if (
        not required_history_start
        or str(generator_params.get("income_sidecar_required_history_start") or "")
        != required_history_start
    ):
        raise CertificationError("income required_history_start lineage mismatch")
    try:
        validated = validate_income_sidecar_identity(
            artifact_path=artifact,
            artifact_sha256=str(artifact_spec["sha256"]),
            manifest_path=manifest_path,
            manifest_sha256=str(manifest_spec["sha256"]),
            required_start=consumed_start,
            required_end=consumed_end,
            required_history_start=required_history_start,
            required_symbols=consumed_symbols,
        )
    except (IncomeSidecarError, ValueError) as exc:
        raise CertificationError(f"income consumed sidecar invalid: {exc}") from exc
    manifest = validated["manifest"]
    normalized = {
        "kind": "income",
        "artifact": {
            "path": artifact.relative_to(project).as_posix(),
            "sha256": str(artifact_spec["sha256"]),
        },
        "manifest": {
            "path": manifest_path.relative_to(project).as_posix(),
            "sha256": str(manifest_spec["sha256"]),
        },
        "artifact_id": str(manifest["artifact_id"]),
        "source_run_id": str(manifest["source_evidence"]["run_id"]),
        "terminal_receipt_sha256": str(
            manifest["source_evidence"]["terminal_receipt_sha256"]
        ),
        "scope_key": str(manifest["scope"]["scope_key"]),
        "range_start": str(manifest["scope"]["range_start"]),
        "range_end": str(manifest["scope"]["range_end"]),
        "availability_cutoff": str(manifest["scope"]["availability_cutoff"]),
        "required_history_start": str(manifest["scope"]["required_history_start"]),
        "transform_contract": str(manifest["contracts"]["transform"]),
        "financial_availability_contract": str(
            manifest["contracts"]["financial_availability"]
        ),
        "availability_rule": str(manifest["contracts"]["availability_rule"]),
        "symbol_count": int(manifest["scope"]["symbol_count"]),
        "symbols_sha256": str(manifest["scope"]["symbols_sha256"]),
    }
    declared_semantics = {
        key: spec.get(key) for key in normalized
        if key not in {"kind", "artifact", "manifest"}
    }
    expected_semantics = {
        key: value for key, value in normalized.items()
        if key not in {"kind", "artifact", "manifest"}
    }
    if declared_semantics != expected_semantics:
        raise CertificationError("income request/manifest semantic identity mismatch")
    lineage = feature_lineage.get("income_sidecar")
    if not isinstance(lineage, Mapping):
        raise CertificationError("income signal feature_source_lineage missing")
    signal_expected = {
        "path": str(artifact.resolve()),
        "sha256": normalized["artifact"]["sha256"],
        "manifest_path": str(manifest_path.resolve()),
        "manifest_sha256": normalized["manifest"]["sha256"],
        **expected_semantics,
    }
    if dict(lineage) != signal_expected:
        raise CertificationError("income signal sidecar lineage mismatch")
    if normalized["scope_key"] != request_scope_key:
        raise CertificationError("income sidecar scope_key mismatch")
    _sidecar_watermark_backlink(
        identity=normalized, evidence=evidence, expected_scope_key=request_scope_key,
    )
    return normalized


def _validate_shareholder_consumed_sidecar(
    *, project: Path, spec: Mapping[str, Any], generator_params: Mapping[str, Any],
    feature_lineage: Mapping[str, Any], request_scope_key: str,
    consumed_symbols: Sequence[str], consumed_start: str, consumed_end: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    from qsys.ops.shareholder_sync import (
        AUDITED_SNAPSHOT_CONTRACT,
        AUDITED_SNAPSHOT_SCHEMA,
    )

    names = ("holder_num", "top10_holder_ratio", "manifest")
    request_specs = {
        name: _request_identity_spec(spec.get(name), label=f"shareholder {name}")
        for name in names
    }
    paths = {
        name: _verify_identity(project, request_specs[name], f"shareholder request {name}")
        for name in names
    }
    if len({path.parent for path in paths.values()}) != 1:
        raise CertificationError("shareholder artifacts and manifest must share one directory")
    config_keys = {
        "holder_num": ("shareholder_holder_path", "shareholder_holder_sha256"),
        "top10_holder_ratio": ("shareholder_top10_path", "shareholder_top10_sha256"),
        "manifest": ("shareholder_manifest_path", "shareholder_manifest_sha256"),
    }
    for name, (path_key, sha_key) in config_keys.items():
        config_path = _verify_identity(
            project,
            {"path": generator_params.get(path_key), "sha256": generator_params.get(sha_key)},
            f"shareholder config {name}",
        )
        if (
            config_path != paths[name]
            or str(generator_params.get(sha_key) or "") != str(request_specs[name]["sha256"])
        ):
            raise CertificationError(f"shareholder request/config identity mismatch: {name}")
    try:
        manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CertificationError("shareholder consumed manifest is invalid JSON") from exc
    identity = manifest.get("identity") if isinstance(manifest, dict) else None
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    scope = manifest.get("scope") if isinstance(manifest, dict) else None
    source = manifest.get("source_evidence") if isinstance(manifest, dict) else None
    if (
        manifest.get("schema_version") != 2
        or manifest.get("artifact_type") != AUDITED_SNAPSHOT_SCHEMA
        or not isinstance(identity, Mapping)
        or identity.get("schema") != AUDITED_SNAPSHOT_SCHEMA
        or identity.get("contract") != AUDITED_SNAPSHOT_CONTRACT
        or manifest.get("artifact_id") != _sha256_bytes(
            json.dumps(
                identity, indent=2, sort_keys=True, ensure_ascii=False, default=str,
            ).encode("utf-8") + b"\n"
        )
        or not isinstance(artifacts, Mapping)
        or not isinstance(scope, Mapping)
        or not isinstance(source, Mapping)
    ):
        raise CertificationError("shareholder manifest contract/identity mismatch")
    for name in ("holder_num", "top10_holder_ratio"):
        artifact = artifacts.get(name)
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("path") != paths[name].name
            or artifact.get("sha256") != request_specs[name]["sha256"]
        ):
            raise CertificationError(f"shareholder manifest artifact mismatch: {name}")
    required_symbols = sorted(set(consumed_symbols))
    scope_symbols = sorted({str(value) for value in scope.get("symbols") or []})
    if (
        not set(required_symbols).issubset(scope_symbols)
        or scope.get("symbol_count") != len(scope_symbols)
        or scope.get("symbols_sha256") != stable_scope_hash(scope_symbols)
        or any(
            scope.get(key) != identity.get(key)
            for key in (
                "scope_key", "range_start", "range_end", "symbol_count", "symbols_sha256",
            )
        )
        or _normal_date(scope.get("range_start")) > consumed_start
        or _normal_date(scope.get("range_end")) < consumed_end
        or source.get("run_id") != identity.get("source_run_id")
        or source.get("terminal_receipt_sha256")
        != identity.get("terminal_receipt_sha256")
    ):
        raise CertificationError("shareholder manifest consumed scope mismatch")
    normalized = {
        "kind": "shareholder",
        **{
            name: {
                "path": paths[name].relative_to(project).as_posix(),
                "sha256": str(request_specs[name]["sha256"]),
            }
            for name in names
        },
        "artifact_id": str(manifest["artifact_id"]),
        "source_run_id": str(source["run_id"]),
        "terminal_receipt_sha256": str(source["terminal_receipt_sha256"]),
        "scope_key": str(scope["scope_key"]),
        "range_start": str(scope["range_start"]),
        "range_end": str(scope["range_end"]),
        "symbol_count": int(scope["symbol_count"]),
        "symbols_sha256": str(scope["symbols_sha256"]),
        "transform_contract": AUDITED_SNAPSHOT_CONTRACT,
    }
    declared_semantics = {
        key: spec.get(key) for key in normalized
        if key not in {"kind", *names}
    }
    expected_semantics = {
        key: value for key, value in normalized.items()
        if key not in {"kind", *names}
    }
    if declared_semantics != expected_semantics:
        raise CertificationError("shareholder request/manifest semantic identity mismatch")
    for name in ("holder_num", "top10_holder_ratio"):
        lineage = feature_lineage.get(name)
        if not isinstance(lineage, Mapping) or dict(lineage) != {
            "path": str(paths[name].resolve()),
            "sha256": normalized[name]["sha256"],
        }:
            raise CertificationError(f"shareholder signal sidecar lineage mismatch: {name}")
    manifest_lineage = feature_lineage.get("shareholder_sidecar")
    signal_manifest_expected = {
        "path": str(paths["manifest"].resolve()),
        "sha256": normalized["manifest"]["sha256"],
        **expected_semantics,
    }
    if not isinstance(manifest_lineage, Mapping) or dict(manifest_lineage) != signal_manifest_expected:
        raise CertificationError("shareholder signal manifest lineage mismatch")
    if normalized["scope_key"] != request_scope_key:
        raise CertificationError("shareholder sidecar scope_key mismatch")
    _sidecar_watermark_backlink(
        identity=normalized, evidence=evidence, expected_scope_key=request_scope_key,
    )
    return normalized


def _validate_consumed_sidecars(
    *, project: Path, request: Mapping[str, Any], dependencies: Mapping[str, Any],
    lineage_context: Mapping[str, Any], spans: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    datasets = set(lineage_context.get("dependency_datasets") or [])
    required = set()
    if "income_sidecar" in datasets:
        required.add("income")
    if datasets.intersection({"shareholder_holdernumber", "shareholder_top10"}):
        required.add("shareholder")
    declared = request.get("consumed_sidecars") or {}
    if not isinstance(declared, Mapping) or set(declared) != required:
        raise CertificationError(
            "baseline consumed_sidecars must exactly match consumed dependency datasets"
        )
    if not required:
        return {}
    symbols = sorted({str(span["instrument"]) for span in spans})
    consumed_start = min(_normal_date(span["date_start"]) for span in spans)
    consumed_end = max(_normal_date(span["date_end"]) for span in spans)
    def dataset_start(names: set[str]) -> str:
        starts = [
            max(consumed_start, _normal_date(dependency["evidence_date_floor"]))
            if dependency.get("evidence_date_floor") else consumed_start
            for item in dependencies["features"]
            for dependency in item["dependencies"]
            if str(dependency.get("dataset") or "") in names
        ]
        return min(starts, default=consumed_start)

    common = {
        "project": project,
        "generator_params": lineage_context["generator_params"],
        "feature_lineage": lineage_context["feature_source_lineage"],
        "request_scope_key": str(request["scope_key"]),
        "consumed_symbols": symbols,
        "consumed_end": consumed_end,
        "evidence": evidence,
    }
    result: dict[str, Any] = {}
    if "income" in required:
        result["income"] = _validate_income_consumed_sidecar(
            spec=declared["income"],
            consumed_start=dataset_start({"income_sidecar"}), **common,
        )
    if "shareholder" in required:
        result["shareholder"] = _validate_shareholder_consumed_sidecar(
            spec=declared["shareholder"],
            consumed_start=dataset_start({
                "shareholder_holdernumber", "shareholder_top10",
            }), **common,
        )
    return result


def _income_sidecar_receipt_ids(
    *, project: Path, consumed_sidecars: Mapping[str, Any],
) -> frozenset[tuple[str, str]]:
    income = consumed_sidecars.get("income")
    if not isinstance(income, Mapping):
        return frozenset()
    manifest_spec = income.get("manifest")
    if not isinstance(manifest_spec, Mapping):
        raise CertificationError("validated income sidecar manifest identity missing")
    manifest_path = _verify_identity(project, manifest_spec, "income coverage manifest")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CertificationError("income coverage manifest is invalid JSON") from exc
    source_evidence = manifest.get("source_evidence")
    rows = source_evidence.get("receipts") if isinstance(source_evidence, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise CertificationError("income coverage receipt identities missing")
    identities = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise CertificationError("income coverage receipt identity is invalid")
        run_id = str(row.get("evidence_run_id") or "")
        receipt_id = str(row.get("receipt_id") or "")
        if not run_id or not receipt_id:
            raise CertificationError("income coverage receipt identity is incomplete")
        identities.append((run_id, receipt_id))
    result = frozenset(identities)
    if len(result) != len(identities):
        raise CertificationError("income coverage receipt identities are duplicated")
    return result


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        if current.is_symlink():
            raise CertificationError(f"output path contains symlink: {current}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _portable_scalar(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalized_revision_date(value: Any, *, field: str) -> str | None:
    if value is None or value is pd.NA:
        return None
    text = str(value).strip().replace("-", "")
    if not text or text.lower() == "nan":
        return None
    try:
        return _normal_date(text)
    except CertificationError as exc:
        raise CertificationError(f"invalid {field}: {value!r}") from exc


def _financial_key_json(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    return json.dumps(
        {field: _portable_scalar(row.get(field)) for field in fields},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )


def _financial_value_json(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    return json.dumps(
        {field: _portable_scalar(row.get(field)) for field in fields},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )


def classify_financial_revision_events(
    frame: pd.DataFrame, *, endpoint: str, availability_cutoff: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Classify raw rows without guessing same-publication revision order.

    A complete key's earliest actual-publication group contains an
    ``update_flag=0`` row and every publication date has one consumed value
    tuple. Later, differently dated values are publication events. Conflicting
    values sharing one date, and right-censored keys whose earliest observed
    publication is already ``update_flag=1``, stay fail-closed. The flag is not
    treated as an intra-day or cross-day clock.
    """

    endpoint = str(endpoint)
    if endpoint not in FINANCIAL_REVISION_VALUE_FIELDS:
        raise CertificationError(f"unsupported financial endpoint: {endpoint}")
    is_statement = endpoint in FINANCIAL_STATEMENT_ENDPOINTS
    key_fields = (
        FINANCIAL_STATEMENT_LOGICAL_KEY
        if is_statement else FINANCIAL_INDICATOR_LOGICAL_KEY
    )
    value_fields = FINANCIAL_REVISION_VALUE_FIELDS[endpoint]
    required = set(key_fields) | {"ann_date", "update_flag", *value_fields}
    if is_statement:
        required.add("f_ann_date")
    missing = sorted(required - set(frame.columns))
    if missing:
        raise CertificationError(
            f"{endpoint} raw payload missing revision fields: {missing}"
        )
    cutoff = (
        _normal_date(availability_cutoff)
        if availability_cutoff is not None else None
    )
    empty_stats = {
        "raw_rows": 0, "eligible_primary_rows": 0,
        "excluded_future_rows": 0, "logical_keys": 0,
        "excluded_missing_end_type_keys": 0,
        "excluded_missing_end_type_rows": 0,
        "complete_keys": 0, "blocked_keys": 0, "proven_events": 0,
        "proven_revision_events": 0, "equivalent_rows_collapsed": 0,
        "same_publication_conflict_keys": 0,
        "missing_initial_keys": 0,
    }
    if frame.empty:
        return (
            pd.DataFrame(columns=FINANCIAL_EVENT_COLUMNS),
            pd.DataFrame(columns=SOURCE_REVISION_EXCEPTION_COLUMNS),
            empty_stats,
        )
    work = frame.copy()
    work["ts_code"] = work["ts_code"].astype(str).str.strip().str.upper()
    if work["ts_code"].eq("").any():
        raise CertificationError(f"{endpoint} payload contains empty ts_code")
    work["ann_date"] = work["ann_date"].map(
        lambda value: _normalized_revision_date(value, field="ann_date")
    )
    if work["ann_date"].isna().any():
        raise CertificationError(f"{endpoint} payload contains null ann_date")
    work["end_date"] = work["end_date"].map(
        lambda value: _normalized_revision_date(value, field="end_date")
    )
    if work["end_date"].isna().any():
        raise CertificationError(f"{endpoint} payload contains null end_date")
    if is_statement:
        work["f_ann_date"] = work["f_ann_date"].map(
            lambda value: _normalized_revision_date(value, field="f_ann_date")
        )
        final_dates = work["f_ann_date"].fillna(work["ann_date"])
        work["publication_date"] = work["ann_date"].where(
            work["ann_date"].ge(final_dates), final_dates,
        )
        for field in ("report_type", "comp_type", "end_type"):
            work[field] = (
                work[field].astype("string").str.strip()
                .str.replace(r"\.0$", "", regex=True)
            )
        if work["comp_type"].isna().any():
            raise CertificationError(f"{endpoint} payload contains null comp_type")
        work = work.loc[work["report_type"].eq("1")].copy()
        expected_end_type = {
            "0331": "1", "0630": "2", "0930": "3", "1231": "4",
        }
        expected = work["end_date"].astype(str).str[-4:].map(expected_end_type)
        matched = work["end_type"].eq(expected)
        group_fields = [work["ts_code"], work["end_date"]]
        group_has_match = matched.groupby(group_fields, dropna=False).transform("any")
        missing_end_type = work["end_type"].isna() | work["end_type"].eq("")
        group_all_missing = missing_end_type.groupby(
            group_fields, dropna=False,
        ).transform("all")
        missing_group_rows = expected.notna() & ~group_has_match & group_all_missing
        excluded_missing_end_type_rows = int(missing_group_rows.sum())
        excluded_missing_end_type_keys = int(
            work.loc[missing_group_rows, ["ts_code", "end_date"]]
            .drop_duplicates().shape[0]
        )
        work = work.loc[expected.notna() & matched].copy()
        publication_evidence = "max_ann_date_f_ann_date"
    else:
        excluded_missing_end_type_keys = 0
        excluded_missing_end_type_rows = 0
        work["publication_date"] = work["ann_date"]
        publication_evidence = "ann_date"
    eligible_primary_rows = int(len(work))
    excluded_future_rows = int(
        work["publication_date"].gt(cutoff).sum() if cutoff is not None else 0
    )
    if cutoff is not None:
        work = work.loc[work["publication_date"].le(cutoff)].copy()
    flags = work["update_flag"].astype("string").str.strip().str.replace(
        r"\.0$", "", regex=True,
    )
    if not flags.isin(["0", "1"]).all():
        raise CertificationError(f"{endpoint} payload contains invalid update_flag")
    work["update_flag"] = flags
    events: list[dict[str, Any]] = []
    exceptions: list[dict[str, Any]] = []
    stats = {
        **empty_stats, "raw_rows": int(len(frame)),
        "eligible_primary_rows": eligible_primary_rows,
        "excluded_future_rows": excluded_future_rows,
        "excluded_missing_end_type_keys": excluded_missing_end_type_keys,
        "excluded_missing_end_type_rows": excluded_missing_end_type_rows,
    }
    for key, group in work.groupby(list(key_fields), dropna=False, sort=True):
        stats["logical_keys"] += 1
        key_values = key if isinstance(key, tuple) else (key,)
        key_mapping = dict(zip(key_fields, key_values))
        key_json = _financial_key_json(key_mapping, key_fields)
        key_sha = _sha256_bytes(key_json.encode("utf-8"))
        earliest_publication = str(group["publication_date"].min())
        earliest_group = group.loc[
            group["publication_date"].astype(str).eq(earliest_publication)
        ]
        has_initial = earliest_group["update_flag"].eq("0").any()
        conflict_dates: list[str] = []
        candidates: list[tuple[str, pd.Series, str, int]] = []
        for publication_date, published in group.groupby(
            "publication_date", dropna=False, sort=True,
        ):
            payloads: dict[str, tuple[pd.Series, str, int]] = {}
            for _index, row in published.iterrows():
                value_json = _financial_value_json(row, value_fields)
                value_sha = _sha256_bytes(value_json.encode("utf-8"))
                previous = payloads.get(value_sha)
                if previous is None:
                    payloads[value_sha] = (row, value_json, 1)
                else:
                    payloads[value_sha] = (previous[0], previous[1], previous[2] + 1)
            if len(payloads) != 1:
                conflict_dates.append(str(publication_date))
                exceptions.append({
                    "source": "tushare", "dataset": "canonical_daily",
                    "endpoint": endpoint,
                    "reason_code": "SAME_PUBLICATION_VALUE_CONFLICT",
                    "instrument": str(key_mapping.get("ts_code") or ""),
                    "logical_key_sha256": key_sha,
                    "publication_date": str(publication_date),
                    "row_count": int(len(published)),
                    "details_json": json.dumps({
                        "logical_key": json.loads(key_json),
                        "distinct_value_count": len(payloads),
                        "distinct_values": [
                            json.loads(item[1])
                            for _hash, item in sorted(payloads.items())
                        ],
                        "update_flags": sorted(published["update_flag"].unique()),
                        "contract": FINANCIAL_LATEST_KNOWN_CONTRACT,
                    }, sort_keys=True, ensure_ascii=False),
                })
                continue
            value_sha, (row, value_json, duplicate_count) = next(iter(payloads.items()))
            stats["equivalent_rows_collapsed"] += duplicate_count - 1
            candidates.append((str(publication_date), row, value_json, duplicate_count))
        blocked = bool(conflict_dates) or not has_initial
        if not has_initial:
            stats["missing_initial_keys"] += 1
            exceptions.append({
                "source": "tushare", "dataset": "canonical_daily",
                "endpoint": endpoint,
                "reason_code": "INITIAL_PUBLICATION_VALUE_MISSING",
                "instrument": str(key_mapping.get("ts_code") or ""),
                "logical_key_sha256": key_sha, "publication_date": None,
                "row_count": int(len(group)),
                "details_json": json.dumps({
                    "logical_key": json.loads(key_json),
                    "earliest_publication_date": earliest_publication,
                    "earliest_update_flags": sorted(
                        earliest_group["update_flag"].unique()
                    ),
                    "reason": (
                        "earliest observed publication group lacks update_flag=0; "
                        "a later flag=0 does not prove the missing initial timeline"
                    ),
                    "contract": FINANCIAL_LATEST_KNOWN_CONTRACT,
                }, sort_keys=True, ensure_ascii=False),
            })
        if conflict_dates:
            stats["same_publication_conflict_keys"] += 1
        stats["blocked_keys" if blocked else "complete_keys"] += 1
        previous_sha: str | None = None
        for position, (publication_date, row, value_json, _duplicates) in enumerate(
            sorted(candidates, key=lambda item: item[0])
        ):
            value_sha = _sha256_bytes(value_json.encode("utf-8"))
            if position == 0:
                event_kind = (
                    "INITIAL_PUBLICATION" if has_initial
                    else "RIGHT_CENSORED_FIRST_OBSERVED"
                )
            elif value_sha == previous_sha:
                event_kind = "EQUIVALENT_REPUBLICATION"
            else:
                event_kind = (
                    "REVISION_PUBLICATION" if has_initial
                    else "UNORDERED_REVISION_CANDIDATE"
                )
            if event_kind == "REVISION_PUBLICATION":
                stats["proven_revision_events"] += int(not blocked)
            event = {
                "endpoint": endpoint, "receipt_id": None, "observed_at": None,
                "ts_code": str(key_mapping.get("ts_code") or ""),
                "end_date": str(key_mapping.get("end_date") or ""),
                "logical_key_json": key_json, "logical_key_sha256": key_sha,
                "publication_date": publication_date,
                "publication_evidence": publication_evidence,
                "event_kind": event_kind,
                "update_flag": str(row["update_flag"]),
                "value_json": value_json, "value_sha256": value_sha,
                "capability_status": (
                    "PROVEN_COMPLETE_KEY" if not blocked else "BLOCKED_INCOMPLETE_KEY"
                ),
            }
            events.append(event)
            stats["proven_events"] += int(not blocked)
            previous_sha = value_sha
    return (
        pd.DataFrame(events, columns=FINANCIAL_EVENT_COLUMNS),
        pd.DataFrame(exceptions, columns=SOURCE_REVISION_EXCEPTION_COLUMNS),
        stats,
    )


def resolve_financial_events_as_of(
    events: pd.DataFrame, *, trade_date: str,
) -> pd.DataFrame:
    """Resolve only complete event keys with strict publication-before-trade visibility."""

    cutoff = _normal_date(trade_date)
    if events.empty:
        return events.copy()
    eligible = events.loc[
        events["capability_status"].eq("PROVEN_COMPLETE_KEY")
        & events["publication_date"].astype(str).lt(cutoff)
    ].copy()
    if eligible.empty:
        return eligible
    return eligible.sort_values(
        ["logical_key_sha256", "publication_date"], kind="mergesort",
    ).drop_duplicates("logical_key_sha256", keep="last").reset_index(drop=True)


def _load_revision_terminal(
    *, audit_db: Path, reference: Mapping[str, Any], require_trusted: bool = True,
) -> tuple[dict[str, Any], str]:
    run_id = str(reference.get("run_id") or "")
    expected_sha = str(reference.get("sha256") or "").lower()
    if (
        not run_id or run_id in {".", ".."}
        or not all(char.isalnum() or char in "_.-" for char in run_id)
        or len(expected_sha) != 64
    ):
        raise CertificationError("invalid source revision terminal identity")
    path = audit_db.resolve().parent / "source_runs" / run_id / "receipt.json"
    _reject_symlink_components(path.absolute())
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise CertificationError(f"source revision terminal missing: {run_id}") from exc
    if sha256_file(path) != expected_sha:
        raise CertificationError(f"source revision terminal sha256 mismatch: {run_id}")
    terminal = json.loads(path.read_text(encoding="utf-8"))
    gates = terminal.get("terminal_gates")
    if (
        terminal.get("schema_version") != 1 or terminal.get("run_id") != run_id
        or not isinstance(terminal.get("fetch_receipts"), list)
        or not isinstance(gates, Mapping)
        or set(gates) != REQUIRED_TERMINAL_GATES
    ):
        raise CertificationError(f"invalid source revision terminal: {run_id}")
    if require_trusted and (
        terminal.get("trust_state") not in {"trusted", "trusted_unchanged"}
        or any(gates.get(name) is not True for name in REQUIRED_TERMINAL_GATES)
    ):
        raise CertificationError(f"source revision terminal is not trusted: {run_id}")
    return terminal, expected_sha


def _read_verified_revision_payload(
    *, data_root: Path, fetch: Mapping[str, Any], endpoint: str,
) -> pd.DataFrame:
    if (
        fetch.get("endpoint") != endpoint or fetch.get("status") != "success"
        or fetch.get("payload_kind") != "raw_supplier"
    ):
        raise CertificationError(f"invalid {endpoint} revision payload receipt")
    relative = Path(str(fetch.get("payload_path") or ""))
    expected_sha = str(fetch.get("payload_sha256") or "").lower()
    if relative.is_absolute() or ".." in relative.parts or len(expected_sha) != 64:
        raise CertificationError(f"unsafe {endpoint} revision payload identity")
    path = (data_root / relative).resolve()
    if (data_root != path and data_root not in path.parents) or not path.is_file():
        raise CertificationError(f"{endpoint} revision payload missing")
    if sha256_file(path) != expected_sha:
        raise CertificationError(f"{endpoint} revision payload sha256 mismatch")
    frame = pd.read_parquet(path)
    if int(fetch.get("returned_rows") or -1) != len(frame):
        raise CertificationError(f"{endpoint} revision payload row count mismatch")
    return frame


def _audit_financial_revision_terminal(
    *, terminal: Mapping[str, Any], data_root: Path,
    availability_cutoff: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    event_frames: list[pd.DataFrame] = []
    exception_frames: list[pd.DataFrame] = []
    endpoint_summaries: dict[str, dict[str, int]] = {}
    fetches = terminal["fetch_receipts"]
    for endpoint in FINANCIAL_REVISION_VALUE_FIELDS:
        selected = [
            row for row in fetches
            if isinstance(row, Mapping) and row.get("endpoint") == endpoint
        ]
        if not selected:
            raise CertificationError(f"financial terminal lacks endpoint: {endpoint}")
        summary = {
            "receipt_count": len(selected), "raw_rows": 0,
            "eligible_primary_rows": 0, "excluded_future_rows": 0,
            "excluded_missing_end_type_keys": 0,
            "excluded_missing_end_type_rows": 0,
            "logical_keys": 0,
            "complete_keys": 0, "blocked_keys": 0, "proven_events": 0,
            "proven_revision_events": 0, "equivalent_rows_collapsed": 0,
            "same_publication_conflict_keys": 0, "missing_initial_keys": 0,
            "published_at_nonnull_receipts": 0,
        }
        payload_fetches = []
        for fetch in selected:
            status = str(fetch.get("status") or "")
            if status == "empty":
                if fetch.get("payload_path") is not None:
                    raise CertificationError(
                        f"empty {endpoint} receipt unexpectedly has a payload"
                    )
                continue
            payload_fetches.append(fetch)

        def read_payload(fetch: Mapping[str, Any]) -> pd.DataFrame:
            return _read_verified_revision_payload(
                data_root=data_root, fetch=fetch, endpoint=endpoint,
            )

        workers = max(1, min(8, len(payload_fetches)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            verified_frames = executor.map(read_payload, payload_fetches)
            for fetch, frame in zip(payload_fetches, verified_frames):
                events, exceptions, stats = classify_financial_revision_events(
                    frame, endpoint=endpoint,
                    availability_cutoff=availability_cutoff,
                )
                if not events.empty:
                    events["receipt_id"] = str(fetch.get("receipt_id") or "")
                    events["observed_at"] = str(fetch.get("observed_at") or "")
                    event_frames.append(events)
                if not exceptions.empty:
                    exception_frames.append(exceptions)
                for key, value in stats.items():
                    summary[key] += int(value)
                summary["published_at_nonnull_receipts"] += int(
                    fetch.get("published_at") is not None
                )
        endpoint_summaries[endpoint] = summary
    events = (
        pd.concat(event_frames, ignore_index=True)
        if event_frames else pd.DataFrame(columns=FINANCIAL_EVENT_COLUMNS)
    )
    exceptions = (
        pd.concat(exception_frames, ignore_index=True)
        if exception_frames
        else pd.DataFrame(columns=SOURCE_REVISION_EXCEPTION_COLUMNS)
    )
    return events, exceptions, {
        "contract": FINANCIAL_LATEST_KNOWN_CONTRACT,
        "terminal_run_id": terminal["run_id"],
        "terminal_range_end": terminal.get("range_end"),
        "r3_feature_date_end": availability_cutoff,
        "endpoint_summaries": endpoint_summaries,
        "candidate_event_count": int(len(events)),
        "proven_event_count": sum(
            item["proven_events"] for item in endpoint_summaries.values()
        ),
        "proven_revision_event_count": sum(
            item["proven_revision_events"] for item in endpoint_summaries.values()
        ),
        "exception_count": int(len(exceptions)),
        "complete_key_count": sum(
            item["complete_keys"] for item in endpoint_summaries.values()
        ),
        "blocked_key_count": sum(
            item["blocked_keys"] for item in endpoint_summaries.values()
        ),
    }


def _audit_shareholder_vintages(
    *, project: Path, audit_db: Path, specs: Sequence[Mapping[str, Any]],
    availability_cutoff: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    cutoff = _normal_date(availability_cutoff)
    frames: list[pd.DataFrame] = []
    vintage_ids: set[str] = set()
    for index, spec in enumerate(specs):
        if not isinstance(spec, Mapping):
            raise CertificationError("shareholder vintage spec must be a mapping")
        terminal_ref = spec.get("terminal")
        if not isinstance(terminal_ref, Mapping):
            raise CertificationError("shareholder vintage terminal is missing")
        terminal, terminal_sha = _load_revision_terminal(
            audit_db=audit_db, reference=terminal_ref,
        )
        manifest_spec = spec.get("manifest")
        holder_spec = spec.get("holder_num")
        top10_spec = spec.get("top10_holder_ratio")
        if not all(isinstance(item, Mapping) for item in (
            manifest_spec, holder_spec, top10_spec,
        )):
            raise CertificationError("shareholder vintage artifact identities are missing")
        manifest_path = _verify_identity(project, manifest_spec, "shareholder manifest")
        holder_path = _verify_identity(project, holder_spec, "shareholder holder_num")
        top10_path = _verify_identity(
            project, top10_spec, "shareholder top10_holder_ratio",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        identity = manifest.get("identity") or {}
        artifacts = manifest.get("artifacts") or {}
        if (
            manifest.get("artifact_type") != "audited_shareholder_pit_sidecars_v2"
            or identity.get("source_run_id") != terminal["run_id"]
            or identity.get("terminal_receipt_sha256") != terminal_sha
            or (artifacts.get("holder_num") or {}).get("sha256")
            != sha256_file(holder_path)
            or (artifacts.get("top10_holder_ratio") or {}).get("sha256")
            != sha256_file(top10_path)
        ):
            raise CertificationError("shareholder vintage manifest backlink mismatch")
        vintage_id = str(manifest.get("artifact_id") or f"vintage-{index}")
        if vintage_id in vintage_ids:
            raise CertificationError("duplicate shareholder vintage identity")
        vintage_ids.add(vintage_id)
        observed_at = str(terminal.get("exported_at") or "")
        for kind, path, value_column in (
            ("holder_num", holder_path, "holder_num"),
            ("top10_holder_ratio", top10_path, "top10_ratio"),
        ):
            frame = pd.read_parquet(path)
            required = {"inst", "ann_date", "end_date", value_column}
            if not required.issubset(frame.columns):
                raise CertificationError(
                    f"shareholder {kind} vintage missing columns: {sorted(required - set(frame.columns))}"
                )
            part = frame[["inst", "ann_date", "end_date", value_column]].copy()
            part = part.rename(columns={value_column: "value"})
            part["kind"] = kind
            part["inst"] = part["inst"].astype(str).str.strip().str.upper()
            part["ann_date"] = part["ann_date"].map(
                lambda value: _normal_date(str(value)[:10].replace("-", ""))
            )
            part["end_date"] = part["end_date"].map(
                lambda value: _normal_date(str(value)[:10].replace("-", ""))
            )
            part = part.loc[part["ann_date"].le(cutoff)].copy()
            part["value"] = pd.to_numeric(part["value"], errors="coerce")
            if part["value"].isna().any():
                raise CertificationError(f"shareholder {kind} vintage contains null values")
            part["value_sha256"] = part["value"].map(
                lambda value: _sha256_bytes(
                    _canonical_bytes(_portable_scalar(value))
                )
            )
            part["vintage_id"] = vintage_id
            part["source_run_id"] = terminal["run_id"]
            part["terminal_receipt_sha256"] = terminal_sha
            part["observed_at"] = observed_at
            frames.append(part)
    vintages = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if vintages.empty:
        raise CertificationError("shareholder revision audit has no vintage rows")
    vintages, distinct = classify_shareholder_vintage_events(vintages)
    exception_rows = []
    for kind, group in distinct.groupby("kind", sort=True):
        exception_rows.append({
            "source": "tushare", "dataset": f"shareholder_{kind}",
            "endpoint": (
                "stk_holdernumber" if kind == "holder_num" else "top10_holders"
            ),
            "reason_code": "SHAREHOLDER_HISTORICAL_REVISION_TIMELINE_UNPROVEN",
            "instrument": None, "logical_key_sha256": None,
            "publication_date": None, "row_count": int(len(group)),
            "details_json": json.dumps({
                "contract": SHAREHOLDER_VINTAGE_CONTRACT,
                "source_vintage_count": len(vintage_ids),
                "actual_publication_timestamp_available": False,
                "reason": (
                    "ann_date has no revision timestamp; observed_at is only a "
                    "conservative upper bound for changes seen between vintages"
                ),
            }, sort_keys=True, ensure_ascii=False),
        })
    exceptions = pd.DataFrame(
        exception_rows, columns=SOURCE_REVISION_EXCEPTION_COLUMNS,
    )
    return (
        vintages[SHAREHOLDER_VINTAGE_COLUMNS].sort_values(
            ["kind", "inst", "ann_date", "end_date", "observed_at"],
            kind="mergesort",
        ).reset_index(drop=True),
        exceptions,
        {
            "contract": SHAREHOLDER_VINTAGE_CONTRACT,
            "r3_feature_date_end": cutoff,
            "source_vintage_count": len(vintage_ids),
            "vintage_row_count": int(len(vintages)),
            "unique_event_key_count": int(len(distinct)),
            "single_vintage_event_key_count": int(
                distinct["vintage_count"].eq(1).sum()
            ),
            "multi_vintage_event_key_count": int(
                distinct["vintage_count"].gt(1).sum()
            ),
            "observed_changed_event_key_count": int(
                distinct["event_value_revision_count"].gt(0).sum()
            ),
            "actual_publication_timestamp_event_key_count": 0,
        },
    )


def classify_shareholder_vintage_events(
    vintages: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Annotate exact shareholder facts without collapsing report periods."""

    key_fields = ["kind", "inst", "ann_date", "end_date"]
    required = {*key_fields, "value_sha256", "vintage_id"}
    missing = sorted(required - set(vintages.columns))
    if missing:
        raise CertificationError(
            f"shareholder vintage events missing fields: {missing}"
        )
    work = vintages.copy()
    if work.duplicated([*key_fields, "vintage_id"]).any():
        raise CertificationError(
            "shareholder vintage contains duplicate exact fact/vintage keys"
        )
    key_fields = ["kind", "inst", "ann_date", "end_date"]
    counts = work.groupby(key_fields, sort=False)["vintage_id"].transform("nunique")
    revisions = work.groupby(key_fields, sort=False)["value_sha256"].transform(
        "nunique"
    ) - 1
    work["vintage_count"] = counts.astype(int)
    work["event_value_revision_count"] = revisions.astype(int)
    work["revision_visibility_status"] = "SINGLE_VINTAGE_REVISION_UNVERIFIED"
    work.loc[counts.gt(1) & revisions.eq(0), "revision_visibility_status"] = (
        "MULTI_VINTAGE_NO_CHANGE_OBSERVED"
    )
    work.loc[counts.gt(1) & revisions.gt(0), "revision_visibility_status"] = (
        "OBSERVED_REVISION_UPPER_BOUND_ONLY"
    )
    distinct = work.drop_duplicates(key_fields)
    return work, distinct


def _load_trade_calendar(
    *, project: Path, spec: Mapping[str, Any], availability_cutoff: str,
) -> tuple[list[str], dict[str, str]]:
    path = _verify_identity(project, spec, "source revision trade calendar")
    cutoff = _normal_date(availability_cutoff)
    dates: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        date = _normal_date(text[:10].replace("-", ""))
        if date <= cutoff:
            dates.append(date)
    dates = sorted(set(dates))
    if not dates:
        raise CertificationError("source revision trade calendar is empty")
    return dates, {
        "path": path.relative_to(project).as_posix(),
        "sha256": sha256_file(path),
    }


def _financial_asof_samples(
    events: pd.DataFrame, *, trade_dates: Sequence[str],
) -> pd.DataFrame:
    samples: list[dict[str, Any]] = []
    complete = events.loc[
        events["capability_status"].eq("PROVEN_COMPLETE_KEY")
    ].copy()
    for endpoint in FINANCIAL_REVISION_VALUE_FIELDS:
        endpoint_frame = complete.loc[complete["endpoint"].eq(endpoint)]
        sampled = 0
        for key, group in endpoint_frame.groupby("logical_key_sha256", sort=True):
            group = group.sort_values("publication_date", kind="mergesort")
            revisions = group.loc[group["event_kind"].eq("REVISION_PUBLICATION")]
            if revisions.empty:
                continue
            revision = revisions.iloc[0]
            publication = str(revision["publication_date"])
            previous = group.loc[
                group["publication_date"].astype(str).lt(publication)
            ].iloc[-1]
            before_candidates = [date for date in trade_dates if date <= publication]
            after_candidates = [date for date in trade_dates if date > publication]
            if not before_candidates or not after_candidates:
                continue
            before_trade = before_candidates[-1]
            after_trade = after_candidates[0]
            before = resolve_financial_events_as_of(
                group, trade_date=before_trade,
            )
            after = resolve_financial_events_as_of(
                group, trade_date=after_trade,
            )
            for sample_type, trade_date, expected, resolved in (
                (
                    "REVISION_NOT_VISIBLE_THROUGH_PUBLICATION_DATE", before_trade,
                    str(previous["value_sha256"]), before,
                ),
                (
                    "REVISION_VISIBLE_ON_FIRST_LATER_TRADE", after_trade,
                    str(revision["value_sha256"]), after,
                ),
            ):
                observed = (
                    str(resolved.iloc[-1]["value_sha256"])
                    if not resolved.empty else None
                )
                samples.append({
                    "sample_type": sample_type, "endpoint": endpoint,
                    "logical_key_sha256": key,
                    "publication_date": publication, "trade_date": trade_date,
                    "expected_value_sha256": expected,
                    "observed_value_sha256": observed,
                    "status": "PASS" if observed == expected else "FAIL",
                })
            sampled += 1
            if sampled >= 1:
                break
    return pd.DataFrame(samples, columns=ASOF_SAMPLE_COLUMNS)


def _validate_r3_source_blockers(
    *, project: Path, spec: Mapping[str, Any], feature_date_end: str,
) -> dict[str, Any]:
    audit_id = str(spec.get("audit_id") or "")
    receipt_spec = spec.get("audit_receipt")
    exceptions_spec = spec.get("exceptions")
    if not audit_id or not all(
        isinstance(item, Mapping) for item in (receipt_spec, exceptions_spec)
    ):
        raise CertificationError("R3 source blocker identity is incomplete")
    receipt_path = _verify_identity(
        project, receipt_spec, "R3 certification receipt",
    )
    exceptions_path = _verify_identity(
        project, exceptions_spec, "R3 certification exceptions",
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if (
        receipt.get("audit_id") != audit_id
        or receipt.get("baseline_status") != "BLOCKED"
        or (receipt.get("artifacts") or {}).get("exceptions.parquet")
        != sha256_file(exceptions_path)
    ):
        raise CertificationError("R3 source blocker backlink mismatch")
    exceptions = pd.read_parquet(exceptions_path)
    required = {"reason_code", "affected_features_json"}
    if not required.issubset(exceptions.columns):
        raise CertificationError("R3 exceptions lack source blocker fields")
    feature_sets: dict[str, set[str]] = {
        "shareholder": set(), "financial": set(),
    }
    reason_counts: dict[str, int] = {}
    for _index, row in exceptions.iterrows():
        reason = str(row["reason_code"])
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if reason == "SHAREHOLDER_REVISION_CAPABILITY_UNVERIFIED":
            target = feature_sets["shareholder"]
        elif reason == "FINANCIAL_LATEST_KNOWN_REVISION_CAPABILITY_UNVERIFIED":
            target = feature_sets["financial"]
        else:
            continue
        values = json.loads(str(row["affected_features_json"]))
        if not isinstance(values, list):
            raise CertificationError("R3 affected_features_json is not a list")
        target.update(str(value) for value in values)
    expected = spec.get("expected") or {}
    actual = {
        "exception_rows": int(len(exceptions)),
        "shareholder_unique_features": len(feature_sets["shareholder"]),
        "financial_unique_features": len(feature_sets["financial"]),
    }
    for key, value in actual.items():
        if int(expected.get(key, -1)) != value:
            raise CertificationError(
                f"R3 source blocker count mismatch for {key}: {value}"
            )
    return {
        "audit_id": audit_id,
        "feature_date_end": _normal_date(feature_date_end),
        "audit_receipt": {
            "path": receipt_path.relative_to(project).as_posix(),
            "sha256": sha256_file(receipt_path),
        },
        "exceptions": {
            "path": exceptions_path.relative_to(project).as_posix(),
            "sha256": sha256_file(exceptions_path),
        },
        **actual,
        "reason_counts": reason_counts,
        "shareholder_features": sorted(feature_sets["shareholder"]),
        "financial_features": sorted(feature_sets["financial"]),
    }


def _latest_shareholder_vintage_values(current: pd.DataFrame) -> pd.DataFrame:
    """Return the latest observed snapshot for diagnostic legacy comparison."""

    key_fields = ["kind", "inst", "ann_date", "end_date"]
    required = {*key_fields, "value", "value_sha256", "vintage_id", "observed_at"}
    missing = sorted(required - set(current.columns))
    if missing:
        raise CertificationError(
            f"current shareholder vintages lack diagnostic fields: {missing}"
        )
    work = current[list(required)].copy()
    if not work.duplicated(key_fields).any():
        return work[key_fields + ["value"]]
    observed = work["observed_at"].fillna("").astype(str).str.strip()
    if observed.eq("").any():
        raise CertificationError(
            "multi-vintage diagnostic comparison requires observed_at"
        )
    work["observed_at"] = observed
    latest_observed = work.groupby(key_fields, sort=False)["observed_at"].transform(
        "max"
    )
    latest = work.loc[work["observed_at"].eq(latest_observed)].copy()
    conflicting = latest.groupby(key_fields, sort=False)["value_sha256"].nunique()
    if conflicting.gt(1).any():
        raise CertificationError(
            "latest shareholder vintages conflict at one observation time"
        )
    latest = latest.sort_values(
        [*key_fields, "observed_at", "vintage_id"], kind="mergesort"
    ).drop_duplicates(key_fields, keep="last")
    return latest[key_fields + ["value"]].reset_index(drop=True)


def _shareholder_legacy_comparator_deltas(
    *, project: Path, current: pd.DataFrame,
    specs: Sequence[Mapping[str, Any]], availability_cutoff: str,
) -> list[dict[str, Any]]:
    cutoff = _normal_date(availability_cutoff)
    key_fields = ["kind", "inst", "ann_date", "end_date"]
    current_keys = _latest_shareholder_vintage_values(current)
    results: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, Mapping):
            raise CertificationError("legacy shareholder comparator must be a mapping")
        name = str(spec.get("name") or "")
        manifest_spec = spec.get("manifest")
        if not name or not isinstance(manifest_spec, Mapping):
            raise CertificationError("legacy shareholder comparator identity missing")
        manifest_path = _verify_identity(
            project, manifest_spec, f"legacy shareholder comparator {name}",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_kind: dict[str, Any] = {}
        for kind, value_column, request_key in (
            ("holder_num", "holder_num", "holder_num"),
            ("top10_holder_ratio", "top10_ratio", "top10_holder_ratio"),
        ):
            artifact_spec = spec.get(request_key)
            if not isinstance(artifact_spec, Mapping):
                raise CertificationError(
                    f"legacy shareholder comparator {name} lacks {request_key}"
                )
            path = _verify_identity(
                project, artifact_spec, f"legacy {name} {request_key}",
            )
            manifest_artifact = (manifest.get("artifacts") or {}).get(request_key) or {}
            if manifest_artifact.get("sha256") != sha256_file(path):
                raise CertificationError(
                    f"legacy shareholder comparator {name} backlink mismatch"
                )
            frame = pd.read_parquet(path)
            required = {"inst", "ann_date", "end_date", value_column}
            if not required.issubset(frame.columns):
                raise CertificationError(
                    f"legacy shareholder comparator {name} lacks exact event fields"
                )
            candidate = frame[list(required)].copy().rename(
                columns={value_column: "candidate_value"}
            )
            candidate["kind"] = kind
            candidate["inst"] = candidate["inst"].astype(str).str.strip().str.upper()
            candidate["ann_date"] = candidate["ann_date"].map(
                lambda value: _normal_date(str(value)[:10].replace("-", ""))
            )
            candidate["end_date"] = candidate["end_date"].map(
                lambda value: _normalized_revision_date(value, field="end_date")
            )
            candidate = candidate.loc[candidate["ann_date"].le(cutoff)].copy()
            missing_end_date_rows = int(candidate["end_date"].isna().sum())
            candidate = candidate.loc[candidate["end_date"].notna()].copy()
            if candidate.duplicated(key_fields).any():
                raise CertificationError(
                    f"legacy shareholder comparator {name} has duplicate exact keys"
                )
            base = current_keys.loc[current_keys["kind"].eq(kind)].rename(
                columns={"value": "current_value"}
            )
            merged = base.merge(candidate, on=key_fields, how="outer", indicator=True)
            overlap = merged.loc[merged["_merge"].eq("both")].copy()
            difference = (overlap["current_value"] - overlap["candidate_value"]).abs()
            tolerance = 1e-9 + 1e-9 * overlap["candidate_value"].abs()
            changed = overlap.loc[difference.gt(tolerance)]
            examples = [
                {
                    **{field: _portable_scalar(row[field]) for field in key_fields},
                    "current_value": _portable_scalar(row["current_value"]),
                    "candidate_value": _portable_scalar(row["candidate_value"]),
                }
                for _index, row in changed.head(3).iterrows()
            ]
            by_kind[kind] = {
                "current_rows": int(len(base)),
                "candidate_rows": int(len(candidate)),
                "excluded_missing_end_date_rows": missing_end_date_rows,
                "overlap_rows": int(len(overlap)),
                "equal_value_rows": int(len(overlap) - len(changed)),
                "changed_value_rows": int(len(changed)),
                "current_only_rows": int(merged["_merge"].eq("left_only").sum()),
                "candidate_only_rows": int(merged["_merge"].eq("right_only").sum()),
                "changed_examples": examples,
            }
        results.append({
            "name": name,
            "evidence_class": str(spec.get("evidence_class") or "diagnostic_only"),
            "non_certifying_reason": str(spec.get("non_certifying_reason") or ""),
            "manifest": {
                "path": manifest_path.relative_to(project).as_posix(),
                "sha256": sha256_file(manifest_path),
            },
            "manifest_contract": (
                (manifest.get("identity") or {}).get("contract")
                or (manifest.get("contracts") or {}).get("transform")
                or manifest.get("snapshot_id")
            ),
            "by_kind": by_kind,
        })
    return results


def _source_revision_report(
    *, status: str, financial: Mapping[str, Any], shareholder: Mapping[str, Any],
    r3: Mapping[str, Any], legacy_comparators: Sequence[Mapping[str, Any]],
    exception_count: int, sample_count: int,
) -> str:
    lines = [
        "# Source Revision Capability Audit", "", f"Status: **{status}**", "",
        "This is a read-only data-certification artifact. It is not a training,",
        "signal, feature-cache, backtest, model, strategy, or accounting input.", "",
        "## Business contracts", "",
        f"- Financial: `{FINANCIAL_LATEST_KNOWN_CONTRACT}`.",
        "  Statements use `max(ann_date, f_ann_date)` and indicators use `ann_date`.",
        "  A value is visible only to trade dates strictly after that actual",
        "  publication date. The earliest date group must contain `update_flag=0`;",
        "  a flag appearing later is not a clock. Same-date conflicting values",
        "  remain blocked; equal duplicates collapse without creating an event.",
        f"- Shareholder: `{SHAREHOLDER_VINTAGE_CONTRACT}`.",
        "  The exact fact key is `(kind, inst, ann_date, end_date, vintage)`.",
        "  `ann_date` proves only the declared date. A terminal snapshot is one",
        "  vintage; `observed_at` can upper-bound a later observed change but cannot",
        "  be promoted to its historical publication time.", "",
        "## R3 scope", "",
        f"- Upstream certification: `{r3['audit_id']}`",
        f"- Feature/prediction date end: `{r3['feature_date_end']}`",
        f"- Upstream blockers: {r3['exception_rows']:,} exception rows,",
        f"  {r3['shareholder_unique_features']:,} unique shareholder features and",
        f"  {r3['financial_unique_features']:,} unique financial features.", "",
        "## Frozen evidence result", "",
        f"- Financial candidate rows: {financial['candidate_event_count']:,}",
        f"- Financial orderable events: {financial['proven_event_count']:,}",
        f"- Financial value revisions: {financial['proven_revision_event_count']:,}",
        f"- Financial complete logical keys: {financial['complete_key_count']:,}",
        f"- Financial blocked logical keys: {financial['blocked_key_count']:,}",
        f"- Shareholder source vintages: {shareholder['source_vintage_count']:,}",
        f"- Shareholder exact event keys: {shareholder['unique_event_key_count']:,}",
        f"- Blocking exception rows: {exception_count:,}",
        f"- Trade-calendar event/as-of samples: {sample_count:,}", "",
        "### Financial endpoint counts", "",
        "| Endpoint | Complete keys | Right-censored keys | Same-date conflict keys | Orderable events | Value revisions |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for endpoint, item in financial["endpoint_summaries"].items():
        lines.append(
            f"| {endpoint} | {item['complete_keys']:,} | "
            f"{item['missing_initial_keys']:,} | "
            f"{item['same_publication_conflict_keys']:,} | "
            f"{item['proven_events']:,} | "
            f"{item['proven_revision_events']:,} |"
        )
    lines.extend([
        "", "### Legacy shareholder comparators", "",
        "Legacy snapshots and alternate projections are diagnostic comparators,",
        "not source vintages. Value deltas can be caused by coverage or transform",
        "semantics and therefore are not supplier-revision evidence.", "",
    ])
    for comparator in legacy_comparators:
        lines.append(f"- `{comparator['name']}`: {comparator['non_certifying_reason']}")
        for kind, item in comparator["by_kind"].items():
            lines.append(
                f"  `{kind}` overlap {item['overlap_rows']:,}; changed values "
                f"{item['changed_value_rows']:,}; current-only "
                f"{item['current_only_rows']:,}; comparator-only "
                f"{item['candidate_only_rows']:,}; excluded missing `end_date` "
                f"{item['excluded_missing_end_date_rows']:,}."
            )
    lines.extend([
        "",
        "## Verdict", "",
        "Frozen bytes support the emitted proven financial event subset and the",
        "shareholder snapshot inventory. They do not prove a complete historical",
        "latest-known revision timeline, so this audit does not close the R3 blocker.",
        "", "## Legal supplementation paths", "",
        "1. Import immutable historical supplier snapshots that pre-date each change",
        "   and preserve their original receipt/observation identity.",
        "2. Bind revision values to official announcement versions. Tushare `anns_d`",
        "   exposes announcement `rec_time` and original PDF URLs; the PDF/version",
        "   parser must prove the exact value tuple before it can close a key.",
        "3. Capture new terminal vintages going forward. This can prove only the",
        "   conservative first-observed bound, never backfill earlier publication time.",
        "4. Otherwise obtain a licensed PIT/versioned financial and shareholder feed.",
        "", "Normal `data_sync` is neither required nor permitted for these paths.", "",
    ])
    return "\n".join(lines)


def _validate_source_revision_count_contract(
    *, request: Mapping[str, Any], financial: Mapping[str, Any],
    shareholder: Mapping[str, Any], sample_count: int,
) -> dict[str, Any]:
    endpoint_counts = {
        endpoint: {
            "logical_keys": item["logical_keys"],
            "complete_keys": item["complete_keys"],
            "blocked_keys": item["blocked_keys"],
            "right_censored_keys": item["missing_initial_keys"],
            "same_publication_conflict_keys": (
                item["same_publication_conflict_keys"]
            ),
            "orderable_events": item["proven_events"],
            "value_revisions": item["proven_revision_events"],
        }
        for endpoint, item in financial["endpoint_summaries"].items()
    }
    observed_financial = {
        "logical_keys": sum(item["logical_keys"] for item in endpoint_counts.values()),
        "complete_keys": financial["complete_key_count"],
        "blocked_keys": financial["blocked_key_count"],
        "right_censored_keys": sum(
            item["right_censored_keys"] for item in endpoint_counts.values()
        ),
        "same_publication_conflict_keys": sum(
            item["same_publication_conflict_keys"]
            for item in endpoint_counts.values()
        ),
        "orderable_events": financial["proven_event_count"],
        "value_revisions": financial["proven_revision_event_count"],
        "excluded_missing_end_type_keys": sum(
            item["excluded_missing_end_type_keys"]
            for item in financial["endpoint_summaries"].values()
        ),
        "excluded_future_rows": sum(
            item["excluded_future_rows"]
            for item in financial["endpoint_summaries"].values()
        ),
    }
    observed_shareholder = {
        "source_vintages": shareholder["source_vintage_count"],
        "exact_event_keys": shareholder["unique_event_key_count"],
        "historical_revision_timeline_proven_keys": (
            shareholder["actual_publication_timestamp_event_key_count"]
        ),
        "asof_samples": sample_count,
    }
    financial_request = request.get("financial") or {}
    shareholder_request = request.get("shareholder") or {}
    expected_financial = financial_request.get("expected_r3_counts")
    expected_shareholder = shareholder_request.get("expected_r3_counts")
    if not all(
        isinstance(item, Mapping)
        for item in (expected_financial, expected_shareholder)
    ):
        raise CertificationError("source revision expected count contract is missing")

    def check(name: str, observed: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
        for key, expected_value in expected.items():
            if key == "by_endpoint":
                continue
            if key not in observed or int(observed[key]) != int(expected_value):
                raise CertificationError(
                    f"source revision count mismatch for {name}.{key}: "
                    f"observed={observed.get(key)!r} expected={expected_value!r}"
                )

    check("financial", observed_financial, expected_financial)
    expected_endpoints = expected_financial.get("by_endpoint")
    if not isinstance(expected_endpoints, Mapping):
        raise CertificationError("financial endpoint count contract is missing")
    if set(expected_endpoints) != set(endpoint_counts):
        raise CertificationError("financial endpoint count contract scope mismatch")
    for endpoint, expected in expected_endpoints.items():
        if not isinstance(expected, Mapping):
            raise CertificationError(f"invalid expected counts for {endpoint}")
        check(f"financial.{endpoint}", endpoint_counts[endpoint], expected)
    check("shareholder", observed_shareholder, expected_shareholder)
    if observed_financial["complete_keys"] + observed_financial["blocked_keys"] != (
        observed_financial["logical_keys"]
    ):
        raise CertificationError("financial logical-key conservation failed")
    return {
        "status": "PASS",
        "financial": observed_financial,
        "financial_by_endpoint": endpoint_counts,
        "shareholder": observed_shareholder,
    }


def audit_source_revision_capabilities(
    *, request_path: str | Path, audit_db: str | Path, output_root: str | Path,
    project_root: str | Path | None = None,
) -> dict[str, Any]:
    """Audit frozen source bytes into a new immutable revision-capability artifact."""

    project = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    request_file = _safe_path(project, request_path)
    request = _load_yaml(request_file)
    if request.get("schema_version") != "source_revision_audit_request_v1":
        raise CertificationError("unsupported source revision audit request schema")
    audit_name = str(request.get("audit_name") or "")
    if not audit_name or not all(char.isalnum() or char in "_.-" for char in audit_name):
        raise CertificationError("unsafe source revision audit_name")
    audit_db_path = Path(audit_db).resolve()
    if not audit_db_path.is_file():
        raise CertificationError("source revision audit database is missing")
    db_before = sha256_file(audit_db_path)
    scope_request = request.get("scope") or {}
    feature_date_end = _normal_date(str(scope_request.get("feature_date_end") or ""))
    r3_spec = request.get("upstream_r3_certification")
    calendar_spec = request.get("trade_calendar")
    source_contract_spec = request.get("source_contract")
    if not all(
        isinstance(item, Mapping)
        for item in (r3_spec, calendar_spec, source_contract_spec)
    ):
        raise CertificationError("source revision scope identities are incomplete")
    r3_context = _validate_r3_source_blockers(
        project=project, spec=r3_spec, feature_date_end=feature_date_end,
    )
    current_r3_specs = request.get("current_r3_outputs")
    if not isinstance(current_r3_specs, Mapping) or not current_r3_specs:
        raise CertificationError("current R3 output identities are missing")
    current_r3_outputs: dict[str, dict[str, str]] = {}
    for name, spec in current_r3_specs.items():
        if not isinstance(spec, Mapping):
            raise CertificationError(f"invalid current R3 output identity: {name}")
        path = _verify_identity(project, spec, f"current R3 output {name}")
        current_r3_outputs[str(name)] = {
            "path": path.relative_to(project).as_posix(),
            "sha256": sha256_file(path),
        }
    trade_dates, trade_calendar_identity = _load_trade_calendar(
        project=project, spec=calendar_spec,
        availability_cutoff=feature_date_end,
    )
    source_contract = _verify_identity(
        project, source_contract_spec, "source revision business contract",
    )
    financial_ref = (request.get("financial") or {}).get("terminal")
    if not isinstance(financial_ref, Mapping):
        raise CertificationError("financial terminal identity is missing")
    financial_terminal, financial_terminal_sha = _load_revision_terminal(
        audit_db=audit_db_path, reference=financial_ref,
    )
    data_root = audit_db_path.parent.parent
    financial_events, financial_exceptions, financial_summary = (
        _audit_financial_revision_terminal(
            terminal=financial_terminal, data_root=data_root,
            availability_cutoff=feature_date_end,
        )
    )
    shareholder_specs = (request.get("shareholder") or {}).get("vintages")
    if not isinstance(shareholder_specs, list) or not shareholder_specs:
        raise CertificationError("shareholder vintages are missing")
    shareholder_vintages, shareholder_exceptions, shareholder_summary = (
        _audit_shareholder_vintages(
            project=project, audit_db=audit_db_path, specs=shareholder_specs,
            availability_cutoff=feature_date_end,
        )
    )
    legacy_specs = (request.get("shareholder") or {}).get("legacy_comparators") or []
    if not isinstance(legacy_specs, list):
        raise CertificationError("legacy shareholder comparators must be a list")
    legacy_deltas = _shareholder_legacy_comparator_deltas(
        project=project, current=shareholder_vintages, specs=legacy_specs,
        availability_cutoff=feature_date_end,
    )
    exceptions = pd.concat(
        [financial_exceptions, shareholder_exceptions], ignore_index=True,
    )
    samples = _financial_asof_samples(
        financial_events, trade_dates=trade_dates,
    )
    if samples.empty or not samples["status"].eq("PASS").all():
        raise CertificationError("source revision as-of sample validation failed")
    count_contract = _validate_source_revision_count_contract(
        request=request, financial=financial_summary,
        shareholder=shareholder_summary, sample_count=len(samples),
    )
    if sha256_file(audit_db_path) != db_before:
        raise CertificationError("audit database changed during source revision audit")
    request_relative = request_file.relative_to(project).as_posix()
    source_contract_relative = source_contract.relative_to(project).as_posix()
    implementation_paths = [
        Path(__file__).resolve(),
        project / "scripts/research/certify_pit_baseline.py",
    ]
    identity = {
        "schema_version": SOURCE_REVISION_AUDIT_SCHEMA_VERSION,
        "audit_name": audit_name,
        "request": {"path": request_relative, "sha256": sha256_file(request_file)},
        "audit_db_sha256": db_before,
        "source_contract": {
            "path": source_contract_relative, "sha256": sha256_file(source_contract),
        },
        "implementation": [
            {
                "path": path.relative_to(project).as_posix(),
                "sha256": sha256_file(path),
            }
            for path in implementation_paths
        ],
        "upstream_r3_certification": r3_context,
        "current_r3_outputs": current_r3_outputs,
        "feature_date_end": feature_date_end,
        "trade_calendar": trade_calendar_identity,
        "financial_terminal": {
            "run_id": financial_terminal["run_id"],
            "sha256": financial_terminal_sha,
        },
        "shareholder_vintages": [
            {
                key: dict(value) if isinstance(value, Mapping) else value
                for key, value in spec.items()
            }
            for spec in shareholder_specs
        ],
        "contracts": {
            "financial": FINANCIAL_LATEST_KNOWN_CONTRACT,
            "shareholder": SHAREHOLDER_VINTAGE_CONTRACT,
        },
    }
    audit_id = _sha256_bytes(_canonical_bytes(identity))
    status = "BLOCKED" if not exceptions.empty else "CERTIFIED"
    scope = {
        **identity, "audit_id": audit_id, "status": status,
        "scope_contract": {
            "feature_date_end": feature_date_end,
            "source_terminal_range_end": financial_terminal.get("range_end"),
            "rule": "audit_feature_scope_and_report_source_terminal_separately",
        },
        "financial_summary": financial_summary,
        "shareholder_summary": shareholder_summary,
        "legacy_comparator_deltas": legacy_deltas,
        "count_contract": count_contract,
        "exception_count": int(len(exceptions)),
        "asof_sample_count": int(len(samples)),
    }
    root = Path(output_root).resolve()
    _reject_symlink_components(root.absolute())
    root.mkdir(parents=True, exist_ok=True)
    audit_root = root / audit_name
    audit_root.mkdir(exist_ok=True)
    target = audit_root / audit_id
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"source revision audit output already exists: {target}")
    staging = Path(tempfile.mkdtemp(prefix=f".{audit_id}.", dir=audit_root))
    try:
        _write_json(staging / "audit_scope.json", scope)
        financial_events.to_parquet(
            staging / "financial_events.parquet", index=False, compression="zstd",
        )
        shareholder_vintages.to_parquet(
            staging / "shareholder_vintages.parquet", index=False, compression="zstd",
        )
        exceptions.to_parquet(
            staging / "exceptions.parquet", index=False, compression="zstd",
        )
        samples.to_parquet(
            staging / "asof_samples.parquet", index=False, compression="zstd",
        )
        _write_json(
            staging / "legacy_comparator_deltas.json", legacy_deltas,
        )
        (staging / "REPORT.md").write_text(
            _source_revision_report(
                status=status, financial=financial_summary,
                shareholder=shareholder_summary,
                r3=r3_context, legacy_comparators=legacy_deltas,
                exception_count=len(exceptions), sample_count=len(samples),
            ),
            encoding="utf-8",
        )
        artifact_names = [
            "audit_scope.json", "financial_events.parquet",
            "shareholder_vintages.parquet", "exceptions.parquet",
            "asof_samples.parquet", "legacy_comparator_deltas.json", "REPORT.md",
        ]
        artifact_hashes = {
            name: sha256_file(staging / name) for name in artifact_names
        }
        _write_json(staging / "audit_receipt.json", {
            "schema_version": SOURCE_REVISION_AUDIT_SCHEMA_VERSION,
            "audit_name": audit_name, "audit_id": audit_id, "status": status,
            "created_at": datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            ),
            "input_identities": identity, "artifacts": artifact_hashes,
        })
        for path in staging.iterdir():
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        staging_descriptor = os.open(
            staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(staging_descriptor)
        finally:
            os.close(staging_descriptor)
        directory_descriptor = os.open(
            audit_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            fcntl.flock(directory_descriptor, fcntl.LOCK_EX)
            if target.exists() or target.is_symlink():
                raise FileExistsError(
                    f"source revision audit output already exists: {target}"
                )
            os.rename(staging, target)
            staging = None
            os.fsync(directory_descriptor)
        finally:
            fcntl.flock(directory_descriptor, fcntl.LOCK_UN)
            os.close(directory_descriptor)
    finally:
        if staging is not None and staging.exists():
            shutil.rmtree(staging)
    db_after = sha256_file(audit_db_path)
    if db_after != db_before:
        raise CertificationError("audit database changed during artifact finalization")
    return {
        "status": status, "audit_id": audit_id, "output_dir": str(target),
        "receipt_path": str(target / "audit_receipt.json"),
        "exception_count": int(len(exceptions)),
        "financial_candidate_event_count": int(len(financial_events)),
        "financial_proven_event_count": int(financial_summary["proven_event_count"]),
        "shareholder_vintage_row_count": int(len(shareholder_vintages)),
        "asof_sample_count": int(len(samples)),
    }


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
    lineage_context = _validate_cross_artifact_lineage(
        project=project, request=request, identities=identities, paths=paths,
        research_config=research_config, registry=registry, dependencies=dependencies,
        checkpoint_scope=checkpoint_scope,
    )
    feature_input = request["feature_input_scope"]
    dependency_floors = [
        _normal_date(scope["evidence_date_floor"])
        for item in dependencies["features"]
        for scope in item["dependencies"]
        if scope.get("evidence_date_floor")
    ]
    if dependency_floors and _normal_date(feature_input["date_start"]) < max(dependency_floors):
        raise CertificationError("feature input starts before dependency evidence_date_floor")
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
    consumed_sidecars = _validate_consumed_sidecars(
        project=project,
        request=request,
        dependencies=dependencies,
        lineage_context=lineage_context,
        spans=spans,
        evidence=evidence,
    )
    income_sidecar_receipts = _income_sidecar_receipt_ids(
        project=project, consumed_sidecars=consumed_sidecars,
    )
    scopes, dependency_features = _scope_rows(dependencies, spans)
    consumed_instruments = sorted({str(span["instrument"]) for span in spans})
    coverage, coverage_proofs = _coverage_for_scopes(
        scopes, evidence, str(request["scope_key"]), audit_db=Path(audit_db),
        consumed_instruments=consumed_instruments,
        income_sidecar_receipts=income_sidecar_receipts,
        income_sidecar_identity=consumed_sidecars.get("income"),
    )
    exceptions: list[dict[str, Any]] = []
    if not evidence_run_ids:
        exceptions.append(_exception("NO_EVIDENCE_RUNS_SELECTED", "BLOCKING", registry["features"], {}))
    if evidence["missing_evidence_run_ids"]:
        exceptions.append(_exception("EVIDENCE_RUN_MISSING", "BLOCKING", registry["features"], evidence["missing_evidence_run_ids"]))
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
    for blocker in dependencies.get("semantic_blockers", []):
        endpoints = set(blocker["endpoints"])
        affected_features = sorted({
            item["feature"]
            for item in dependencies["features"]
            if any(
                dependency["endpoint"] in endpoints
                for dependency in item["dependencies"]
            )
        })
        if affected_features:
            exceptions.append(_exception(
                str(blocker["code"]), "BLOCKING", affected_features,
                {
                    "required_contract": blocker["required_contract"],
                    "endpoints": sorted(endpoints),
                    "reason": blocker["reason"],
                },
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
    out_of_scope_count = 0
    mutation_count = 0
    mutation_detail_count = 0
    mutation_reaudit_count = 0
    mutation_present: set[str] = set()
    mutation_type_counts: dict[str, int] = {}
    mutation_status_counts: dict[str, int] = {}
    mutation_digest = hashlib.sha256()
    mutation_digest.update(b"[")
    first_mutation = True
    mutation_scope_index = _build_mutation_scope_index(scopes)
    mutation_db_before = sha256_file(audit_db)
    if mutation_db_before != evidence["audit_db_sha256"]:
        raise CertificationError("audit database changed before mutation scan")
    for mutation in iter_canonical_mutations(Path(audit_db)):
        if not first_mutation:
            mutation_digest.update(b",")
        mutation_digest.update(_canonical_bytes(mutation))
        first_mutation = False
        mutation_count += 1
        mutation_run_id = str(mutation.get("run_id") or "")
        if mutation_run_id:
            mutation_present.add(mutation_run_id)
        mutation_type = str(mutation.get("mutation_type") or "")
        mutation_type_counts[mutation_type] = mutation_type_counts.get(mutation_type, 0) + 1
        if mutation_type == "noop":
            continue
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
                _proof_accounts_mutation(
                    proof,
                    mutation,
                    required_sidecar_identity=_sidecar_identity_for_scope(
                        scopes[index], consumed_sidecars,
                    ),
                )
                for proof in coverage_proofs.get(index, [])
            )
            for index in intersecting
        ):
            status = "ACCOUNTED"
        else:
            status = "INTERSECTS"
        mutation_status_counts[status] = mutation_status_counts.get(status, 0) + 1
        if status == "DISJOINT":
            out_of_scope_count += 1
            if len(out_of_scope) < MAX_MUTATION_DETAIL_ROWS:
                out_of_scope.append(str(mutation["mutation_id"]))
        elif status != "ACCOUNTED":
            mutation_reaudit_count += 1
            if mutation_reaudit_count <= MAX_MUTATION_DETAIL_ROWS:
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
        if mutation_detail_count < MAX_MUTATION_DETAIL_ROWS:
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
            mutation_detail_count += 1
    mutation_digest.update(b"]")
    if sha256_file(audit_db) != mutation_db_before:
        raise CertificationError("audit database changed during mutation scan")
    missing_mutation_run_ids = sorted(set(mutation_run_ids) - mutation_present)
    evidence["missing_mutation_run_ids"] = missing_mutation_run_ids
    if missing_mutation_run_ids:
        exceptions.append(_exception(
            "MUTATION_RUN_MISSING", "BLOCKING", registry["features"],
            missing_mutation_run_ids,
        ))
    mutation_summary = {
        "count": mutation_count,
        "counts_by_type": dict(sorted(mutation_type_counts.items())),
        "counts_by_scope_status": dict(sorted(mutation_status_counts.items())),
        "run_ids": sorted(mutation_present),
        "detail_limit": MAX_MUTATION_DETAIL_ROWS,
        "detail_count": mutation_detail_count,
        "detail_omitted_count": max(sum(mutation_status_counts.values()) - mutation_detail_count, 0),
        "reaudit_count": mutation_reaudit_count,
        "out_of_scope_count": out_of_scope_count,
    }
    evidence["canonical_mutation_summary"] = mutation_summary
    evidence["full_mutation_ledger_sha256"] = mutation_digest.hexdigest()
    evidence_query_payload = {
        "selected_evidence_run_ids": evidence["selected_evidence_run_ids"],
        "selected_mutation_run_ids": evidence["selected_mutation_run_ids"],
        "full_mutation_ledger_sha256": evidence["full_mutation_ledger_sha256"],
        "canonical_mutation_summary": mutation_summary,
        "tables": evidence["tables"],
    }
    evidence["evidence_query_sha256"] = _sha256_bytes(
        _canonical_bytes(evidence_query_payload)
    )
    has_blocker = any(item["severity"] == "BLOCKING" for item in exceptions)
    has_reaudit = mutation_reaudit_count > 0 or any(
        item["severity"] == "REAUDIT" for item in exceptions
    )
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
        "consumed_sidecars": consumed_sidecars,
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
        "canonical_mutation_summary": mutation_summary,
        "out_of_scope_mutation_ids": sorted(out_of_scope),
        "out_of_scope_mutation_id_count": out_of_scope_count,
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
            "canonical_mutation_summary": mutation_summary,
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
