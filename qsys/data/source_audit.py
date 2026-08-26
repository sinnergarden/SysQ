"""Small, append-only evidence store for canonical source ingestion.

The SQLite database is the source of truth.  Per-run JSON receipts are an
immutable, human-readable export; legacy date-named audit JSON files are not
trusted evidence.
"""
from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
import re
import sqlite3
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


AUDIT_SCHEMA_VERSION = 1
TRUSTED = "trusted"
LEGACY_UNTRUSTED = "legacy_untrusted"
REQUIRED_TERMINAL_GATES = frozenset(
    {"fetch", "raw_payloads", "canonical_commit", "qlib_readback", "readiness", "contiguous_range"}
)
_SECRET_KEY = re.compile(r"(?:token|secret|password|api[_-]?key|authorization)", re.I)
_SECRET_VALUE = re.compile(
    r"(?i)(token|secret|password|api[_-]?key|authorization)(\s*[=:]\s*)([^\s&;,]+)"
)
_RUN_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
WRITER_LOCK_FD_ENV = "QSYS_DATA_WRITER_LOCK_FD"


def validate_run_id(run_id: str) -> str:
    value = str(run_id)
    if not _RUN_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid run_id: {value!r}")
    return value


def resolve_under(root: str | Path, candidate: str | Path) -> Path:
    resolved_root = Path(root).resolve()
    resolved = Path(candidate).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"path escapes root: {candidate}")
    return resolved


