"""Audited offline replay of canonical financial fields from frozen raw receipts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from qsys.data._merge_helpers import (
    FINANCIAL_AVAILABILITY_CONTRACT,
    FINANCIAL_LATEST_KNOWN_CONTRACT,
    FINANCIAL_OPERATIONAL_PIT_CONTRACT,
    FINANCIAL_VERSIONED_EVENT_CONTRACT,
    TUSHARE_FINA_INDICATOR_UNIT_CONTRACT,
)
from qsys.data.adapter import QlibAdapter
from qsys.data.collector import HISTORY_FIELD_ENDPOINTS, TushareCollector
from qsys.data.source_audit import (
    REQUIRED_TERMINAL_GATES,
    SourceAuditStore,
    canonical_history_scope_semantic_identity,
    canonical_symbol_files_sha256,
    history_scope_identity,
    new_run_id,
    normalized_response_metadata,
    stable_scope_hash,
)
from qsys.data.storage import StockDataStore
from qsys.utils.logger import log


FINANCIAL_REPLAY_CONTRACT = (
    "financial_canonical_offline_replay_v1:"
    + FINANCIAL_AVAILABILITY_CONTRACT
    + ":"
    + FINANCIAL_LATEST_KNOWN_CONTRACT
    + ":"
    + FINANCIAL_OPERATIONAL_PIT_CONTRACT
    + ":"
    + FINANCIAL_VERSIONED_EVENT_CONTRACT
    + ":"
    + TUSHARE_FINA_INDICATOR_UNIT_CONTRACT
)
FINANCIAL_REPLAY_SCHEMA = "audited_financial_canonical_replay_v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _validate_terminal(
    data_root: Path,
    *,
    run_id: str,
    expected_sha256: str,
    require_trusted: bool,
) -> dict[str, Any]:
    path = data_root / "audit" / "source_runs" / run_id / "receipt.json"
    if not path.is_file() or _sha256(path) != expected_sha256:
        raise RuntimeError(f"financial replay source terminal mismatch: {run_id}")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"financial replay source terminal is invalid: {run_id}") from exc
    if receipt.get("run_id") != run_id:
        raise RuntimeError(f"financial replay source run identity mismatch: {run_id}")
    trusted = str(receipt.get("trust_state") or "").startswith("trusted")
    if trusted != require_trusted:
        expectation = "trusted" if require_trusted else "failed/untrusted"
        raise RuntimeError(f"financial replay source must be {expectation}: {run_id}")
    return receipt


def _symbols_from_registry(path: Path) -> list[str]:
    symbols = sorted({
        line.split("\t", 1)[0].strip().upper()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    })
    if not symbols or any("/" in value or "\\" in value for value in symbols):
        raise RuntimeError("financial replay registry is empty or unsafe")
    return symbols


def _atomic_manifest(root: Path, identity: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    artifact_id = hashlib.sha256(_json_bytes(identity)).hexdigest()
    target = root / artifact_id
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target / "manifest.json"
    manifest = {
        "schema_version": 1,
        "artifact_type": FINANCIAL_REPLAY_SCHEMA,
        "artifact_id": artifact_id,
        "identity": identity,
        **payload,
    }
    content = _json_bytes(manifest)
    if manifest_path.exists():
        if manifest_path.read_bytes() != content:
            raise RuntimeError("existing financial replay artifact is not byte-identical")
    else:
        descriptor, temporary = tempfile.mkstemp(dir=target, suffix=".json.tmp")
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest_path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
    return {
        "artifact_id": artifact_id,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_json_bytes(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _recover_abandoned_replays(
    *, data_root: Path, audit: SourceAuditStore, store: StockDataStore
) -> list[str]:
    """Reconcile durable write intents, then seal any prior interrupted replay."""

    recovered: list[str] = []
    source_runs = data_root / "audit" / "source_runs"
    for progress_path in sorted(source_runs.glob("financial_replay_*/financial_replay_progress.json")):
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if progress.get("status") != "running":
            continue
        run_id = str(progress.get("run_id") or "")
        receipt_path = progress_path.parent / "receipt.json"
        if receipt_path.is_file():
            try:
                terminal = json.loads(receipt_path.read_text(encoding="utf-8"))
                terminal_failure = terminal.get("trust_state") == "untrusted"
            except (OSError, json.JSONDecodeError):
                terminal_failure = False
            progress.update({
                "status": "failed",
                "stage": (
                    "sealed_terminal_failure"
                    if terminal_failure else "sealed_interrupted"
                ),
            })
            _atomic_json(progress_path, progress)
            continue
        summary = audit.run_evidence_summary(run_id)
        intents = {
            (str(event["payload"].get("symbol") or ""), str(event["payload"].get("after_hash") or "")):
            dict(event["payload"])
            for event in summary.get("events", [])
            if event.get("event_type") == "financial_replay_mutation_intent"
        }
        commits = {
            (str(event["payload"].get("symbol") or ""), str(event["payload"].get("after_hash") or ""))
            for event in summary.get("events", [])
            if event.get("event_type") == "financial_replay_mutation_committed"
        }
        for key, mutation in intents.items():
            if key in commits:
                continue
            with audit._connect() as connection:
                recorded = connection.execute(
                    """SELECT 1 FROM canonical_mutations
                       WHERE run_id=? AND symbol=? AND before_hash=? AND after_hash=? LIMIT 1""",
                    (
                        run_id,
                        mutation["symbol"],
                        mutation["before_hash"],
                        mutation["after_hash"],
                    ),
                ).fetchone()
            frame = store.load_daily(str(mutation["symbol"]))
            if frame is None or frame.empty:
                audit.append_event(run_id, "financial_replay_recovery_ambiguous", mutation)
                continue
            current_hash = store._projection_window_hash(
                frame,
                symbol=str(mutation["symbol"]),
                date_start=str(mutation["date_start"]),
                date_end=str(mutation["date_end"]),
                fields=[str(field) for field in mutation.get("fields") or []],
            )
            if current_hash == mutation["after_hash"]:
                if recorded is None:
                    audit.record_mutations(run_id=run_id, mutations=[mutation])
                audit.append_event(run_id, "financial_replay_mutation_committed", {
                    "symbol": mutation["symbol"],
                    "after_hash": mutation["after_hash"],
                    "recovered_after_interruption": True,
                })
            elif current_hash == mutation["before_hash"]:
                audit.append_event(run_id, "financial_replay_mutation_aborted", {
                    "symbol": mutation["symbol"],
                    "before_hash": mutation["before_hash"],
                })
            else:
                audit.append_event(run_id, "financial_replay_recovery_ambiguous", {
                    "symbol": mutation["symbol"],
                    "expected_before_hash": mutation["before_hash"],
                    "expected_after_hash": mutation["after_hash"],
                    "observed_hash": current_hash,
                })
        audit.record_crash_receipt(
            run_id=run_id,
            receipt_root=source_runs,
            entrypoint="scripts/data_sync.py:offline_financial_replay",
            error="interrupted_without_terminal_receipt",
        )
        progress.update({
            "status": "failed",
            "stage": "sealed_interrupted",
            "failure": "interrupted_without_terminal_receipt",
        })
        _atomic_json(progress_path, progress)
        recovered.append(run_id)
    return recovered


def replay_audited_financial_canonical(
    *,
    data_root: Path,
    source_run_id: str,
    source_receipt_sha256: str,
    trusted_base_run_id: str,
    trusted_base_receipt_sha256: str,
    registry_path: Path,
    range_start: str,
    range_end: str,
    output_root: Path,
    readback_mutation_run_id: str | None = None,
    readback_mutation_receipt_sha256: str | None = None,
    batch_size: int = 50,
    local_workers: int = 8,
    qlib_workers: int = 8,
) -> dict[str, Any]:
    """Replay only financial fields; every supplier call is forbidden."""

    data_root = Path(data_root).resolve()
    source_terminal = _validate_terminal(
        data_root,
        run_id=source_run_id,
        expected_sha256=source_receipt_sha256,
        require_trusted=False,
    )
    _validate_terminal(
        data_root,
        run_id=trusted_base_run_id,
        expected_sha256=trusted_base_receipt_sha256,
        require_trusted=True,
    )
    symbols = _symbols_from_registry(registry_path)
    audit = SourceAuditStore(data_root / "audit" / "audit.db")
    store = StockDataStore()
    readback_mutation_proof: dict[str, str] | None = None
    if readback_mutation_run_id is not None:
        if not readback_mutation_receipt_sha256:
            raise RuntimeError(
                "financial replay readback mutation receipt SHA-256 is required"
            )
        readback_mutation_proof = audit.validate_resume_run(
            resume_from_run_id=readback_mutation_run_id,
            expected_entrypoint="scripts/data_sync.py",
            universe="csi1800",
            target_date=range_end,
            range_start=range_start,
            expected_receipt_sha256=readback_mutation_receipt_sha256,
        )
        summary = audit.run_evidence_summary(readback_mutation_run_id)
        replay_events = [
            event["payload"] for event in summary.get("events", [])
            if event.get("event_type") == "financial_replay_started"
        ]
        qlib_events = [
            event["payload"] for event in summary.get("events", [])
            if event.get("event_type") == "qlib_readback"
        ]
        if len(replay_events) != 1 or not qlib_events:
            raise RuntimeError("financial replay readback mutation lineage is incomplete")
        replay_identity = replay_events[0]
        if (
            replay_identity.get("contract") != FINANCIAL_REPLAY_CONTRACT
            or replay_identity.get("source_run_id") != source_run_id
            or replay_identity.get("source_receipt_sha256")
            != source_receipt_sha256
            or replay_identity.get("trusted_base_run_id") != trusted_base_run_id
            or replay_identity.get("trusted_base_receipt_sha256")
            != trusted_base_receipt_sha256
            or (qlib_events[-1].get("refresh") or {}).get("status") != "success"
            or int(summary.get("mutation_count") or 0) < 1
        ):
            raise RuntimeError("financial replay readback mutation identity mismatch")
    recovered_runs = _recover_abandoned_replays(
        data_root=data_root, audit=audit, store=store
    )
    run_id = new_run_id("financial_replay")
    progress_path = data_root / "audit" / "source_runs" / run_id / "financial_replay_progress.json"
    log.info(f"Financial offline replay run_id={run_id}")
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": "csi1800",
        "target_date": range_end,
        "range_start": range_start,
    })
    resume_proof = audit.validate_resume_run(
        resume_from_run_id=source_run_id,
        expected_entrypoint="scripts/data_sync.py",
        universe="csi1800",
        target_date=range_end,
        range_start=range_start,
        expected_receipt_sha256=source_receipt_sha256,
    )
    audit.append_event(run_id, "financial_replay_started", {
        "contract": FINANCIAL_REPLAY_CONTRACT,
        "source_run_id": source_run_id,
        "source_receipt_sha256": source_receipt_sha256,
        "trusted_base_run_id": trusted_base_run_id,
        "trusted_base_receipt_sha256": trusted_base_receipt_sha256,
        "symbol_count": len(symbols),
        "symbols_sha256": stable_scope_hash(symbols),
        "network_policy": "local_reuse_only",
        "recovered_abandoned_run_ids": recovered_runs,
        "readback_mutation_run_id": readback_mutation_run_id,
        "readback_mutation_receipt_sha256": (
            readback_mutation_receipt_sha256
        ),
    })
    if readback_mutation_proof is not None:
        audit.append_event(run_id, "financial_replay_readback_mutation_source", {
            **readback_mutation_proof,
            "mutation_count": len(
                audit.changed_mutations(str(readback_mutation_run_id))
            ),
        })

    collector = TushareCollector()
    # Publication/event columns are internal to the as-of projection and are
    # intentionally removed by ``_merge_financials``.  Only consumed canonical
    # value columns cross this boundary.
    fields = sorted(set(collector.financial_cols))
    batches = [symbols[index:index + batch_size] for index in range(0, len(symbols), batch_size)]
    completed_scope_ids: list[str] = []
    mutation_count = 0
    changed_symbols: set[str] = set()
    _atomic_json(progress_path, {
        "schema_version": 1,
        "run_id": run_id,
        "stage": "canonical_replay",
        "status": "running",
        "completed_batches": 0,
        "total_batches": len(batches),
        "mutation_count": 0,
        "changed_symbol_count": 0,
    })
    for batch_number, batch in enumerate(batches, start=1):
        log.info(
            f"Financial offline replay batch {batch_number}/{len(batches)} "
            f"({len(batch)} symbols)"
        )
        before_receipts = set(audit.fetch_receipt_ids(run_id))
        financial = collector._fetch_financials_batch(
            ",".join(batch),
            range_start,
            range_end,
            run_id=run_id,
            audit_store=audit,
            resume_proof=resume_proof,
            scope_key="csi1800",
            universe="csi1800",
            local_max_workers=local_workers,
            local_reuse_only=True,
        )
        if financial is None or financial.empty:
            raise RuntimeError(f"financial offline replay returned no events for batch {batch_number}")
        raw_receipts = [
            item for item in audit.fetch_receipt_ids(run_id) if item not in before_receipts
        ]
        bundle_receipt = audit.record_fetch(
            run_id=run_id,
            source="tushare",
            endpoint="financial_replay_bundle",
            contract_version="1",
            status="success",
            requested_scope={
                "date_start": range_start,
                "date_end": range_end,
                "symbol_count": len(batch),
                "symbols": sorted(batch),
                "symbols_sha256": stable_scope_hash(batch),
                "processing_contract": FINANCIAL_REPLAY_CONTRACT,
                "source_receipt_count": len(raw_receipts),
                "source_receipts_sha256": hashlib.sha256(
                    _json_bytes(sorted(raw_receipts))
                ).hexdigest(),
            },
            returned_rows=len(financial),
            attempt_count=1,
            payload_kind="derived",
            published_at=None,
            **normalized_response_metadata(financial),
        )
        for code in batch:
            existing = store.load_daily(code)
            if existing is None or existing.empty:
                raise RuntimeError(f"financial replay canonical source is missing: {code}")
            dates = (
                existing["trade_date"].astype("string").str.replace("-", "", regex=False).str[:8]
            )
            scope = existing.loc[dates.between(range_start, range_end)].copy()
            if scope.empty:
                raise RuntimeError(f"financial replay canonical scope is empty: {code}")
            scope = scope.drop(columns=fields, errors="ignore")
            projected = collector._merge_financials(scope, financial)
            mutation = store.replace_daily_projection(
                projected,
                code,
                fields=fields,
                date_start=range_start,
                date_end=range_end,
                fetch_receipt_id=bundle_receipt,
                before_commit=lambda item: audit.append_event(
                    run_id, "financial_replay_mutation_intent", item
                ),
            )
            if mutation is None:
                continue
            audit.record_mutations(run_id=run_id, mutations=[mutation])
            audit.append_event(run_id, "financial_replay_mutation_committed", {
                "symbol": code,
                "after_hash": mutation["after_hash"],
            })
            mutation_count += 1
            changed_symbols.add(code)

        scope_identity = history_scope_identity(
            source="tushare",
            scope_key="csi1800",
            universe="csi1800",
            range_start=range_start,
            range_end=range_end,
            symbols=batch,
            processing_contract=FINANCIAL_REPLAY_CONTRACT,
        )
        semantic = canonical_history_scope_semantic_identity(
            store.canonical_dir,
            batch,
            range_start=range_start,
            range_end=range_end,
            fields=fields,
            max_workers=local_workers,
        )
        audit.record_history_scope_completed(
            run_id=run_id,
            identity=scope_identity,
            canonical_scope_sha256=canonical_symbol_files_sha256(
                store.canonical_dir, batch, max_workers=local_workers
            ),
            receipt_ids=[*raw_receipts, bundle_receipt],
            canonical_semantic_identity=semantic,
        )
        completed_scope_ids.append(str(scope_identity["scope_id"]))
        _atomic_json(progress_path, {
            "schema_version": 1,
            "run_id": run_id,
            "stage": "canonical_replay",
            "status": "running",
            "completed_batches": batch_number,
            "total_batches": len(batches),
            "mutation_count": mutation_count,
            "changed_symbol_count": len(changed_symbols),
        })

    coverage = {
        "status": "success",
        "expected_scope_count": len(batches),
        "completed_scope_ids": completed_scope_ids,
        "inherited_scope_ids": [],
    }
    scope_check = audit.evaluate_history_scope_checkpoints(run_id=run_id, coverage=coverage)
    financial_field_endpoints = {
        field: endpoint
        for field, endpoint in HISTORY_FIELD_ENDPOINTS.items()
        if endpoint in {"income", "balancesheet", "cashflow", "fina_indicator"}
    }
    field_check = audit.evaluate_history_field_receipts(
        run_id=run_id,
        dataset="canonical_daily",
        field_endpoints=financial_field_endpoints,
    )
    payload_check = audit.verify_payloads(run_id)

    adapter = QlibAdapter()
    _atomic_json(progress_path, {
        "schema_version": 1,
        "run_id": run_id,
        "stage": "qlib_refresh_readback",
        "status": "running",
        "completed_batches": len(batches),
        "total_batches": len(batches),
        "mutation_count": mutation_count,
        "changed_symbol_count": len(changed_symbols),
    })
    adapter.init_qlib()
    from scripts.ops.sync_csi800_daily import _refresh_and_verify_history_mutation_store

    qlib_mutation_run_ids = [str(readback_mutation_run_id or run_id)]
    qlib_readback = _refresh_and_verify_history_mutation_store(
        adapter,
        store,
        audit,
        qlib_mutation_run_ids,
        apply=True,
        require_pit_industry=True,
        pit_industry_until_date=range_end,
        qlib_max_workers=qlib_workers,
    )
    audit.append_event(run_id, "qlib_readback", qlib_readback)
    audit.append_event(run_id, "outer_readiness", {
        "status": "success" if qlib_readback.get("status") == "success" else "failed",
        "contract": FINANCIAL_REPLAY_CONTRACT,
    })
    gates = {
        "fetch": scope_check.get("status") == "success" and field_check.get("status") == "success",
        "raw_payloads": payload_check.get("status") == "success",
        "canonical_commit": len(completed_scope_ids) == len(batches),
        "qlib_readback": qlib_readback.get("status") == "success",
        "readiness": qlib_readback.get("status") == "success",
        "contiguous_range": True,
    }
    if set(gates) != set(REQUIRED_TERMINAL_GATES):
        raise RuntimeError("financial replay terminal gate schema mismatch")
    terminal = audit.finalize_run(
        run_id=run_id,
        source="tushare",
        scope_key="csi1800",
        range_start=range_start,
        range_end=range_end,
        fields=fields,
        gates=gates,
        receipt_root=data_root / "audit" / "source_runs",
        trust_state="trusted",
        allow_initial_history=True,
        field_range_starts={field: range_start for field in fields},
    )
    if terminal.get("trust_state") != "trusted":
        raise RuntimeError(f"financial replay terminal did not become trusted: {terminal}")
    terminal_path = Path(str(terminal["receipt_path"]))
    terminal_sha = str(terminal["terminal_receipt_sha256"])
    identity = {
        "schema": FINANCIAL_REPLAY_SCHEMA,
        "contract": FINANCIAL_REPLAY_CONTRACT,
        "run_id": run_id,
        "terminal_receipt_sha256": terminal_sha,
        "source_run_id": source_run_id,
        "source_receipt_sha256": source_receipt_sha256,
        "trusted_base_run_id": trusted_base_run_id,
        "trusted_base_receipt_sha256": trusted_base_receipt_sha256,
        "canonical_mutation_run_ids": qlib_mutation_run_ids,
        "canonical_mutation_terminal_receipt_sha256": (
            readback_mutation_receipt_sha256
        ),
        "scope_key": "csi1800",
        "range_start": range_start,
        "range_end": range_end,
        "symbol_count": len(symbols),
        "symbols_sha256": stable_scope_hash(symbols),
    }
    artifact = _atomic_manifest(
        Path(output_root).resolve(),
        identity,
        {
            "terminal_receipt_path": terminal_path.relative_to(data_root).as_posix(),
            "terminal_gates": gates,
            "source_terminal_trust_state": source_terminal.get("trust_state"),
            "scope_check": scope_check,
            "field_check": field_check,
            "payload_check": payload_check,
            "qlib_readback": qlib_readback,
            "mutation_count": mutation_count,
            "changed_symbol_count": len(changed_symbols),
            "canonical_mutation_run_ids": qlib_mutation_run_ids,
            "changed_symbols_sha256": stable_scope_hash(changed_symbols),
        },
    )
    _atomic_json(progress_path, {
        "schema_version": 1,
        "run_id": run_id,
        "stage": "complete",
        "status": "success",
        "completed_batches": len(batches),
        "total_batches": len(batches),
        "mutation_count": mutation_count,
        "changed_symbol_count": len(changed_symbols),
        "terminal_receipt_sha256": terminal_sha,
        **artifact,
    })
    return {
        "status": "success",
        "run_id": run_id,
        "terminal_receipt_sha256": terminal_sha,
        "mutation_count": mutation_count,
        "changed_symbol_count": len(changed_symbols),
        **artifact,
    }
