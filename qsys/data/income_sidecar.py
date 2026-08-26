"""Audited immutable income PIT sidecar materialization and validation.

This module is deliberately income-specific.  It consumes one already trusted
SourceAudit terminal run offline; it neither calls Tushare nor generalises a
sidecar platform.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FINANCIAL_AVAILABILITY_RULE,
    select_first_available_financial_rows,
)
from qsys.data.source_audit import REQUIRED_TERMINAL_GATES, stable_scope_hash


INCOME_SIDECAR_SCHEMA = "audited_income_pit_sidecar_v1"
INCOME_SIDECAR_TRANSFORM = "income_first_available_projection_v1"
INCOME_SIDECAR_FILENAME = "income.parquet"
INCOME_SIDECAR_MANIFEST_FILENAME = "manifest.json"
_REQUIRED_INCOME_COLUMNS = {
    "ts_code",
    "ann_date",
    "f_ann_date",
    "end_date",
    "report_type",
    "comp_type",
    "end_type",
    "update_flag",
    "n_income",
    "revenue",
    "oper_cost",
}
_TRUSTED_INCOME_FIELDS = {
    "ann_date", "end_date", "report_type", "n_income", "revenue", "oper_cost",
}
_SHA256_CHARS = frozenset("0123456789abcdef")


class IncomeSidecarError(RuntimeError):
    """Income sidecar evidence or immutable artifact validation failed."""


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise IncomeSidecarError(f"cannot hash income sidecar evidence: {path}") from exc
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and set(text).issubset(_SHA256_CHARS)


def _normalize_date(value: Any, *, field: str) -> str:
    text = str(value or "").strip().replace("-", "")
    parsed = pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        raise IncomeSidecarError(f"invalid {field}: {value!r}")
    return pd.Timestamp(parsed).strftime("%Y%m%d")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        + "\n"
    ).encode("utf-8")


def _resolve_terminal_receipt(path: str | Path, *, source_run_id: str) -> tuple[Path, Path]:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise IncomeSidecarError("income terminal receipt must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise IncomeSidecarError(f"income terminal receipt does not exist: {candidate}") from exc
    if (
        not resolved.is_file()
        or resolved.name != "receipt.json"
        or len(resolved.parents) < 4
        or resolved.parent.name != source_run_id
        or resolved.parents[1].name != "source_runs"
        or resolved.parents[2].name != "audit"
    ):
        raise IncomeSidecarError("income terminal receipt is outside canonical audit layout")
    return resolved, resolved.parents[3]


def _db_receipt_identity(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "receipt_id": row["receipt_id"],
        "run_id": row["run_id"],
        "source": row["source"],
        "endpoint": row["endpoint"],
        "contract_version": row["contract_version"],
        "status": row["status"],
        "requested_scope": json.loads(row["requested_scope_json"]),
        "returned_rows": row["returned_rows"],
        "response_hash": row["response_hash"],
        "response_columns": json.loads(row["response_columns_json"]),
        "response_date_min": row["response_date_min"],
        "response_date_max": row["response_date_max"],
        "payload_kind": row["payload_kind"],
        "payload_path": row["payload_path"],
        "payload_sha256": row["payload_sha256"],
    }


def _terminal_receipt_identity(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "receipt_id", "run_id", "source", "endpoint", "contract_version",
            "status", "requested_scope", "returned_rows", "response_hash",
            "response_columns", "response_date_min", "response_date_max",
            "payload_kind", "payload_path", "payload_sha256",
        )
    }


def _projection_totals(stats_rows: list[dict[str, Any]]) -> dict[str, int]:
    fields = (
        "raw_rows",
        "selected_first_available_rows",
        "projected_rows",
        "excluded_future_rows",
        "excluded_later_revision_rows",
        "excluded_non_primary_report_type_rows",
        "collapsed_equivalent_branch_rows",
        "missing_end_type_fallback_keys",
        "non_consumed_branch_exception_count",
        "revision_timeline_unproven_excluded_keys",
        "revision_timeline_unproven_excluded_rows",
    )
    return {
        field: sum(int(row.get(field) or 0) for row in stats_rows)
        for field in fields
    }


def _validate_income_scope(
    scope: Mapping[str, Any],
    *,
    scope_key: str,
    range_start: str,
    range_end: str,
    availability_cutoff: str,
) -> str:
    symbols = scope.get("symbols")
    if (
        scope.get("scope_key") != scope_key
        or scope.get("universe") != scope_key
        or scope.get("date_start") != range_start
        or scope.get("date_end") != range_end
        or scope.get("availability_cutoff") != availability_cutoff
        or scope.get("query_axis") != "announcement_date_query_axis"
        or scope.get("request_variant") != FINANCIAL_AVAILABILITY_CONTRACT
        or type(scope.get("symbol_count")) is not int
        or scope.get("symbol_count") != 1
        or not isinstance(symbols, list)
        or len(symbols) != 1
        or not _is_sha256(scope.get("request_sha256"))
        or not _is_sha256(scope.get("checkpoint_key"))
    ):
        raise IncomeSidecarError("income receipt is not the exact current-contract history scope")
    symbol = str(symbols[0]).strip()
    if not symbol or scope.get("symbols_sha256") != stable_scope_hash([symbol]):
        raise IncomeSidecarError("income receipt symbol identity is invalid")
    return symbol


def _load_income_payload(
    fetch: Mapping[str, Any],
    *,
    data_root: Path,
    symbol: str,
) -> pd.DataFrame:
    returned_rows = fetch.get("returned_rows")
    if type(returned_rows) is not int or returned_rows < 0:
        raise IncomeSidecarError("income receipt returned_rows is invalid")
    status = fetch.get("status")
    if status == "empty":
        if (
            returned_rows != 0
            or fetch.get("payload_path") is not None
            or fetch.get("payload_sha256") is not None
        ):
            raise IncomeSidecarError("empty income receipt contains a payload")
        return pd.DataFrame(columns=sorted(_REQUIRED_INCOME_COLUMNS))
    if status != "success" or fetch.get("payload_kind") != "raw_supplier":
        raise IncomeSidecarError("income receipt is not success/empty raw supplier evidence")
    payload_text = fetch.get("payload_path")
    payload_sha256 = str(fetch.get("payload_sha256") or "").lower()
    relative = Path(str(payload_text)) if payload_text else None
    if relative is None or relative.is_absolute() or not _is_sha256(payload_sha256):
        raise IncomeSidecarError("income receipt payload identity is invalid")
    payload_path = (data_root / relative).resolve()
    evidence_root = (data_root / "raw" / "evidence" / "tushare" / "income").resolve()
    if (
        evidence_root not in payload_path.parents
        or payload_path.parent.parent != evidence_root
        or payload_path.suffix != ".parquet"
        or not payload_path.is_file()
    ):
        raise IncomeSidecarError("income payload is outside canonical evidence layout")
    if _sha256_file(payload_path) != payload_sha256:
        raise IncomeSidecarError("income payload sha256 mismatch")
    try:
        frame = pd.read_parquet(payload_path)
    except Exception as exc:
        raise IncomeSidecarError(f"cannot read income payload: {payload_path}") from exc
    if len(frame) != returned_rows or not _REQUIRED_INCOME_COLUMNS.issubset(frame.columns):
        raise IncomeSidecarError("income payload schema or row count is invalid")
    if frame.empty or not frame["ts_code"].astype(str).eq(symbol).all():
        raise IncomeSidecarError("income payload symbol escaped requested scope")
    return frame


def materialize_audited_income_sidecar(
    *,
    terminal_receipt_path: str | Path,
    source_run_id: str,
    scope_key: str,
    range_start: str,
    range_end: str,
    availability_cutoff: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Materialize one immutable income PIT sidecar from trusted raw evidence."""

    source_run_id = str(source_run_id).strip()
    scope_key = str(scope_key).strip()
    if not source_run_id or not scope_key:
        raise IncomeSidecarError("source_run_id and scope_key are required")
    range_start = _normalize_date(range_start, field="range_start")
    range_end = _normalize_date(range_end, field="range_end")
    availability_cutoff = _normalize_date(
        availability_cutoff, field="availability_cutoff"
    )
    if range_start > range_end or availability_cutoff != range_end:
        raise IncomeSidecarError(
            "income sidecar requires range_start <= range_end == availability_cutoff"
        )
    receipt_path, data_root = _resolve_terminal_receipt(
        terminal_receipt_path, source_run_id=source_run_id,
    )
    receipt_bytes = receipt_path.read_bytes()
    terminal_sha256 = _sha256_bytes(receipt_bytes)
    try:
        terminal = json.loads(receipt_bytes)
    except json.JSONDecodeError as exc:
        raise IncomeSidecarError("income terminal receipt is invalid JSON") from exc
    gates = terminal.get("terminal_gates") if isinstance(terminal, dict) else None
    if (
        not isinstance(terminal, dict)
        or terminal.get("schema_version") != 1
        or terminal.get("run_id") != source_run_id
        or terminal.get("trust_state") != "trusted"
        or not isinstance(gates, dict)
        or set(gates) != REQUIRED_TERMINAL_GATES
        or any(gates[name] is not True for name in REQUIRED_TERMINAL_GATES)
    ):
        raise IncomeSidecarError("income terminal receipt is not trusted with all gates true")
    fetches = terminal.get("fetch_receipts")
    links = terminal.get("field_receipt_links")
    if not isinstance(fetches, list) or not isinstance(links, list):
        raise IncomeSidecarError("income terminal receipt evidence sections are invalid")

    db_path = data_root / "audit" / "audit.db"
    try:
        connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
    except (OSError, sqlite3.Error) as exc:
        raise IncomeSidecarError("cannot open source audit database read-only") from exc
    try:
        watermark_rows = connection.execute(
            """SELECT field_name,scope_key,range_start,range_end,trusted_through
               FROM trusted_watermarks
               WHERE source=? AND run_id=? AND terminal_receipt_sha256=?""",
            ("tushare", source_run_id, terminal_sha256),
        ).fetchall()
        by_field = {str(row["field_name"]): row for row in watermark_rows}
        if not _TRUSTED_INCOME_FIELDS.issubset(by_field):
            raise IncomeSidecarError("income terminal watermark backlink is incomplete")
        for field in sorted(_TRUSTED_INCOME_FIELDS):
            row = by_field[field]
            if (
                row["scope_key"] != scope_key
                or str(row["range_start"]) > range_start
                or str(row["range_end"]) < range_end
                or str(row["trusted_through"]) < range_end
            ):
                raise IncomeSidecarError(
                    f"income terminal watermark does not cover requested scope: {field}"
                )

        income_fetches = [
            fetch for fetch in fetches
            if isinstance(fetch, dict) and fetch.get("endpoint") == "income"
        ]
        if not income_fetches:
            raise IncomeSidecarError("terminal receipt contains no income shards")
        linked_fields: dict[str, set[str]] = {}
        for link in links:
            if not isinstance(link, dict) or link.get("dataset") != "income_sidecar":
                continue
            linked_fields.setdefault(str(link.get("receipt_id") or ""), set()).add(
                str(link.get("field_name") or "")
            )

        projected_frames: list[pd.DataFrame] = []
        projection_rows: list[dict[str, Any]] = []
        receipt_lineage: list[dict[str, Any]] = []
        symbols: list[str] = []
        seen_symbols: set[str] = set()
        for fetch in income_fetches:
            if (
                fetch.get("run_id") != source_run_id
                or fetch.get("source") != "tushare"
                or fetch.get("contract_version") != "1"
                or fetch.get("status") not in {"success", "empty"}
            ):
                raise IncomeSidecarError("income receipt has invalid run/source/status")
            receipt_id = str(fetch.get("receipt_id") or "")
            if not receipt_id:
                raise IncomeSidecarError("income receipt_id is missing")
            scope = fetch.get("requested_scope")
            if not isinstance(scope, dict):
                raise IncomeSidecarError("income receipt requested_scope is invalid")
            symbol = _validate_income_scope(
                scope,
                scope_key=scope_key,
                range_start=range_start,
                range_end=range_end,
                availability_cutoff=availability_cutoff,
            )
            if symbol in seen_symbols:
                raise IncomeSidecarError(f"duplicate income shard for symbol: {symbol}")
            seen_symbols.add(symbol)
            symbols.append(symbol)
            if not _REQUIRED_INCOME_COLUMNS.issubset(linked_fields.get(receipt_id, set())):
                raise IncomeSidecarError(
                    f"income receipt field links are incomplete: {receipt_id}"
                )
            db_row = connection.execute(
                "SELECT * FROM fetch_receipts WHERE run_id=? AND receipt_id=?",
                (source_run_id, receipt_id),
            ).fetchone()
            if db_row is None or _db_receipt_identity(db_row) != _terminal_receipt_identity(fetch):
                raise IncomeSidecarError(
                    f"income terminal receipt does not match audit.db: {receipt_id}"
                )
            raw = _load_income_payload(fetch, data_root=data_root, symbol=symbol)
            projected, stats = select_first_available_financial_rows(
                raw,
                endpoint="income",
                availability_cutoff=availability_cutoff,
            )
            stats_record = {
                "symbol": symbol,
                "receipt_id": receipt_id,
                **{
                    key: value for key, value in stats.items()
                    if key not in {
                        "non_consumed_branch_exceptions",
                        "revision_timeline_unproven_exceptions",
                    }
                },
            }
            projection_rows.append(stats_record)
            receipt_lineage.append({
                "symbol": symbol,
                "receipt_id": receipt_id,
                "status": fetch["status"],
                "returned_rows": int(fetch["returned_rows"]),
                "response_hash": fetch.get("response_hash"),
                "payload_sha256": fetch.get("payload_sha256"),
                "projected_rows": int(stats.get("projected_rows") or 0),
                "revision_timeline_unproven_excluded_rows": int(
                    stats.get("revision_timeline_unproven_excluded_rows") or 0
                ),
            })
            if not projected.empty:
                projected = projected.copy()
                projected["source_run_id"] = source_run_id
                projected["source_receipt_id"] = receipt_id
                projected["source_payload_sha256"] = str(
                    fetch.get("payload_sha256") or ""
                )
                projected_frames.append(projected)
    except sqlite3.Error as exc:
        raise IncomeSidecarError("cannot verify income evidence in audit.db") from exc
    finally:
        connection.close()

    symbols = sorted(symbols)
    if not symbols:
        raise IncomeSidecarError("income sidecar symbol scope is empty")
    sidecar = (
        pd.concat(projected_frames, ignore_index=True)
        if projected_frames else pd.DataFrame()
    )
    if sidecar.empty:
        raise IncomeSidecarError("income sidecar projection contains no certified rows")
    sort_columns = [
        column for column in (
            "ts_code", "end_date", "availability_date", "publication_date",
            "source_receipt_id",
        )
        if column in sidecar.columns
    ]
    sidecar = sidecar.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    if sidecar.duplicated(["ts_code", "end_date"]).any():
        raise IncomeSidecarError("income sidecar has duplicate ts_code/end_date rows")
    if (sidecar["availability_date"].astype(str) > availability_cutoff).any():
        raise IncomeSidecarError("income sidecar projection escaped availability cutoff")

    projection_totals = _projection_totals(projection_rows)
    unproven_symbols = sorted({
        row["symbol"] for row in projection_rows
        if int(row.get("revision_timeline_unproven_excluded_rows") or 0) > 0
    })
    identity = {
        "schema": INCOME_SIDECAR_SCHEMA,
        "transform_contract": INCOME_SIDECAR_TRANSFORM,
        "financial_availability_contract": FINANCIAL_AVAILABILITY_CONTRACT,
        "financial_availability_rule": FINANCIAL_AVAILABILITY_RULE,
        "source": "tushare",
        "endpoint": "income",
        "source_run_id": source_run_id,
        "terminal_receipt_sha256": terminal_sha256,
        "scope_key": scope_key,
        "range_start": range_start,
        "range_end": range_end,
        "availability_cutoff": availability_cutoff,
        "symbol_count": len(symbols),
        "symbols_sha256": stable_scope_hash(symbols),
        "source_receipts": receipt_lineage,
    }
    artifact_id = _sha256_bytes(_json_bytes(identity))
    output_root_path = Path(output_root).expanduser().absolute()
    if output_root_path.is_symlink():
        raise IncomeSidecarError("income sidecar output_root must not be a symlink")
    output_root_path.mkdir(parents=True, exist_ok=True)
    target = output_root_path / artifact_id
    lock_path = output_root_path / ".income_sidecar.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        staging = Path(tempfile.mkdtemp(prefix=".income-sidecar-", dir=output_root_path))
        try:
            data_path = staging / INCOME_SIDECAR_FILENAME
            sidecar.to_parquet(data_path, index=False)
            data_sha256 = _sha256_file(data_path)
            manifest = {
                "schema_version": 1,
                "artifact_type": INCOME_SIDECAR_SCHEMA,
                "artifact_id": artifact_id,
                "identity": identity,
                "artifact": {
                    "path": INCOME_SIDECAR_FILENAME,
                    "sha256": data_sha256,
                    "rows": len(sidecar),
                    "columns": list(sidecar.columns),
                },
                "scope": {
                    "scope_key": scope_key,
                    "range_start": range_start,
                    "range_end": range_end,
                    "availability_cutoff": availability_cutoff,
                    "symbol_count": len(symbols),
                    "symbols_sha256": stable_scope_hash(symbols),
                    "symbols": symbols,
                },
                "contracts": {
                    "transform": INCOME_SIDECAR_TRANSFORM,
                    "financial_availability": FINANCIAL_AVAILABILITY_CONTRACT,
                    "availability_rule": FINANCIAL_AVAILABILITY_RULE,
                    "logical_key": [
                        "ts_code", "end_date", "report_type", "comp_type", "end_type",
                    ],
                },
                "source_evidence": {
                    "run_id": source_run_id,
                    "terminal_receipt_path": str(
                        receipt_path.relative_to(data_root).as_posix()
                    ),
                    "terminal_receipt_sha256": terminal_sha256,
                    "terminal_exported_at": terminal.get("exported_at"),
                    "receipts": receipt_lineage,
                },
                "projection": {
                    **projection_totals,
                    "unproven_symbol_count": len(unproven_symbols),
                    "unproven_symbols_sha256": stable_scope_hash(unproven_symbols),
                },
            }
            manifest_path = staging / INCOME_SIDECAR_MANIFEST_FILENAME
            manifest_path.write_bytes(_json_bytes(manifest))
            for path in (data_path, manifest_path):
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            directory_fd = os.open(staging, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

            reused = False
            if target.exists():
                if target.is_symlink() or not target.is_dir():
                    raise IncomeSidecarError("income sidecar identity target is invalid")
                for filename in (
                    INCOME_SIDECAR_FILENAME, INCOME_SIDECAR_MANIFEST_FILENAME,
                ):
                    existing = target / filename
                    staged = staging / filename
                    if not existing.is_file() or existing.read_bytes() != staged.read_bytes():
                        raise IncomeSidecarError(
                            "existing income sidecar identity is not byte-identical"
                        )
                reused = True
            else:
                os.rename(staging, target)
                staging = None
                root_fd = os.open(
                    output_root_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(root_fd)
                finally:
                    os.close(root_fd)
            final_data = target / INCOME_SIDECAR_FILENAME
            final_manifest = target / INCOME_SIDECAR_MANIFEST_FILENAME
            return {
                "status": "reused" if reused else "published",
                "artifact_id": artifact_id,
                "artifact_path": str(final_data),
                "artifact_sha256": data_sha256,
                "manifest_path": str(final_manifest),
                "manifest_sha256": _sha256_file(final_manifest),
                "rows": len(sidecar),
                "symbol_count": len(symbols),
                "projection": manifest["projection"],
            }
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)


def validate_income_sidecar_identity(
    *,
    artifact_path: str | Path,
    artifact_sha256: str,
    manifest_path: str | Path,
    manifest_sha256: str,
    required_start: str | None = None,
    required_end: str | None = None,
    required_symbols: Iterable[str] = (),
) -> dict[str, Any]:
    """Validate an explicit immutable income sidecar identity fail closed."""

    artifact = Path(artifact_path).expanduser().absolute()
    manifest_file = Path(manifest_path).expanduser().absolute()
    for path, label in ((artifact, "artifact"), (manifest_file, "manifest")):
        if path.is_symlink() or not path.is_file():
            raise IncomeSidecarError(
                f"income sidecar {label} must be an existing regular file: {path}"
            )
    declared_artifact_sha = str(artifact_sha256 or "").lower()
    declared_manifest_sha = str(manifest_sha256 or "").lower()
    if not _is_sha256(declared_artifact_sha) or not _is_sha256(declared_manifest_sha):
        raise IncomeSidecarError("income sidecar requires artifact and manifest SHA-256")
    actual_artifact_sha = _sha256_file(artifact)
    actual_manifest_sha = _sha256_file(manifest_file)
    if actual_artifact_sha != declared_artifact_sha:
        raise IncomeSidecarError("income sidecar artifact sha256 mismatch")
    if actual_manifest_sha != declared_manifest_sha:
        raise IncomeSidecarError("income sidecar manifest sha256 mismatch")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IncomeSidecarError("income sidecar manifest is invalid JSON") from exc
    contracts = manifest.get("contracts") if isinstance(manifest, dict) else None
    scope = manifest.get("scope") if isinstance(manifest, dict) else None
    artifact_identity = manifest.get("artifact") if isinstance(manifest, dict) else None
    immutable_identity = manifest.get("identity") if isinstance(manifest, dict) else None
    source_evidence = (
        manifest.get("source_evidence") if isinstance(manifest, dict) else None
    )
    if (
        manifest.get("schema_version") != 1
        or manifest.get("artifact_type") != INCOME_SIDECAR_SCHEMA
        or not _is_sha256(manifest.get("artifact_id"))
        or not isinstance(immutable_identity, dict)
        or manifest.get("artifact_id") != _sha256_bytes(
            _json_bytes(immutable_identity)
        )
        or immutable_identity.get("schema") != INCOME_SIDECAR_SCHEMA
        or immutable_identity.get("transform_contract") != INCOME_SIDECAR_TRANSFORM
        or immutable_identity.get("financial_availability_contract")
        != FINANCIAL_AVAILABILITY_CONTRACT
        or immutable_identity.get("financial_availability_rule")
        != FINANCIAL_AVAILABILITY_RULE
        or immutable_identity.get("source") != "tushare"
        or immutable_identity.get("endpoint") != "income"
        or not isinstance(contracts, dict)
        or contracts.get("transform") != INCOME_SIDECAR_TRANSFORM
        or contracts.get("financial_availability") != FINANCIAL_AVAILABILITY_CONTRACT
        or contracts.get("availability_rule") != FINANCIAL_AVAILABILITY_RULE
        or not isinstance(scope, dict)
        or not isinstance(artifact_identity, dict)
        or artifact_identity.get("path") != artifact.name
        or artifact_identity.get("sha256") != actual_artifact_sha
        or not isinstance(source_evidence, dict)
        or source_evidence.get("run_id") != immutable_identity.get("source_run_id")
        or source_evidence.get("terminal_receipt_sha256")
        != immutable_identity.get("terminal_receipt_sha256")
    ):
        raise IncomeSidecarError("income sidecar manifest contract/identity mismatch")
    scope_symbols = scope.get("symbols")
    if (
        not isinstance(scope_symbols, list)
        or type(scope.get("symbol_count")) is not int
        or scope.get("symbol_count") != len(scope_symbols)
        or scope.get("symbols_sha256") != stable_scope_hash(scope_symbols)
        or scope.get("scope_key") != immutable_identity.get("scope_key")
        or scope.get("range_start") != immutable_identity.get("range_start")
        or scope.get("range_end") != immutable_identity.get("range_end")
        or scope.get("availability_cutoff")
        != immutable_identity.get("availability_cutoff")
        or scope.get("symbol_count") != immutable_identity.get("symbol_count")
        or scope.get("symbols_sha256") != immutable_identity.get("symbols_sha256")
    ):
        raise IncomeSidecarError("income sidecar manifest symbol scope is invalid")
    if required_start is not None:
        start = _normalize_date(required_start, field="required_start")
        if _normalize_date(scope.get("range_start"), field="scope.range_start") > start:
            raise IncomeSidecarError("income sidecar does not cover required start")
    if required_end is not None:
        end = _normalize_date(required_end, field="required_end")
        if _normalize_date(
            scope.get("availability_cutoff"), field="scope.availability_cutoff"
        ) < end:
            raise IncomeSidecarError("income sidecar does not cover required end")
    requested = {str(symbol).strip() for symbol in required_symbols if str(symbol).strip()}
    if not requested.issubset(set(str(symbol) for symbol in scope_symbols)):
        raise IncomeSidecarError("income sidecar does not cover required symbols")
    return {
        "artifact_path": str(artifact),
        "artifact_sha256": actual_artifact_sha,
        "manifest_path": str(manifest_file),
        "manifest_sha256": actual_manifest_sha,
        "manifest": manifest,
    }