class data_writer_lock:
    """Non-blocking single-writer flock for one data root."""

    def __init__(self, data_root: str | Path, *, inherited_fd: int | None = None):
        self.data_root = Path(data_root).resolve()
        self.inherited_fd = inherited_fd
        self.inherited = inherited_fd is not None
        self.handle = None
        self._owns_unlock = False

    @classmethod
    def from_environment(cls, data_root: str | Path) -> "data_writer_lock":
        raw_fd = os.environ.get(WRITER_LOCK_FD_ENV)
        if raw_fd is None:
            return cls(data_root)
        try:
            inherited_fd = int(raw_fd)
        except ValueError as exc:
            raise RuntimeError(f"invalid inherited writer lock fd: {raw_fd!r}") from exc
        return cls(data_root, inherited_fd=inherited_fd)

    def __enter__(self):
        lock_path = self.data_root / "audit" / "data_sync.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        if self.inherited_fd is not None:
            try:
                inherited_stat = os.fstat(self.inherited_fd)
                lock_stat = lock_path.stat()
            except OSError as exc:
                raise RuntimeError("inherited writer lock fd is not open") from exc
            if (inherited_stat.st_dev, inherited_stat.st_ino) != (
                lock_stat.st_dev,
                lock_stat.st_ino,
            ):
                raise RuntimeError("inherited writer lock fd does not match data-root lock inode")
            duplicate = os.dup(self.inherited_fd)
            self.handle = os.fdopen(duplicate, "a+")
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                self.handle.close()
                self.handle = None
                raise RuntimeError("inherited data-root writer lock is not held") from exc
            return self
        self.handle = lock_path.open("a+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"data sync already holds writer lock: {lock_path}") from exc
        self._owns_unlock = True
        return self

    def fileno(self) -> int:
        if self.handle is None:
            raise RuntimeError("writer lock is not active")
        return self.handle.fileno()

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            if self._owns_unlock:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_run_id(prefix: str = "data_sync") -> str:
    """Return a collision-resistant identity suitable for an artifact path."""

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return validate_run_id(f"{prefix}_{stamp}_{uuid.uuid4().hex[:8]}")


def stable_scope_hash(values: Iterable[str]) -> str:
    normalized = sorted({str(value).strip() for value in values if str(value).strip()})
    return hashlib.sha256(_json_text(normalized).encode("utf-8")).hexdigest()


def fetch_checkpoint_key(
    *,
    source: str,
    endpoint: str,
    contract_version: str,
    scope_key: str,
    universe: str,
    date_start: str,
    date_end: str,
    symbols_sha256: str,
    request_variant: str | None = None,
    request_sha256: str | None = None,
    schema_version: int = AUDIT_SCHEMA_VERSION,
) -> str:
    """Return the stable identity of one resumable remote request shard."""

    normalized_start = _normalise_trade_date(date_start)
    normalized_end = _normalise_trade_date(date_end)
    if (
        not re.fullmatch(r"\d{8}", normalized_start)
        or not re.fullmatch(r"\d{8}", normalized_end)
        or normalized_start > normalized_end
    ):
        raise ValueError("fetch checkpoint identity has an invalid date range")
    if not re.fullmatch(r"[0-9a-f]{64}", str(symbols_sha256)):
        raise ValueError("fetch checkpoint identity has an invalid symbols_sha256")
    payload = {
        "schema_version": int(schema_version),
        "source": str(source),
        "endpoint": str(endpoint),
        "contract_version": str(contract_version),
        "scope_key": str(scope_key),
        "universe": str(universe),
        "date_start": normalized_start,
        "date_end": normalized_end,
        "symbols_sha256": str(symbols_sha256),
    }
    if request_variant is not None:
        if not str(request_variant).strip():
            raise ValueError("fetch checkpoint request_variant cannot be empty")
        payload["request_variant"] = str(request_variant)
    if request_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", str(request_sha256)):
            raise ValueError("fetch checkpoint identity has an invalid request_sha256")
        payload["request_sha256"] = str(request_sha256)
    if not all(str(value).strip() for value in payload.values()):
        raise ValueError("fetch checkpoint identity has an empty component")
    return hashlib.sha256(_json_text(payload).encode("utf-8")).hexdigest()


def checkpoint_requested_scope(
    requested_scope: Mapping[str, Any],
    *,
    source: str,
    endpoint: str,
    contract_version: str,
    scope_key: str,
    universe: str,
    request_variant: str | None = None,
    request_sha256: str | None = None,
) -> dict[str, Any]:
    """Attach a deterministic checkpoint key without changing request semantics."""

    scope = dict(requested_scope)
    scope["scope_key"] = str(scope_key)
    scope["universe"] = str(universe)
    effective_variant = (
        request_variant
        if request_variant is not None
        else scope.get("request_variant")
    )
    if effective_variant is not None:
        scope["request_variant"] = str(effective_variant)
    effective_request_sha256 = (
        request_sha256
        if request_sha256 is not None
        else scope.get("request_sha256")
    )
    if (
        request_sha256 is not None
        and scope.get("request_sha256") is not None
        and str(scope["request_sha256"]) != str(request_sha256)
    ):
        raise ValueError("requested_scope request_sha256 mismatch")
    if effective_request_sha256 is not None:
        scope["request_sha256"] = str(effective_request_sha256)
    scope["checkpoint_key"] = fetch_checkpoint_key(
        source=source,
        endpoint=endpoint,
        contract_version=contract_version,
        scope_key=scope_key,
        universe=universe,
        date_start=str(scope.get("date_start") or ""),
        date_end=str(scope.get("date_end") or ""),
        symbols_sha256=str(scope.get("symbols_sha256") or ""),
        request_variant=effective_variant,
        request_sha256=effective_request_sha256,
    )
    return scope


def redact_secrets(value: Any) -> Any:
    """Recursively redact credentials before they reach SQLite or JSON."""

    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact_secrets(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _SECRET_VALUE.sub(r"\1\2[REDACTED]", value)
    return value


def _json_text(value: Any) -> str:
    return json.dumps(
        redact_secrets(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _stable_value(value: Any) -> Any:
    if value is None or value is pd.NA:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return value.hex()
    if isinstance(value, (int, bool, str)):
        return value
    return str(value)


def _window_hash(*, symbol: str, trade_date: str, row: Mapping[str, Any] | None, fields: Sequence[str]) -> str:
    payload = {
        "symbol": symbol,
        "trade_date": trade_date,
        "fields": {field: _stable_value(row.get(field)) if row is not None else None for field in fields},
        "row_present": row is not None,
    }
    return hashlib.sha256(_json_text(payload).encode("utf-8")).hexdigest()


def normalized_response_metadata(frame: pd.DataFrame | None) -> dict[str, Any]:
    """Hash a normalized response without persisting source values."""

    if frame is None:
        frame = pd.DataFrame()
    columns = sorted(str(column) for column in frame.columns)
    rows: list[dict[str, Any]] = []
    for _, row in frame.reindex(columns=columns).iterrows():
        rows.append({column: _stable_value(row[column]) for column in columns})
    rows.sort(key=_json_text)
    date_column = next((name for name in ("trade_date", "ann_date", "cal_date", "end_date") if name in frame.columns), None)
    dates = sorted(
        _normalise_trade_date(value)
        for value in (frame[date_column].tolist() if date_column else [])
        if _normalise_trade_date(value)
    )
    return {
        "response_hash": hashlib.sha256(_json_text({"columns": columns, "rows": rows}).encode("utf-8")).hexdigest(),
        "response_columns": columns,
        "response_date_min": dates[0] if dates else None,
        "response_date_max": dates[-1] if dates else None,
    }


def _normalise_trade_date(value: Any) -> str:
    text = str(value).strip().replace("-", "")
    if text.endswith(".0"):
        text = text[:-2]
    return text[:8]


def build_canonical_mutations(
    *,
    symbol: str,
    incoming: pd.DataFrame,
    before: pd.DataFrame | None,
    after: pd.DataFrame,
    ingested_at: str | None = None,
) -> list[dict[str, Any]]:
    """Describe exact incoming symbol/date/field effects without storing values.

    Hashes cover only fields affected on each incoming date.  A no-op hashes an
    empty affected-field window, producing equal before/after hashes.
    """

    if incoming is None or incoming.empty or "trade_date" not in incoming.columns:
        return []
    observed_ingest = ingested_at or utc_now()
    before_frame = before.copy() if before is not None else pd.DataFrame()
    after_frame = after.copy()
    incoming_dates = sorted({_normalise_trade_date(item) for item in incoming["trade_date"].tolist()})

    def rows_by_date(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
        if frame.empty or "trade_date" not in frame.columns:
            return {}
        dates = frame["trade_date"].map(_normalise_trade_date)
        frame = frame.loc[dates.isin(incoming_dates)]
        result: dict[str, dict[str, Any]] = {}
        for _, row in frame.iterrows():
            result[_normalise_trade_date(row["trade_date"])] = row.to_dict()
        return result

    old_rows = rows_by_date(before_frame)
    new_rows = rows_by_date(after_frame)
    excluded = {"trade_date", "ts_code"}
    candidate_fields = sorted((set(before_frame.columns) | set(after_frame.columns)) - excluded)
    receipts: list[dict[str, Any]] = []
    for trade_date in incoming_dates:
        old_row = old_rows.get(trade_date)
        new_row = new_rows.get(trade_date)
        if new_row is None:
            raise RuntimeError(f"canonical write lost incoming row {symbol}/{trade_date}")
        if old_row is None:
            # Row presence and explicit missingness are dependency-relevant.
            changed_fields = list(candidate_fields)
            mutation_type = "insert"
        else:
            changed_fields = [
                field
                for field in candidate_fields
                if _stable_value(old_row.get(field)) != _stable_value(new_row.get(field))
            ]
            mutation_type = "update" if changed_fields else "noop"
        receipts.append(
            {
                "symbol": symbol,
                "dataset": "canonical_daily",
                "source": "tushare",
                "endpoint": "daily",
                "fetch_receipt_id": None,
                "date_start": trade_date,
                "date_end": trade_date,
                "fields": changed_fields,
                "mutation_type": mutation_type,
                "before_hash": _window_hash(
                    symbol=symbol, trade_date=trade_date, row=old_row, fields=changed_fields
                ),
                "after_hash": _window_hash(
                    symbol=symbol, trade_date=trade_date, row=new_row, fields=changed_fields
                ),
                "ingested_at": observed_ingest,
            }
        )
    return receipts


class SourceAuditStore:
    """Minimal SQLite SOT for source receipts, mutations and watermarks."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._resume_validation_token = uuid.uuid4().hex
        self._validated_resume_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _data_root(self) -> Path:
        return self.db_path.parent.parent if self.db_path.parent.name == "audit" else self.db_path.parent

    def _init_schema(self) -> None:
        with self._connect() as conn:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            existing = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
            if version == 0 and existing:
                raise RuntimeError("audit.db schema has tables but PRAGMA user_version=0; refusing incompatible schema")
            if version not in {0, AUDIT_SCHEMA_VERSION}:
                raise RuntimeError(
                    f"unsupported audit.db schema version {version}; expected {AUDIT_SCHEMA_VERSION}"
                )
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_journal (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fetch_receipts (
                    receipt_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    contract_version TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('success','empty','partial','failure')),
                    requested_scope_json TEXT NOT NULL,
                    returned_rows INTEGER NOT NULL,
                    response_hash TEXT NOT NULL,
                    response_columns_json TEXT NOT NULL,
                    response_date_min TEXT,
                    response_date_max TEXT,
                    attempt_count INTEGER NOT NULL CHECK(attempt_count > 0),
                    payload_kind TEXT NOT NULL CHECK(payload_kind IN ('raw_supplier','derived')),
                    payload_path TEXT,
                    payload_sha256 TEXT,
                    published_at TEXT,
                    observed_at TEXT NOT NULL,
                    error_json TEXT
                );
                CREATE TABLE IF NOT EXISTS canonical_mutations (
                    mutation_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    source TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    fetch_receipt_id TEXT,
                    symbol TEXT NOT NULL,
                    date_start TEXT NOT NULL,
                    date_end TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    mutation_type TEXT NOT NULL CHECK(mutation_type IN ('insert','update','noop')),
                    before_hash TEXT NOT NULL,
                    after_hash TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    FOREIGN KEY(fetch_receipt_id) REFERENCES fetch_receipts(receipt_id)
                );
                CREATE TABLE IF NOT EXISTS field_receipt_links (
                    run_id TEXT NOT NULL,
                    dataset TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    receipt_id TEXT NOT NULL,
                    PRIMARY KEY(run_id,dataset,field_name,receipt_id),
                    FOREIGN KEY(receipt_id) REFERENCES fetch_receipts(receipt_id)
                );
                CREATE TABLE IF NOT EXISTS trusted_watermarks (
                    source TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    range_start TEXT NOT NULL,
                    range_end TEXT NOT NULL,
                    trusted_through TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_receipt_sha256 TEXT NOT NULL,
                    PRIMARY KEY(source, field_name, scope_key)
                );
                CREATE INDEX IF NOT EXISTS canonical_mutations_run_symbol_type_idx
                ON canonical_mutations(run_id,symbol,mutation_type);
                CREATE INDEX IF NOT EXISTS canonical_mutations_run_mutation_idx
                ON canonical_mutations(run_id,mutation_id);
                CREATE TRIGGER IF NOT EXISTS fetch_receipts_no_update
                BEFORE UPDATE ON fetch_receipts BEGIN SELECT RAISE(ABORT, 'fetch receipts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS fetch_receipts_no_delete
                BEFORE DELETE ON fetch_receipts BEGIN SELECT RAISE(ABORT, 'fetch receipts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS canonical_mutations_no_update
                BEFORE UPDATE ON canonical_mutations BEGIN SELECT RAISE(ABORT, 'canonical mutations are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS canonical_mutations_no_delete
                BEFORE DELETE ON canonical_mutations BEGIN SELECT RAISE(ABORT, 'canonical mutations are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_journal_no_update
                BEFORE UPDATE ON audit_journal BEGIN SELECT RAISE(ABORT, 'audit journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS audit_journal_no_delete
                BEFORE DELETE ON audit_journal BEGIN SELECT RAISE(ABORT, 'audit journal is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS field_receipt_links_no_update
                BEFORE UPDATE ON field_receipt_links BEGIN SELECT RAISE(ABORT, 'field receipt links are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS field_receipt_links_no_delete
                BEFORE DELETE ON field_receipt_links BEGIN SELECT RAISE(ABORT, 'field receipt links are append-only'); END;
                """
            )
            conn.execute(f"PRAGMA user_version={AUDIT_SCHEMA_VERSION}")

    def append_event(self, run_id: str, event_type: str, payload: Mapping[str, Any] | None = None) -> None:
        run_id = validate_run_id(run_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_journal(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (run_id, event_type, _json_text(payload or {}), utc_now()),
            )

    def seal_interrupted_run_for_resume(
        self,
        *,
        run_id: str,
        expected_entrypoint: str,
        universe: str,
        target_date: str,
        range_start: str | None = None,
    ) -> None:
        """Seal a lineage-matched interrupted writer run before explicit resume."""

        run_id = validate_run_id(run_id)
        data_root = self._data_root()
        receipt_root = data_root / "audit" / "source_runs"
        receipt_path = resolve_under(
            data_root, receipt_root / run_id / "receipt.json",
        )
        if receipt_path.is_file():
            return
        expected = {
            "entrypoint": str(expected_entrypoint),
            "universe": str(universe),
            "target_date": _normalise_trade_date(target_date),
        }
        if range_start is not None:
            expected["range_start"] = _normalise_trade_date(range_start)
        matched = False
        with self._connect() as conn:
            events = conn.execute(
                "SELECT event_type,payload_json FROM audit_journal WHERE run_id=? ORDER BY seq",
                (run_id,),
            ).fetchall()
            if conn.execute(
                "SELECT 1 FROM trusted_watermarks WHERE run_id=? LIMIT 1", (run_id,),
            ).fetchone() is not None:
                raise ValueError("trusted run cannot be sealed for resume")
        for event in events:
            payload = json.loads(event["payload_json"])
            if event["event_type"] == "terminal_gate_passed" or (
                event["event_type"] == "watermark_unchanged"
                and payload.get("prior_trusted") is True
            ):
                raise ValueError("trusted run cannot be sealed for resume")
            if event["event_type"] != "run_started":
                continue
            candidate = {
                "entrypoint": str(payload.get("entrypoint") or ""),
                "universe": str(payload.get("universe") or ""),
                "target_date": _normalise_trade_date(payload.get("target_date")),
            }
            payload_range = str(payload.get("range_start") or "").strip()
            if payload_range:
                candidate["range_start"] = _normalise_trade_date(payload_range)
            if candidate == expected:
                matched = True
        if not matched:
            raise ValueError("interrupted resume source run_started lineage mismatch")
        self.record_crash_receipt(
            run_id=run_id,
            receipt_root=receipt_root,
            entrypoint=expected_entrypoint,
            error="interrupted_without_terminal_receipt",
        )

    def validate_resume_run(
        self,
        *,
        resume_from_run_id: str,
        expected_entrypoint: str,
        universe: str,
        target_date: str,
        range_start: str | None = None,
        expected_receipt_sha256: str | None = None,
    ) -> dict[str, str]:
        """Validate one immutable failed terminal snapshot against the SQLite SOT."""

        resume_from_run_id = validate_run_id(resume_from_run_id)
        data_root = self._data_root()
        receipt_path = resolve_under(
            data_root,
            data_root / "audit" / "source_runs" / resume_from_run_id / "receipt.json",
        )
        if not receipt_path.is_file():
            raise ValueError(f"resume source receipt missing: {resume_from_run_id}")
        receipt_bytes = receipt_path.read_bytes()
        receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
        if expected_receipt_sha256 is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", str(expected_receipt_sha256)):
                raise ValueError("expected resume receipt SHA-256 is invalid")
            if receipt_sha256 != str(expected_receipt_sha256):
                raise ValueError("resume source receipt SHA-256 mismatch")
        try:
            receipt = json.loads(receipt_bytes)
        except json.JSONDecodeError as exc:
            raise ValueError("resume source receipt is invalid JSON") from exc
        trust_state = str(receipt.get("trust_state") or "")
        if receipt.get("run_id") != resume_from_run_id:
            raise ValueError("resume source receipt run_id mismatch")
        if receipt.get("schema_version") != AUDIT_SCHEMA_VERSION:
            raise ValueError("resume source receipt schema_version mismatch")
        if trust_state.startswith("trusted"):
            raise ValueError("trusted terminal run cannot be used as resume source")
        if trust_state != "untrusted":
            raise ValueError(f"resume source is not an explicit failed run: {trust_state!r}")
        expected_lineage = {
            "entrypoint": str(expected_entrypoint),
            "universe": str(universe),
            "target_date": _normalise_trade_date(target_date),
        }
        if range_start is not None:
            expected_lineage["range_start"] = _normalise_trade_date(range_start)
        if (
            not expected_lineage["entrypoint"]
            or not expected_lineage["universe"]
            or not re.fullmatch(r"\d{8}", expected_lineage["target_date"])
        ):
            raise ValueError("expected resume run_started lineage is invalid")
        journal = receipt.get("audit_journal")
        fetch_rows = receipt.get("fetch_receipts")
        field_links = receipt.get("field_receipt_links")
        if not isinstance(journal, list):
            raise ValueError("resume source terminal audit_journal is invalid")
        if not isinstance(fetch_rows, list):
            raise ValueError("resume source terminal fetch_receipts is invalid")
        if not isinstance(field_links, list):
            raise ValueError("resume source terminal field_receipt_links is invalid")

        with self._connect() as conn:
            db_journal = {}
            trusted_in_db = False
            for row in conn.execute(
                "SELECT * FROM audit_journal WHERE run_id=? ORDER BY seq",
                (resume_from_run_id,),
            ).fetchall():
                decoded = dict(row)
                decoded["payload"] = json.loads(decoded.pop("payload_json"))
                db_journal[int(decoded["seq"])] = decoded
                if decoded["event_type"] == "terminal_gate_passed":
                    trusted_in_db = True
                if (
                    decoded["event_type"] == "watermark_unchanged"
                    and decoded["payload"].get("prior_trusted") is True
                ):
                    trusted_in_db = True
            if trusted_in_db:
                raise ValueError("trusted terminal run cannot be used as resume source")

            db_fetches: dict[str, dict[str, Any]] = {}
            for row in conn.execute(
                "SELECT * FROM fetch_receipts WHERE run_id=? ORDER BY rowid",
                (resume_from_run_id,),
            ).fetchall():
                decoded = dict(row)
                decoded["requested_scope"] = json.loads(
                    decoded.pop("requested_scope_json")
                )
                decoded["response_columns"] = json.loads(
                    decoded.pop("response_columns_json")
                )
                # Mirror the immutable receipt decoder byte-for-byte.  The
                # existing v1 export retains a null error_json key while also
                # exposing error=null; compatibility is intentional here.
                if decoded["error_json"]:
                    decoded["error"] = json.loads(decoded.pop("error_json"))
                else:
                    decoded["error"] = None
                db_fetches[str(decoded["receipt_id"])] = decoded

            db_links = {
                (
                    str(row["run_id"]),
                    str(row["dataset"]),
                    str(row["field_name"]),
                    str(row["receipt_id"]),
                )
                for row in conn.execute(
                    "SELECT * FROM field_receipt_links WHERE run_id=?",
                    (resume_from_run_id,),
                ).fetchall()
            }

        matched = False
        terminal_failure_marker = False
        for event in journal:
            if not isinstance(event, Mapping):
                continue
            try:
                seq = int(event.get("seq"))
            except (TypeError, ValueError):
                continue
            exact_db_event = dict(event) == db_journal.get(seq)
            event_type = event.get("event_type")
            payload = event.get("payload")
            if exact_db_event and (
                event_type in {"crash", "terminal_gate_failed"}
                or (
                    event_type == "watermark_unchanged"
                    and isinstance(payload, Mapping)
                    and payload.get("prior_trusted") is False
                )
            ):
                terminal_failure_marker = True
            if event_type != "run_started":
                continue
            payload = event.get("payload")
            if not isinstance(payload, Mapping):
                continue
            try:
                lineage = {
                    "entrypoint": str(payload.get("entrypoint") or ""),
                    "universe": str(payload.get("universe") or ""),
                    "target_date": _normalise_trade_date(payload.get("target_date")),
                }
                payload_range = str(payload.get("range_start") or "").strip()
                if payload_range:
                    lineage["range_start"] = _normalise_trade_date(payload_range)
            except (TypeError, ValueError):
                continue
            if not re.fullmatch(r"\d{8}", lineage["target_date"]):
                continue
            if lineage == expected_lineage and exact_db_event:
                matched = True
        if not matched:
            raise ValueError("resume source run_started lineage mismatch")
        if not terminal_failure_marker:
            raise ValueError("resume source terminal failure marker is missing or unverified")

        terminal_receipt_ids: set[str] = set()
        receipt_index: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
        for row in fetch_rows:
            if not isinstance(row, Mapping):
                raise ValueError("resume source terminal fetch row is invalid")
            receipt_id = str(row.get("receipt_id") or "")
            if not receipt_id or receipt_id in terminal_receipt_ids:
                raise ValueError("resume source terminal has duplicate/unknown receipt")
            terminal_receipt_ids.add(receipt_id)
            db_row = db_fetches.get(receipt_id)
            if (
                db_row is None
                or row.get("run_id") != resume_from_run_id
                or dict(row) != db_row
            ):
                raise ValueError("resume source terminal fetch row does not match SQLite")
            if row.get("status") not in {"success", "empty"}:
                continue
            if row.get("payload_kind") != "raw_supplier":
                continue
            scope = row.get("requested_scope")
            if not isinstance(scope, Mapping):
                raise ValueError("resume source terminal fetch scope is invalid")
            lookup_key = (
                str(row.get("source") or ""),
                str(row.get("endpoint") or ""),
                str(row.get("contract_version") or ""),
                str(scope.get("checkpoint_key") or ""),
                _json_text(scope),
            )
            receipt_index[lookup_key] = dict(row)

        links_by_receipt: dict[str, list[dict[str, str]]] = {}
        terminal_link_keys: set[tuple[str, str, str, str]] = set()
        for link in field_links:
            if not isinstance(link, Mapping):
                raise ValueError("resume source terminal field link is invalid")
            link_key = (
                str(link.get("run_id") or ""),
                str(link.get("dataset") or ""),
                str(link.get("field_name") or ""),
                str(link.get("receipt_id") or ""),
            )
            if (
                link_key in terminal_link_keys
                or link_key not in db_links
                or link_key[0] != resume_from_run_id
                or link_key[3] not in terminal_receipt_ids
                or dict(link) != {
                    "run_id": link_key[0],
                    "dataset": link_key[1],
                    "field_name": link_key[2],
                    "receipt_id": link_key[3],
                }
            ):
                raise ValueError("resume source terminal field link does not match SQLite")
            terminal_link_keys.add(link_key)
            links_by_receipt.setdefault(link_key[3], []).append(dict(link))

        proof = {
            "resume_from_run_id": resume_from_run_id,
            "receipt_path": str(receipt_path),
            "receipt_sha256": receipt_sha256,
            "validation_token": self._resume_validation_token,
            **expected_lineage,
        }
        self._validated_resume_cache[(resume_from_run_id, receipt_sha256)] = {
            "proof": dict(proof),
            "receipt_index": receipt_index,
            "links_by_receipt": links_by_receipt,
            "validated_current_runs": set(),
        }
        return proof

    def reuse_fetch_shard(
        self,
        *,
        run_id: str,
        resume_proof: Mapping[str, Any],
        source: str,
        endpoint: str,
        contract_version: str,
        requested_scope: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Clone one verified durable remote shard into the fresh run."""

        run_id = validate_run_id(run_id)
        resume_from_run_id = validate_run_id(str(resume_proof.get("resume_from_run_id") or ""))
        if run_id == resume_from_run_id:
            raise ValueError("resume must create a fresh run_id")
        receipt_sha256 = str(resume_proof.get("receipt_sha256") or "")
        cache = self._validated_resume_cache.get(
            (resume_from_run_id, receipt_sha256)
        )
        if cache is None or dict(resume_proof) != cache["proof"]:
            raise ValueError("resume proof was not validated by this audit store")
        validated_proof = cache["proof"]
        expected_current_lineage = {
            "entrypoint": validated_proof["entrypoint"],
            "universe": validated_proof["universe"],
            "target_date": validated_proof["target_date"],
        }
        if "range_start" in validated_proof:
            expected_current_lineage["range_start"] = validated_proof["range_start"]
        if run_id not in cache["validated_current_runs"]:
            with self._connect() as conn:
                current_events = conn.execute(
                    """SELECT payload_json FROM audit_journal
                       WHERE run_id=? AND event_type='run_started' ORDER BY seq""",
                    (run_id,),
                ).fetchall()
            current_lineage_ok = False
            for event in current_events:
                try:
                    payload = json.loads(event["payload_json"])
                    candidate = {
                        "entrypoint": str(payload.get("entrypoint") or ""),
                        "universe": str(payload.get("universe") or ""),
                        "target_date": _normalise_trade_date(payload.get("target_date")),
                    }
                    payload_range = str(payload.get("range_start") or "").strip()
                    if payload_range:
                        candidate["range_start"] = _normalise_trade_date(payload_range)
                except (AttributeError, TypeError, json.JSONDecodeError):
                    continue
                if candidate == expected_current_lineage:
                    current_lineage_ok = True
                    break
            if not current_lineage_ok:
                raise ValueError("current run_started lineage does not match resume proof")
            cache["validated_current_runs"].add(run_id)
        expected_scope = dict(requested_scope)
        checkpoint_key = str(expected_scope.get("checkpoint_key") or "")
        if not checkpoint_key:
            raise ValueError("resumable requested_scope is missing checkpoint_key")
        expected_checkpoint_key = fetch_checkpoint_key(
            source=source,
            endpoint=endpoint,
            contract_version=contract_version,
            scope_key=str(expected_scope.get("scope_key") or ""),
            universe=str(expected_scope.get("universe") or ""),
            date_start=str(expected_scope.get("date_start") or ""),
            date_end=str(expected_scope.get("date_end") or ""),
            symbols_sha256=str(expected_scope.get("symbols_sha256") or ""),
            request_variant=expected_scope.get("request_variant"),
            request_sha256=expected_scope.get("request_sha256"),
        )
        if checkpoint_key != expected_checkpoint_key:
            raise ValueError("resumable requested_scope checkpoint_key mismatch")
        lookup_key = (
            str(source),
            str(endpoint),
            str(contract_version),
            checkpoint_key,
            _json_text(expected_scope),
        )
        selected = cache["receipt_index"].get(lookup_key)
        if selected is None:
            return None
        try:
            frame = self._verified_reusable_frame(selected)
        except Exception:
            return None
        if frame is None:
            return None

        receipt_id = uuid.uuid4().hex
        reused_at = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """INSERT INTO fetch_receipts(
                    receipt_id,run_id,source,endpoint,contract_version,status,
                    requested_scope_json,returned_rows,response_hash,response_columns_json,
                    response_date_min,response_date_max,attempt_count,payload_kind,payload_path,payload_sha256,
                    published_at,observed_at,error_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id, run_id, selected["source"], selected["endpoint"],
                    selected["contract_version"], selected["status"],
                    _json_text(selected["requested_scope"]), selected["returned_rows"],
                    selected["response_hash"], _json_text(selected["response_columns"]),
                    selected["response_date_min"], selected["response_date_max"],
                    selected["attempt_count"], selected["payload_kind"],
                    selected["payload_path"], selected["payload_sha256"],
                    selected["published_at"], selected["observed_at"], None,
                ),
            )
            old_links = cache["links_by_receipt"].get(selected["receipt_id"], [])
            for link in old_links:
                conn.execute(
                    "INSERT INTO field_receipt_links(run_id,dataset,field_name,receipt_id) VALUES(?,?,?,?)",
                    (run_id, link["dataset"], link["field_name"], receipt_id),
                )
            conn.execute(
                "INSERT INTO audit_journal(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (
                    run_id,
                    "fetch_shard_reused",
                    _json_text({
                        "resume_from_run_id": resume_from_run_id,
                        "source_receipt_id": selected["receipt_id"],
                        "receipt_id": receipt_id,
                        "source": source,
                        "endpoint": endpoint,
                        "checkpoint_key": checkpoint_key,
                        "reused_at": reused_at,
                    }),
                    reused_at,
                ),
            )
        return {
            "frame": frame,
            "receipt_id": receipt_id,
            "status": str(selected["status"]),
            "source_receipt_id": str(selected["receipt_id"]),
        }

    def _verified_reusable_frame(self, row: Mapping[str, Any]) -> pd.DataFrame | None:
        response_columns = row.get("response_columns")
        if not isinstance(response_columns, list):
            return None
        if row.get("error") is not None:
            return None
        if not str(row.get("receipt_id") or "").strip():
            return None
        try:
            if int(row.get("attempt_count")) < 1:
                return None
        except (TypeError, ValueError):
            return None
        if not isinstance(row.get("observed_at"), str) or not row["observed_at"].strip():
            return None
        if row.get("published_at") is not None and not isinstance(row["published_at"], str):
            return None
        if row["status"] == "empty":
            if (
                int(row["returned_rows"]) != 0
                or row["payload_path"] is not None
                or row["payload_sha256"] is not None
            ):
                return None
            frame = pd.DataFrame(columns=response_columns)
        else:
            if not row["payload_path"] or not row["payload_sha256"]:
                return None
            data_root = self._data_root()
            relative_path = Path(str(row["payload_path"]))
            if relative_path.is_absolute():
                return None
            path = resolve_under(data_root, data_root / relative_path)
            if not path.is_file():
                return None
            if hashlib.sha256(path.read_bytes()).hexdigest() != row["payload_sha256"]:
                return None
            frame = pd.read_parquet(path)
            if frame.empty or len(frame) != int(row["returned_rows"]):
                return None
        metadata = normalized_response_metadata(frame)
        if (
            metadata["response_hash"] != row["response_hash"]
            or metadata["response_columns"] != response_columns
            or metadata["response_date_min"] != row["response_date_min"]
            or metadata["response_date_max"] != row["response_date_max"]
        ):
            return None
        return frame

    def record_fetch(
        self,
        *,
        run_id: str,
        source: str,
        endpoint: str,
        status: str,
        requested_scope: Mapping[str, Any],
        returned_rows: int,
        response_hash: str,
        response_columns: Sequence[str],
        response_date_min: str | None,
        response_date_max: str | None,
        attempt_count: int,
        payload_frame: pd.DataFrame | None = None,
        payload_kind: str = "raw_supplier",
        published_at: str | None = None,
        observed_at: str | None = None,
        error: Any = None,
        contract_version: str = "1",
    ) -> str:
        run_id = validate_run_id(run_id)
        if status not in {"success", "empty", "partial", "failure"}:
            raise ValueError(f"invalid fetch receipt status: {status}")
        if payload_kind not in {"raw_supplier", "derived"}:
            raise ValueError(f"invalid payload kind: {payload_kind}")
        receipt_id = uuid.uuid4().hex
        payload_path = None
        payload_sha256 = None
        if status in {"success", "partial"} and payload_kind == "raw_supplier":
            if payload_frame is None:
                raise ValueError("successful supplier receipt requires raw payload_frame")
            for component in (source, endpoint, run_id):
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(component)):
                    raise ValueError(f"unsafe evidence path component: {component!r}")
            data_root = self._data_root()
            target = resolve_under(
                data_root,
                data_root / "raw" / "evidence" / source / endpoint / run_id / f"{receipt_id}.parquet",
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
            os.close(fd)
            try:
                payload_frame.to_parquet(temp_name, index=False)
                temp_fd = os.open(temp_name, os.O_RDONLY)
                try:
                    os.fsync(temp_fd)
                finally:
                    os.close(temp_fd)
                os.link(temp_name, target)
                dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                raise
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            payload_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            payload_path = target.relative_to(data_root).as_posix()
        error_json = None if error is None else _json_text({"detail": error})
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO fetch_receipts(
                    receipt_id,run_id,source,endpoint,contract_version,status,
                    requested_scope_json,returned_rows,response_hash,response_columns_json,
                    response_date_min,response_date_max,attempt_count,payload_kind,payload_path,payload_sha256,
                    published_at,observed_at,error_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    receipt_id,
                    run_id,
                    source,
                    endpoint,
                    contract_version,
                    status,
                    _json_text(requested_scope),
                    max(0, int(returned_rows)),
                    str(response_hash),
                    _json_text(list(response_columns)),
                    response_date_min,
                    response_date_max,
                    int(attempt_count),
                    payload_kind,
                    payload_path,
                    payload_sha256,
                    published_at,
                    observed_at or utc_now(),
                    error_json,
                ),
            )
        return receipt_id

    def record_field_receipt_links(
        self,
        *,
        run_id: str,
        receipt_id: str,
        fields: Iterable[str],
        dataset: str = "canonical_daily",
    ) -> None:
        run_id = validate_run_id(run_id)
        with self._connect() as conn:
            receipt = conn.execute(
                "SELECT run_id FROM fetch_receipts WHERE receipt_id=?", (receipt_id,)
            ).fetchone()
            if receipt is None or str(receipt["run_id"]) != run_id:
                raise ValueError("field receipt linkage must reference the same run")
            for field_name in sorted({str(field) for field in fields if str(field)}):
                conn.execute(
                    """INSERT INTO field_receipt_links(run_id,dataset,field_name,receipt_id)
                       SELECT ?,?,?,? WHERE NOT EXISTS (
                         SELECT 1 FROM field_receipt_links
                         WHERE run_id=? AND dataset=? AND field_name=? AND receipt_id=?
                       )""",
                    (
                        run_id, dataset, field_name, receipt_id,
                        run_id, dataset, field_name, receipt_id,
                    ),
                )

    def evaluate_field_receipts(
        self,
        *,
        run_id: str,
        field_endpoints: Mapping[str, str],
        dataset: str = "canonical_daily",
    ) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        data_root = self._data_root()
        fields: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            for field_name, endpoint in field_endpoints.items():
                row = conn.execute(
                    """SELECT f.receipt_id,f.status,f.endpoint,f.payload_path,f.payload_sha256
                       FROM field_receipt_links l JOIN fetch_receipts f ON f.receipt_id=l.receipt_id
                       WHERE l.run_id=? AND l.dataset=? AND l.field_name=? AND f.endpoint=?
                       ORDER BY f.rowid DESC LIMIT 1""",
                    (run_id, dataset, field_name, endpoint),
                ).fetchone()
                passed = bool(row) and row["status"] == "success"
                reason = "missing_field_receipt" if row is None else f"endpoint_status_{row['status']}"
                if passed:
                    path = resolve_under(data_root, data_root / str(row["payload_path"]))
                    passed = path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row["payload_sha256"]
                    reason = "ok" if passed else "payload_invalid"
                fields[field_name] = {
                    "status": "success" if passed else "failed",
                    "endpoint": endpoint,
                    "receipt_id": row["receipt_id"] if row else None,
                    "reason": reason,
                }
        return {"status": "success" if all(v["status"] == "success" for v in fields.values()) else "failed", "fields": fields}

    def evaluate_history_field_receipts(
        self,
        *,
        run_id: str,
        field_endpoints: Mapping[str, str],
        dataset: str = "canonical_daily",
    ) -> dict[str, Any]:
        """Validate every completed shard for a historical field request."""

        run_id = validate_run_id(run_id)
        data_root = self._data_root()
        fields: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            for field_name, endpoint in field_endpoints.items():
                rows = conn.execute(
                    """SELECT f.receipt_id,f.status,f.returned_rows,f.payload_path,f.payload_sha256
                       FROM field_receipt_links l JOIN fetch_receipts f ON f.receipt_id=l.receipt_id
                       WHERE l.run_id=? AND l.dataset=? AND l.field_name=? AND f.endpoint=?
                       ORDER BY f.rowid""",
                    (run_id, dataset, field_name, endpoint),
                ).fetchall()
                passed = bool(rows)
                reason = "missing_field_receipt" if not rows else "ok"
                for row in rows:
                    if row["status"] not in {"success", "empty"}:
                        passed = False
                        reason = f"endpoint_status_{row['status']}"
                        break
                    if row["status"] == "success":
                        path = resolve_under(data_root, data_root / str(row["payload_path"]))
                        if (
                            not path.is_file()
                            or hashlib.sha256(path.read_bytes()).hexdigest()
                            != row["payload_sha256"]
                        ):
                            passed = False
                            reason = "payload_invalid"
                            break
                    elif int(row["returned_rows"]) != 0:
                        passed = False
                        reason = "empty_receipt_has_rows"
                        break
                fields[field_name] = {
                    "status": "success" if passed else "failed",
                    "endpoint": endpoint,
                    "receipt_count": len(rows),
                    "reason": reason,
                }
        return {
            "status": (
                "success"
                if fields and all(value["status"] == "success" for value in fields.values())
                else "failed"
            ),
            "fields": fields,
        }

    def verify_payloads(self, run_id: str) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        data_root = self._data_root()
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT receipt_id,status,payload_kind,payload_path,payload_sha256
                   FROM fetch_receipts WHERE run_id=? ORDER BY rowid""",
                (run_id,),
            ).fetchall()
        failures: list[dict[str, str]] = []
        checked = 0
        for row in rows:
            if row["status"] not in {"success", "partial"} or row["payload_kind"] != "raw_supplier":
                continue
            checked += 1
            if not row["payload_path"] or not row["payload_sha256"]:
                failures.append({"receipt_id": row["receipt_id"], "reason": "payload_link_missing"})
                continue
            path = data_root / str(row["payload_path"])
            if not path.is_file():
                failures.append({"receipt_id": row["receipt_id"], "reason": "payload_missing"})
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != row["payload_sha256"]:
                failures.append({"receipt_id": row["receipt_id"], "reason": "payload_hash_mismatch"})
        return {"status": "success" if not failures else "failed", "checked_count": checked, "failures": failures}

    def verify_fetch_receipt(self, *, run_id: str, receipt_id: str) -> dict[str, Any]:
        """Verify one receipt's terminal status and raw payload linkage."""

        run_id = validate_run_id(run_id)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT status,payload_kind,payload_path,payload_sha256
                   FROM fetch_receipts WHERE run_id=? AND receipt_id=?""",
                (run_id, receipt_id),
            ).fetchone()
        if row is None:
            return {"status": "failed", "reason": "receipt_missing"}
        if row["status"] == "empty":
            return {"status": "success", "reason": "observed_empty_response"}
        if row["status"] != "success" or row["payload_kind"] != "raw_supplier":
            return {"status": "failed", "reason": f"receipt_status_{row['status']}"}
        if not row["payload_path"] or not row["payload_sha256"]:
            return {"status": "failed", "reason": "payload_link_missing"}
        data_root = self._data_root()
        path = resolve_under(data_root, data_root / str(row["payload_path"]))
        if not path.is_file():
            return {"status": "failed", "reason": "payload_missing"}
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "status": "success" if actual == row["payload_sha256"] else "failed",
            "reason": "ok" if actual == row["payload_sha256"] else "payload_hash_mismatch",
        }

    def record_mutations(self, *, run_id: str, mutations: Iterable[Mapping[str, Any]]) -> list[str]:
        run_id = validate_run_id(run_id)
        ids: list[str] = []
        with self._connect() as conn:
            for mutation in mutations:
                fetch_receipt_id = mutation.get("fetch_receipt_id")
                if fetch_receipt_id is not None:
                    receipt = conn.execute(
                        "SELECT run_id FROM fetch_receipts WHERE receipt_id=?",
                        (fetch_receipt_id,),
                    ).fetchone()
                    if receipt is None or str(receipt["run_id"]) != run_id:
                        raise ValueError("canonical mutation must reference a receipt from the same run")
                mutation_id = uuid.uuid4().hex
                conn.execute(
                    """INSERT INTO canonical_mutations(
                        mutation_id,run_id,dataset,source,endpoint,fetch_receipt_id,symbol,date_start,date_end,fields_json,
                        mutation_type,before_hash,after_hash,ingested_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        mutation_id,
                        run_id,
                        str(mutation.get("dataset") or "canonical_daily"),
                        str(mutation.get("source") or "tushare"),
                        str(mutation.get("endpoint") or "daily"),
                        fetch_receipt_id,
                        str(mutation["symbol"]),
                        str(mutation["date_start"]),
                        str(mutation["date_end"]),
                        _json_text(list(mutation.get("fields", []))),
                        str(mutation["mutation_type"]),
                        str(mutation["before_hash"]),
                        str(mutation["after_hash"]),
                        str(mutation.get("ingested_at") or utc_now()),
                    ),
                )
                ids.append(mutation_id)
        return ids

    def changed_mutation_symbols(
        self, run_id: str, *, mutation_type: str | None = None
    ) -> list[str]:
        run_id = validate_run_id(run_id)
        if mutation_type is not None and mutation_type not in {"insert", "update"}:
            raise ValueError("changed mutation_type must be insert or update")
        query = (
            "SELECT DISTINCT symbol FROM canonical_mutations "
            "WHERE run_id=? AND mutation_type IN ('insert','update')"
        )
        params: list[str] = [run_id]
        if mutation_type is not None:
            query += " AND mutation_type=?"
            params.append(mutation_type)
        query += " ORDER BY symbol"
        with self._connect() as conn:
            return [str(row[0]) for row in conn.execute(query, params).fetchall()]

    def changed_mutations(
        self, run_id: str, *, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        run_id = validate_run_id(run_id)
        query = (
            "SELECT dataset,source,endpoint,fetch_receipt_id,symbol,date_start,date_end,"
            "fields_json,mutation_type,before_hash,after_hash,ingested_at "
            "FROM canonical_mutations WHERE run_id=? "
            "AND mutation_type IN ('insert','update')"
        )
        params: list[str] = [run_id]
        if symbol is not None:
            query += " AND symbol=?"
            params.append(str(symbol))
        query += " ORDER BY symbol,date_start,mutation_id"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {
                **dict(row),
                "fields": json.loads(row["fields_json"]),
            }
            for row in rows
        ]

    def resume_lineage_run_ids(self, run_id: str) -> list[str]:
        """Return oldest-to-newest run ids for one explicit resume chain."""

        current = validate_run_id(run_id)
        newest_to_oldest: list[str] = []
        seen: set[str] = set()
        with self._connect() as conn:
            while current:
                if current in seen:
                    raise ValueError("resume lineage contains a cycle")
                seen.add(current)
                newest_to_oldest.append(current)
                rows = conn.execute(
                    """SELECT payload_json FROM audit_journal
                       WHERE run_id=? AND event_type='resume_from_run'
                       ORDER BY seq DESC LIMIT 1""",
                    (current,),
                ).fetchall()
                if not rows:
                    break
                payload = json.loads(rows[0]["payload_json"])
                parent = str(payload.get("resume_from_run_id") or "").strip()
                if not parent:
                    raise ValueError("resume lineage event is missing parent run_id")
                current = validate_run_id(parent)
        return list(reversed(newest_to_oldest))

    def run_evidence_summary(self, run_id: str) -> dict[str, Any]:
        """Return small gate inputs without treating legacy JSON as evidence."""

        run_id = validate_run_id(run_id)
        with self._connect() as conn:
            fetch_statuses = [
                str(row[0])
                for row in conn.execute(
                    "SELECT status FROM fetch_receipts WHERE run_id=? ORDER BY rowid", (run_id,)
                ).fetchall()
            ]
            events = [
                {"event_type": str(row[0]), "payload": json.loads(row[1])}
                for row in conn.execute(
                    "SELECT event_type,payload_json FROM audit_journal WHERE run_id=? ORDER BY seq",
                    (run_id,),
                ).fetchall()
            ]
            mutation_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM canonical_mutations WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
        return {
            "fetch_statuses": fetch_statuses,
            "mutation_count": mutation_count,
            "events": events,
        }

    def watermark_snapshot_bytes(self) -> bytes:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT source,field_name,scope_key,range_start,range_end,trusted_through,run_id,updated_at,terminal_receipt_sha256
                   FROM trusted_watermarks ORDER BY source,field_name,scope_key"""
            ).fetchall()
        return (_json_text([dict(row) for row in rows]) + "\n").encode("utf-8")

    def has_trusted_range(self, *, source: str, scope_key: str, range_start: str, range_end: str, fields: Sequence[str]) -> bool:
        required = sorted({str(field) for field in fields})
        if not required:
            return False
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT field_name,range_start,range_end,trusted_through
                   FROM trusted_watermarks WHERE source=? AND scope_key=?""",
                (source, scope_key),
            ).fetchall()
        by_field = {str(row["field_name"]): row for row in rows}
        return all(
            field in by_field
            and str(by_field[field]["range_start"]) <= range_start
            and str(by_field[field]["range_end"]) >= range_end
            and str(by_field[field]["trusted_through"]) >= range_end
            for field in required
        )

    def can_advance_contiguous(
        self,
        *,
        source: str,
        scope_key: str,
        range_start: str,
        target_date: str,
        fields: Sequence[str],
        previous_open_session: str | None,
        allow_initial_history: bool = False,
    ) -> bool:
        """Reject a watermark jump over an untrusted trading session.

        An explicitly verified history run may seed fields that do not yet
        have a watermark.  Fields with an existing watermark must still be
        contiguous; the opt-in never repairs or bypasses their lineage.
        """

        required = sorted({str(field) for field in fields})
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT field_name,range_start,trusted_through FROM trusted_watermarks
                   WHERE source=? AND scope_key=?""",
                (source, scope_key),
            ).fetchall()
        by_field = {str(row["field_name"]): row for row in rows}
        if not by_field:
            # First segment starts exactly at this target; caller may not claim
            # an earlier range_start in finalize_run.
            return True
        missing_fields = set(required) - set(by_field)
        if missing_fields and not allow_initial_history:
            return False
        return all(
            range_start >= str(by_field[field]["range_start"])
            and (
                str(by_field[field]["trusted_through"]) >= target_date
                or (
                    previous_open_session is not None
                    and str(by_field[field]["trusted_through"]) == previous_open_session
                )
            )
            for field in required
            if field in by_field
        )

    def finalize_unchanged(
        self,
        *,
        run_id: str,
        gates: Mapping[str, bool],
        receipt_root: str | Path,
        prior_trusted: bool,
    ) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        trust_state = "trusted_unchanged" if prior_trusted else "untrusted"
        self.append_event(run_id, "watermark_unchanged", {"prior_trusted": prior_trusted})
        receipt = self.export_receipt(run_id, receipt_root, trust_state=trust_state, gates=gates)
        return {
            "status": "unchanged" if prior_trusted else "not_trusted",
            "trust_state": trust_state,
            "receipt_path": str(receipt),
            "watermark_advanced": False,
        }

    def _receipt_payload(self, run_id: str, *, trust_state: str, gates: Mapping[str, bool]) -> dict[str, Any]:
        run_id = validate_run_id(run_id)
        with self._connect() as conn:
            fetches = [dict(row) for row in conn.execute(
                "SELECT * FROM fetch_receipts WHERE run_id=? ORDER BY rowid", (run_id,)
            ).fetchall()]
            mutation_counts = {
                str(row[0]): int(row[1])
                for row in conn.execute(
                    """SELECT mutation_type,COUNT(*) FROM canonical_mutations
                       WHERE run_id=? GROUP BY mutation_type ORDER BY mutation_type""",
                    (run_id,),
                ).fetchall()
            }
            journal = [dict(row) for row in conn.execute(
                "SELECT * FROM audit_journal WHERE run_id=? ORDER BY seq", (run_id,)
            ).fetchall()]
            field_links = [dict(row) for row in conn.execute(
                "SELECT * FROM field_receipt_links WHERE run_id=? ORDER BY dataset,field_name,receipt_id",
                (run_id,),
            ).fetchall()]
        for row in fetches:
            row["requested_scope"] = json.loads(row.pop("requested_scope_json"))
            row["response_columns"] = json.loads(row.pop("response_columns_json"))
            row["error"] = json.loads(row.pop("error_json")) if row["error_json"] else None
        for row in journal:
            row["payload"] = json.loads(row.pop("payload_json"))
        return redact_secrets(
            {
                "schema_version": AUDIT_SCHEMA_VERSION,
                "run_id": run_id,
                "trust_state": trust_state,
                "terminal_gates": dict(gates),
                "watermark_claim": "not_recorded_in_receipt",
                "fetch_receipts": fetches,
                # Mutation rows remain in the append-only SQLite SOT.  Keeping
                # millions of rows in one terminal JSON would make a full
                # history run impossible to finalize or resume safely.
                "canonical_mutations": [],
                "canonical_mutation_summary": {
                    "count": sum(mutation_counts.values()),
                    "counts_by_type": mutation_counts,
                    "storage": "audit.db:canonical_mutations",
                },
                "field_receipt_links": field_links,
                "audit_journal": journal,
                "exported_at": utc_now(),
            }
        )

    def export_receipt(self, run_id: str, receipt_root: str | Path, *, trust_state: str, gates: Mapping[str, bool]) -> Path:
        run_id = validate_run_id(run_id)
        data_root = self._data_root()
        root = resolve_under(data_root, receipt_root)
        target = resolve_under(root, root / run_id / "receipt.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self._receipt_payload(run_id, trust_state=trust_state, gates=gates)
        content = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        fd, temp_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temp_name, target)
            dir_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return target

    def record_crash_receipt(
        self,
        *,
        run_id: str,
        receipt_root: str | Path,
        entrypoint: str,
        error: Any,
    ) -> dict[str, Any]:
        """Best-effort caller primitive for an unexpected post-start crash.

        It never mutates watermarks and never overwrites an existing terminal
        receipt.  The hard-link install in ``export_receipt`` is the final
        concurrency guard.
        """

        run_id = validate_run_id(run_id)
        data_root = self._data_root()
        root = resolve_under(data_root, receipt_root)
        target = resolve_under(root, root / run_id / "receipt.json")
        if target.is_file():
            return {"status": "existing", "receipt_path": str(target)}
        self.append_event(
            run_id,
            "crash",
            {"entrypoint": entrypoint, "error": error},
        )
        gates = {name: False for name in REQUIRED_TERMINAL_GATES}
        try:
            receipt = self.export_receipt(
                run_id, root, trust_state="untrusted", gates=gates
            )
        except FileExistsError:
            return {"status": "existing", "receipt_path": str(target)}
        return {
            "status": "recorded",
            "trust_state": "untrusted",
            "receipt_path": str(receipt),
            "watermark_advanced": False,
        }

    def finalize_run(
        self,
        *,
        run_id: str,
        source: str,
        scope_key: str,
        range_start: str,
        range_end: str,
        fields: Sequence[str],
        gates: Mapping[str, bool],
        receipt_root: str | Path,
        trust_state: str = TRUSTED,
        previous_open_session: str | None = None,
        allow_initial_history: bool = False,
    ) -> dict[str, Any]:
        """Export immutable evidence then atomically advance trusted watermarks last."""

        run_id = validate_run_id(run_id)

        effective_gates = dict(gates)
        contiguous = self.can_advance_contiguous(
            source=source,
            scope_key=scope_key,
            range_start=range_start,
            target_date=range_end,
            fields=fields,
            previous_open_session=previous_open_session,
            allow_initial_history=allow_initial_history,
        )
        effective_gates["contiguous_range"] = bool(gates.get("contiguous_range")) and contiguous
        missing_gates = sorted(REQUIRED_TERMINAL_GATES - set(effective_gates))
        all_passed = not missing_gates and all(bool(effective_gates[name]) for name in REQUIRED_TERMINAL_GATES)
        with self._connect() as conn:
            existing_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM trusted_watermarks WHERE source=? AND scope_key=?",
                    (source, scope_key),
                ).fetchone()[0]
            )
        if existing_count == 0 and range_start != range_end and not allow_initial_history:
            effective_gates["contiguous_range"] = False
            all_passed = False
        effective_trust = trust_state if all_passed else "untrusted"
        if effective_trust != TRUSTED:
            self.append_event(
                run_id,
                "terminal_gate_failed",
                {"gates": effective_gates, "missing_gates": missing_gates, "trust_state": effective_trust},
            )
            receipt = self.export_receipt(run_id, receipt_root, trust_state=effective_trust, gates=effective_gates)
            return {"status": "not_trusted", "trust_state": effective_trust, "receipt_path": str(receipt), "watermark_advanced": False}

        # The receipt must exist before the watermark commit: watermark advance
        # is intentionally the last durable state transition.
        receipt = self.export_receipt(run_id, receipt_root, trust_state=TRUSTED, gates=effective_gates)
        receipt_sha256 = hashlib.sha256(receipt.read_bytes()).hexdigest()
        updated_at = utc_now()
        unique_fields = sorted({str(field) for field in fields if str(field).strip()})
        if not unique_fields:
            raise ValueError("trusted finalization requires at least one field")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO audit_journal(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (run_id, "terminal_gate_passed", _json_text({"gates": effective_gates}), updated_at),
            )
            for field_name in unique_fields:
                conn.execute(
                    """INSERT INTO trusted_watermarks(
                        source,field_name,scope_key,range_start,range_end,trusted_through,run_id,updated_at,
                        terminal_receipt_sha256
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,field_name,scope_key) DO UPDATE SET
                        range_end=CASE WHEN excluded.range_end > range_end THEN excluded.range_end ELSE range_end END,
                        trusted_through=CASE WHEN excluded.trusted_through > trusted_through THEN excluded.trusted_through ELSE trusted_through END,
                        run_id=CASE WHEN excluded.trusted_through >= trusted_through THEN excluded.run_id ELSE run_id END,
                        updated_at=CASE WHEN excluded.trusted_through >= trusted_through THEN excluded.updated_at ELSE updated_at END,
                        terminal_receipt_sha256=CASE WHEN excluded.trusted_through >= trusted_through THEN excluded.terminal_receipt_sha256 ELSE terminal_receipt_sha256 END""",
                    (source, field_name, scope_key, range_start, range_end, range_end, run_id, updated_at, receipt_sha256),
                )
        return {
            "status": "trusted",
            "trust_state": TRUSTED,
            "receipt_path": str(receipt),
            "terminal_receipt_sha256": receipt_sha256,
            "watermark_advanced": True,
        }
