from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from qsys.data.source_audit import (
    LEGACY_UNTRUSTED,
    SourceAuditStore,
    build_canonical_mutations,
    checkpoint_requested_scope,
    fetch_checkpoint_key,
    normalized_response_metadata,
    stable_scope_hash,
    data_writer_lock,
    validate_run_id,
)
from qsys.data.storage import StockDataStore


def _row(close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260821"],
            "open": [10.0],
            "close": [close],
        }
    )


def _record_fetch(store: SourceAuditStore, *, run_id: str, status: str, scope: dict, error=None) -> None:
    frame = _row(11.0) if status in {"success", "partial"} else pd.DataFrame()
    metadata = normalized_response_metadata(frame)
    store.record_fetch(
        run_id=run_id,
        source="tushare",
        endpoint="daily",
        status=status,
        requested_scope=scope,
        returned_rows=len(frame),
        attempt_count=1,
        payload_frame=frame if status in {"success", "partial"} else None,
        published_at=None,
        error=error,
        **metadata,
    )


def _resume_scope(
    endpoint: str,
    *,
    symbols: tuple[str, ...] = ("000001.SZ",),
    source: str = "tushare",
    contract_version: str = "1",
    scope_key: str = "csi1800",
    universe: str = "csi1800",
    date_start: str = "20260821",
    date_end: str = "20260821",
) -> dict:
    return checkpoint_requested_scope(
        {
            "date_start": date_start, "date_end": date_end,
            "symbol_count": len(symbols),
            "symbols_sha256": stable_scope_hash(symbols),
        },
        source=source, endpoint=endpoint, contract_version=contract_version,
        scope_key=scope_key, universe=universe,
    )


def test_mutation_hashes_only_exact_affected_window() -> None:
    before = pd.concat(
        [
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20260820"],
                    "open": [8.0],
                    "close": [9.0],
                }
            ),
            _row(11.0),
        ],
        ignore_index=True,
    )
    incoming = _row(12.0)
    after = pd.concat([before.iloc[:1], incoming], ignore_index=True)

    receipts = build_canonical_mutations(
        symbol="000001.SZ", incoming=incoming, before=before, after=after
    )

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["date_start"] == receipt["date_end"] == "20260821"
    assert receipt["fields"] == ["close"]
    assert receipt["mutation_type"] == "update"
    assert receipt["before_hash"] != receipt["after_hash"]
    assert "11.0" not in json.dumps(receipt)
    assert "12.0" not in json.dumps(receipt)


def test_noop_has_equal_hash_and_no_affected_fields() -> None:
    frame = _row(11.0)
    receipt = build_canonical_mutations(
        symbol="000001.SZ", incoming=frame, before=frame, after=frame
    )[0]
    assert receipt["mutation_type"] == "noop"
    assert receipt["fields"] == []
    assert receipt["before_hash"] == receipt["after_hash"]


def test_mutation_builder_iterates_only_incoming_date_window(monkeypatch) -> None:
    dates = pd.date_range("2000-01-01", periods=5000, freq="D").strftime("%Y%m%d")
    before = pd.DataFrame({"trade_date": dates, "close": range(5000)})
    incoming = pd.DataFrame({"trade_date": [dates[-1]], "close": [9999]})
    after = before.copy()
    after.loc[after.index[-1], "close"] = 9999
    original = pd.DataFrame.iterrows
    iterated_rows = 0

    def counted(self):
        nonlocal iterated_rows
        iterated_rows += len(self)
        return original(self)

    monkeypatch.setattr(pd.DataFrame, "iterrows", counted)
    receipts = build_canonical_mutations(
        symbol="000001.SZ", incoming=incoming, before=before, after=after
    )
    assert receipts[0]["mutation_type"] == "update"
    assert iterated_rows == 2


def test_storage_returns_same_key_revision_receipt(tmp_path: Path) -> None:
    store = StockDataStore.__new__(StockDataStore)
    store.canonical_dir = tmp_path / "canonical"
    store.canonical_dir.mkdir()
    store.meta_db_path = tmp_path / "meta.db"
    store._init_db()

    inserted = store.save_daily(_row(11.0), "000001.SZ")
    revised = store.save_daily(_row(12.0), "000001.SZ")
    noop = store.save_daily(_row(12.0), "000001.SZ")

    assert inserted[0]["mutation_type"] == "insert"
    assert {"close", "open"}.issubset(inserted[0]["fields"])
    assert revised[0]["mutation_type"] == "update"
    assert revised[0]["fields"] == ["close"]
    assert noop[0]["mutation_type"] == "noop"
    saved = store.load_daily("000001.SZ")
    assert saved is not None
    assert saved.loc[saved["trade_date"].astype(str) == "20260821", "close"].item() == 12.0


def test_storage_receipt_uses_post_cleaner_canonical_fields(tmp_path: Path) -> None:
    store = StockDataStore.__new__(StockDataStore)
    store.canonical_dir = tmp_path / "canonical"
    store.canonical_dir.mkdir()
    store.meta_db_path = tmp_path / "meta.db"
    store._init_db()
    dirty = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260821"],
            "close_x": [11.0],
            "close_y": [None],
        }
    )

    receipt = store.save_daily(dirty, "000001.SZ")[0]

    assert "close" in receipt["fields"]
    assert "close_x" not in receipt["fields"]
    assert "close_y" not in receipt["fields"]


def test_storage_receipt_canonicalizes_dirty_existing_frame_before_hash(tmp_path: Path) -> None:
    store = StockDataStore.__new__(StockDataStore)
    store.canonical_dir = tmp_path / "canonical"
    store.canonical_dir.mkdir()
    store.meta_db_path = tmp_path / "meta.db"
    store._init_db()
    dirty_old = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260821"],
            "close_x": [11.0],
            "close_y": [None],
        }
    )
    dirty_old.to_feather(store.canonical_dir / "000001.SZ.feather")

    receipt = store.save_daily(_row(12.0), "000001.SZ")[0]

    assert receipt["mutation_type"] == "update"
    assert "close" in receipt["fields"]
    assert not ({"close_x", "close_y"} & set(receipt["fields"]))


def test_fetch_statuses_are_append_only_and_secrets_are_redacted(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    for status in ("success", "empty", "partial", "failure"):
        _record_fetch(
            store,
            run_id="same-day-run-a",
            status=status,
            scope={"token": "very-secret", "url": "https://x.test/?api_key=also-secret"},
            error="authorization=leaked" if status == "failure" else None,
        )

    raw = (tmp_path / "audit.db").read_bytes()
    assert b"very-secret" not in raw
    assert b"also-secret" not in raw
    assert b"leaked" not in raw

    with sqlite3.connect(tmp_path / "audit.db") as conn:
        receipt_id = conn.execute("SELECT receipt_id FROM fetch_receipts LIMIT 1").fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE fetch_receipts SET status='empty' WHERE receipt_id=?", (receipt_id,))


def test_raw_supplier_payload_linkage_is_immutable_and_tamper_detected(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    _record_fetch(store, run_id="payload-run", status="success", scope={"date": "20260821"})
    with sqlite3.connect(tmp_path / "audit.db") as conn:
        first_path, first_hash = conn.execute(
            "SELECT payload_path,payload_sha256 FROM fetch_receipts WHERE run_id='payload-run'"
        ).fetchone()
    payload = tmp_path / first_path
    original = payload.read_bytes()
    assert first_hash
    assert store.verify_payloads("payload-run")["status"] == "success"

    _record_fetch(store, run_id="payload-run", status="success", scope={"date": "20260821"})
    with sqlite3.connect(tmp_path / "audit.db") as conn:
        paths = [row[0] for row in conn.execute(
            "SELECT payload_path FROM fetch_receipts WHERE run_id='payload-run' ORDER BY rowid"
        ).fetchall()]
    assert len(paths) == 2 and paths[0] != paths[1]
    assert payload.read_bytes() == original

    payload.write_bytes(original + b"tampered")
    verification = store.verify_payloads("payload-run")
    assert verification["status"] == "failed"
    assert verification["failures"][0]["reason"] == "payload_hash_mismatch"


def test_fetch_checkpoint_key_is_deterministic_and_scope_sensitive() -> None:
    kwargs = {
        "source": "tushare", "endpoint": "daily", "contract_version": "1",
        "scope_key": "csi1800", "universe": "csi1800",
        "date_start": "2026-08-21", "date_end": "20260821",
        "symbols_sha256": stable_scope_hash(["B.SZ", "A.SZ"]),
    }
    first = fetch_checkpoint_key(**kwargs)
    second = fetch_checkpoint_key(**{**kwargs, "symbols_sha256": stable_scope_hash(["A.SZ", "B.SZ"])})
    assert first == second
    assert first != fetch_checkpoint_key(**{**kwargs, "endpoint": "adj_factor"})
    assert first != fetch_checkpoint_key(**{**kwargs, "universe": "csi800"})
    assert first != fetch_checkpoint_key(**{**kwargs, "request_variant": "actual_date"})
    query_sha = "a" * 64
    assert first != fetch_checkpoint_key(**{**kwargs, "request_sha256": query_sha})
    assert fetch_checkpoint_key(**{**kwargs, "request_sha256": query_sha}) == (
        fetch_checkpoint_key(**{**kwargs, "request_sha256": query_sha})
    )
    with pytest.raises(ValueError, match="invalid request_sha256"):
        fetch_checkpoint_key(**{**kwargs, "request_sha256": "not-a-sha"})
    assert fetch_checkpoint_key(**{**kwargs, "request_variant": "actual_date"}) != (
        fetch_checkpoint_key(**{**kwargs, "request_variant": "ann_date"})
    )
    scope = _resume_scope("daily")
    assert scope["checkpoint_key"] == fetch_checkpoint_key(
        source="tushare", endpoint="daily", contract_version="1",
        scope_key="csi1800", universe="csi1800", date_start="20260821",
        date_end="20260821", symbols_sha256=stable_scope_hash(["000001.SZ"]),
    )


def test_verified_success_and_observed_empty_clone_into_fresh_run(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    old_run = "failed-run"
    store.append_event(old_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    success_scope = _resume_scope("daily")
    success_frame = _row(11.0)
    success_id = store.record_fetch(
        run_id=old_run, source="tushare", endpoint="daily", status="success",
        requested_scope=success_scope, returned_rows=1, attempt_count=2,
        payload_frame=success_frame, observed_at="2026-08-21T10:00:00Z",
        published_at="2026-08-21T09:59:00Z",
        **normalized_response_metadata(success_frame),
    )
    store.record_field_receipt_links(
        run_id=old_run, receipt_id=success_id, fields=["close"]
    )
    empty_scope = _resume_scope("moneyflow")
    empty_frame = pd.DataFrame(columns=["ts_code", "trade_date"])
    empty_id = store.record_fetch(
        run_id=old_run, source="tushare", endpoint="moneyflow", status="empty",
        requested_scope=empty_scope, returned_rows=0, attempt_count=1,
        observed_at="2026-08-21T10:01:00Z", published_at=None,
        **normalized_response_metadata(empty_frame),
    )
    crash = store.record_crash_receipt(
        run_id=old_run, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="after moneyflow",
    )
    old_receipt = Path(crash["receipt_path"])
    old_bytes = old_receipt.read_bytes()
    with sqlite3.connect(audit_root / "audit.db") as conn:
        old_counts = tuple(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (old_run,)
        ).fetchone()[0] for table in (
            "fetch_receipts", "field_receipt_links", "audit_journal",
        ))
    lineage = store.validate_resume_run(
        resume_from_run_id=old_run, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    assert lineage["receipt_sha256"]

    new_run = "fresh-run"
    store.append_event(new_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    reused_success = store.reuse_fetch_shard(
        run_id=new_run, resume_proof=lineage, source="tushare",
        endpoint="daily", contract_version="1", requested_scope=success_scope,
    )
    reused_empty = store.reuse_fetch_shard(
        run_id=new_run, resume_proof=lineage, source="tushare",
        endpoint="moneyflow", contract_version="1", requested_scope=empty_scope,
    )

    assert reused_success is not None
    pd.testing.assert_frame_equal(reused_success["frame"], success_frame)
    assert reused_empty is not None and reused_empty["status"] == "empty"
    assert reused_empty["frame"].empty
    with sqlite3.connect(audit_root / "audit.db") as conn:
        old_metadata = conn.execute(
            "SELECT observed_at,published_at FROM fetch_receipts WHERE receipt_id=?",
            (success_id,),
        ).fetchone()
        new_metadata = conn.execute(
            "SELECT observed_at,published_at FROM fetch_receipts WHERE receipt_id=?",
            (reused_success["receipt_id"],),
        ).fetchone()
        new_empty_metadata = conn.execute(
            "SELECT observed_at,published_at,returned_rows,payload_path,payload_sha256 "
            "FROM fetch_receipts WHERE receipt_id=?",
            (reused_empty["receipt_id"],),
        ).fetchone()
        links = conn.execute(
            "SELECT run_id,field_name FROM field_receipt_links WHERE receipt_id=?",
            (reused_success["receipt_id"],),
        ).fetchall()
        new_old_counts = tuple(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (old_run,)
        ).fetchone()[0] for table in (
            "fetch_receipts", "field_receipt_links", "audit_journal",
        ))
    assert tuple(old_metadata) == tuple(new_metadata) == (
        "2026-08-21T10:00:00Z", "2026-08-21T09:59:00Z",
    )
    assert tuple(new_empty_metadata) == (
        "2026-08-21T10:01:00Z", None, 0, None, None,
    )
    assert links == [(new_run, "close")]
    reuse_events = [
        event for event in store.run_evidence_summary(new_run)["events"]
        if event["event_type"] == "fetch_shard_reused"
    ]
    assert len(reuse_events) == 2
    assert reuse_events[0]["payload"]["resume_from_run_id"] == old_run
    assert old_receipt.read_bytes() == old_bytes
    assert new_old_counts == old_counts

    forged = dict(lineage)
    forged["receipt_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="resume proof"):
        store.reuse_fetch_shard(
            run_id=new_run, resume_proof=forged, source="tushare",
            endpoint="daily", contract_version="1", requested_scope=success_scope,
        )

    atomic_run = "atomic-fresh"
    store.append_event(atomic_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    with sqlite3.connect(audit_root / "audit.db") as conn:
        conn.executescript(
            """CREATE TRIGGER test_abort_reused_link
               BEFORE INSERT ON field_receipt_links
               WHEN NEW.run_id='atomic-fresh'
               BEGIN SELECT RAISE(ABORT, 'injected link failure'); END;"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected link failure"):
        store.reuse_fetch_shard(
            run_id=atomic_run, resume_proof=lineage, source="tushare",
            endpoint="daily", contract_version="1", requested_scope=success_scope,
        )
    atomic_summary = store.run_evidence_summary(atomic_run)
    assert atomic_summary["fetch_statuses"] == []
    assert [event["event_type"] for event in atomic_summary["events"]] == ["run_started"]

    other_store = SourceAuditStore(audit_root / "audit.db")
    with pytest.raises(ValueError, match="not validated by this audit store"):
        other_store.reuse_fetch_shard(
            run_id=new_run, resume_proof=lineage, source="tushare",
            endpoint="daily", contract_version="1", requested_scope=success_scope,
        )


def test_historical_resume_requires_exact_range_start(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = "history-failed"
    store.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": "csi1800",
        "target_date": "20260821",
        "range_start": "20140313",
    })
    store.record_crash_receipt(
        run_id=run_id, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="injected",
    )

    proof = store.validate_resume_run(
        resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821", range_start="20140313",
    )
    assert proof["range_start"] == "20140313"
    with pytest.raises(ValueError, match="lineage mismatch"):
        store.validate_resume_run(
            resume_from_run_id=run_id,
            expected_entrypoint="scripts/data_sync.py",
            universe="csi1800",
            target_date="20260821",
            range_start="20140314",
        )
    with pytest.raises(ValueError, match="lineage mismatch"):
        store.validate_resume_run(
            resume_from_run_id=run_id,
            expected_entrypoint="scripts/data_sync.py",
            universe="csi1800",
            target_date="20260821",
        )


def test_interrupted_run_without_terminal_can_be_sealed_then_resumed(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = "history-interrupted"
    store.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821", "range_start": "20140313",
    })
    _record_fetch(
        store, run_id=run_id, status="success",
        scope=_resume_scope("daily"),
    )

    store.seal_interrupted_run_for_resume(
        run_id=run_id,
        expected_entrypoint="scripts/data_sync.py",
        universe="csi1800",
        target_date="20260821",
        range_start="20140313",
    )

    receipt = audit_root / "source_runs" / run_id / "receipt.json"
    terminal = json.loads(receipt.read_text(encoding="utf-8"))
    assert terminal["trust_state"] == "untrusted"
    assert any(
        row["event_type"] == "crash"
        and row["payload"].get("error") == "interrupted_without_terminal_receipt"
        for row in terminal["audit_journal"]
    )
    proof = store.validate_resume_run(
        resume_from_run_id=run_id,
        expected_entrypoint="scripts/data_sync.py",
        universe="csi1800",
        target_date="20260821",
        range_start="20140313",
    )
    assert proof["resume_from_run_id"] == run_id


@pytest.mark.parametrize(
    "tamper,match",
    [
        ("observed_at", "fetch row does not match SQLite"),
        ("run_started_universe", "run_started lineage mismatch"),
        ("run_started_target", "run_started lineage mismatch"),
        ("field_link", "field link does not match SQLite"),
    ],
)
def test_terminal_snapshot_rows_must_exactly_match_sqlite(
    tmp_path: Path, tamper: str, match: str,
) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = f"terminal-db-{tamper}"
    store.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    scope = _resume_scope("daily")
    frame = _row(11.0)
    receipt_id = store.record_fetch(
        run_id=run_id, source="tushare", endpoint="daily", status="success",
        requested_scope=scope, returned_rows=1, attempt_count=1,
        payload_frame=frame, observed_at="2026-08-21T10:00:00Z",
        **normalized_response_metadata(frame),
    )
    store.record_field_receipt_links(
        run_id=run_id, receipt_id=receipt_id, fields=["close"]
    )
    result = store.record_crash_receipt(
        run_id=run_id, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="injected",
    )
    receipt_path = Path(result["receipt_path"])
    terminal = json.loads(receipt_path.read_text())
    if tamper == "observed_at":
        terminal["fetch_receipts"][0]["observed_at"] = "2099-01-01T00:00:00Z"
    elif tamper == "run_started_universe":
        terminal["audit_journal"][0]["payload"]["universe"] = "csi800"
    elif tamper == "run_started_target":
        terminal["audit_journal"][0]["payload"]["target_date"] = "20260820"
    else:
        terminal["field_receipt_links"][0]["dataset"] = "forged_dataset"
    receipt_path.write_text(json.dumps(terminal, sort_keys=True))

    with pytest.raises(ValueError, match=match):
        store.validate_resume_run(
            resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )


@pytest.mark.parametrize("forge_marker", [False, True])
def test_bare_or_forged_untrusted_terminal_is_not_a_resume_source(
    tmp_path: Path, forge_marker: bool,
) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = "forged-marker" if forge_marker else "bare-untrusted"
    store.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    frame = _row(11.0)
    store.record_fetch(
        run_id=run_id, source="tushare", endpoint="daily", status="success",
        requested_scope=_resume_scope("daily"), returned_rows=1, attempt_count=1,
        payload_frame=frame, **normalized_response_metadata(frame),
    )
    receipt_path = store.export_receipt(
        run_id,
        audit_root / "source_runs",
        trust_state="untrusted",
        gates={
            "fetch": False, "raw_payloads": False, "canonical_commit": False,
            "qlib_readback": False, "readiness": False, "contiguous_range": False,
        },
    )
    if forge_marker:
        terminal = json.loads(receipt_path.read_text())
        terminal["audit_journal"].append({
            "seq": 999999,
            "run_id": run_id,
            "event_type": "crash",
            "payload": {"error": "forged"},
            "created_at": "2099-01-01T00:00:00Z",
        })
        receipt_path.write_text(json.dumps(terminal, sort_keys=True))

    with pytest.raises(ValueError, match="terminal failure marker"):
        store.validate_resume_run(
            resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )


def test_terminal_receipt_summarizes_mutations_without_embedding_rows(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = "compact-mutations"
    store.append_event(run_id, "run_started", {"entrypoint": "test"})
    store.record_mutations(
        run_id=run_id,
        mutations=[
            {
                "symbol": "000001.SZ",
                "date_start": "20260821",
                "date_end": "20260821",
                "fields": ["close"],
                "mutation_type": mutation_type,
                "before_hash": "before",
                "after_hash": "after",
            }
            for mutation_type in ("insert", "update")
        ],
    )

    receipt_path = store.export_receipt(
        run_id, audit_root / "source_runs", trust_state="untrusted", gates={}
    )
    receipt = json.loads(receipt_path.read_text())

    assert receipt["canonical_mutations"] == []
    assert receipt["canonical_mutation_summary"] == {
        "count": 2,
        "counts_by_type": {"insert": 1, "update": 1},
        "storage": "audit.db:canonical_mutations",
    }
    assert store.changed_mutation_symbols(run_id) == ["000001.SZ"]
    assert len(store.changed_mutations(run_id, symbol="000001.SZ")) == 2


def test_resume_lineage_returns_all_mutation_runs_oldest_first(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit" / "audit.db")
    for run_id in ("first-run", "second-run", "third-run"):
        store.append_event(run_id, "run_started", {"entrypoint": "test"})
    store.append_event(
        "second-run", "resume_from_run", {"resume_from_run_id": "first-run"}
    )
    store.append_event(
        "third-run", "resume_from_run", {"resume_from_run_id": "second-run"}
    )

    assert store.resume_lineage_run_ids("third-run") == [
        "first-run", "second-run", "third-run"
    ]


def test_post_terminal_db_failure_marker_does_not_retroactively_validate_snapshot(
    tmp_path: Path,
) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = "post-terminal-marker"
    store.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    store.export_receipt(
        run_id,
        audit_root / "source_runs",
        trust_state="untrusted",
        gates={},
    )
    store.append_event(run_id, "crash", {"error": "too late for snapshot"})
    with pytest.raises(ValueError, match="terminal failure marker"):
        store.validate_resume_run(
            resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )


def test_resume_terminal_is_parsed_once_and_candidate_lookup_is_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    old_run = "indexed-old"
    store.append_event(old_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    scopes = {}
    empty = pd.DataFrame(columns=["ts_code", "trade_date"])
    metadata = normalized_response_metadata(empty)
    for index in range(40):
        endpoint = f"empty_{index}"
        scope = _resume_scope(endpoint)
        scopes[endpoint] = scope
        store.record_fetch(
            run_id=old_run, source="tushare", endpoint=endpoint, status="empty",
            requested_scope=scope, returned_rows=0, attempt_count=1, **metadata,
        )
    result = store.record_crash_receipt(
        run_id=old_run, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="after empty shards",
    )
    receipt_path = Path(result["receipt_path"])
    original_read_bytes = Path.read_bytes
    receipt_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal receipt_reads
        if path == receipt_path:
            receipt_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    proof = store.validate_resume_run(
        resume_from_run_id=old_run, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    new_run = "indexed-new"
    store.append_event(new_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })

    class CountingIndex(dict):
        lookups = 0

        def get(self, key, default=None):
            self.lookups += 1
            return super().get(key, default)

    cache = store._validated_resume_cache[(old_run, proof["receipt_sha256"])]
    index = CountingIndex(cache["receipt_index"])
    cache["receipt_index"] = index
    for endpoint, scope in scopes.items():
        assert store.reuse_fetch_shard(
            run_id=new_run, resume_proof=proof, source="tushare",
            endpoint=endpoint, contract_version="1", requested_scope=scope,
        ) is not None
    assert receipt_reads == 1
    assert index.lookups == len(scopes)


def test_unverifiable_fetch_shards_are_not_reused(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit" / "audit.db")
    old_run = "failed-unverified"
    store.append_event(old_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    endpoints = {
        "partial": "partial", "failure": "failure",
        "tampered": "success", "missing": "success",
        "malformed": "success", "bad_rows": "success",
        "bad_columns": "success", "bad_empty": "empty",
        "bad_empty_rows": "empty",
    }
    paths: dict[str, Path] = {}
    for endpoint, status in endpoints.items():
        frame = _row(11.0) if status in {"success", "partial"} else pd.DataFrame()
        metadata = normalized_response_metadata(frame)
        if endpoint == "malformed":
            metadata["response_hash"] = "0" * 64
        if endpoint == "bad_columns":
            metadata["response_columns"] = ["not", "the", "payload", "schema"]
        if endpoint == "bad_empty":
            metadata["response_date_min"] = "20260821"
        receipt_id = store.record_fetch(
            run_id=old_run, source="tushare", endpoint=endpoint, status=status,
            requested_scope=_resume_scope(endpoint),
            returned_rows=(
                2 if endpoint == "bad_rows" else 1 if endpoint == "bad_empty_rows" else len(frame)
            ),
            attempt_count=1,
            payload_frame=frame if status in {"success", "partial"} else None,
            error="failed" if status == "failure" else None,
            **metadata,
        )
        if status == "success":
            with sqlite3.connect(tmp_path / "audit" / "audit.db") as conn:
                relative = conn.execute(
                    "SELECT payload_path FROM fetch_receipts WHERE receipt_id=?", (receipt_id,)
                ).fetchone()[0]
            paths[endpoint] = tmp_path / relative
    store.record_crash_receipt(
        run_id=old_run, receipt_root=tmp_path / "audit" / "source_runs",
        entrypoint="scripts/data_sync.py", error="failed",
    )
    receipt_path = tmp_path / "audit" / "source_runs" / old_run / "receipt.json"
    paths["tampered"].write_bytes(paths["tampered"].read_bytes() + b"tampered")
    paths["missing"].unlink()
    proof = store.validate_resume_run(
        resume_from_run_id=old_run, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    store.append_event("fresh-unverified", "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    for endpoint in endpoints:
        assert store.reuse_fetch_shard(
            run_id="fresh-unverified", resume_proof=proof,
            source="tushare", endpoint=endpoint, contract_version="1",
            requested_scope=_resume_scope(endpoint),
        ) is None


def test_resume_uses_only_terminal_snapshot_and_rejects_unsafe_payload_path(
    tmp_path: Path,
) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    old_run = "terminal-snapshot"
    lineage_payload = {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    }
    store.append_event(old_run, "run_started", lineage_payload)
    store.record_crash_receipt(
        run_id=old_run, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="failed before daily",
    )
    # A durable row appended after the terminal snapshot is not part of the
    # explicit failed run receipt and therefore cannot be resumed from it.
    frame = _row(11.0)
    late_scope = _resume_scope("daily")
    store.record_fetch(
        run_id=old_run, source="tushare", endpoint="daily", status="success",
        requested_scope=late_scope, returned_rows=1, attempt_count=1,
        payload_frame=frame, **normalized_response_metadata(frame),
    )
    proof = store.validate_resume_run(
        resume_from_run_id=old_run, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    store.append_event("terminal-fresh", "run_started", lineage_payload)
    assert store.reuse_fetch_shard(
        run_id="terminal-fresh", resume_proof=proof, source="tushare",
        endpoint="daily", contract_version="1", requested_scope=late_scope,
    ) is None

    unsafe_run = "unsafe-payload"
    store.append_event(unsafe_run, "run_started", lineage_payload)
    unsafe_scope = _resume_scope("unsafe")
    store.record_fetch(
        run_id=unsafe_run, source="tushare", endpoint="unsafe", status="success",
        requested_scope=unsafe_scope, returned_rows=1, attempt_count=1,
        payload_frame=frame, **normalized_response_metadata(frame),
    )
    unsafe_result = store.record_crash_receipt(
        run_id=unsafe_run, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="unsafe evidence",
    )
    unsafe_path = Path(unsafe_result["receipt_path"])
    unsafe_receipt = json.loads(unsafe_path.read_text())
    unsafe_receipt["fetch_receipts"][0]["payload_path"] = "../escape.parquet"
    unsafe_path.write_text(json.dumps(unsafe_receipt, sort_keys=True))
    with pytest.raises(ValueError, match="fetch row does not match SQLite"):
        store.validate_resume_run(
            resume_from_run_id=unsafe_run, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )


def test_resume_validation_missing_and_malformed_receipts_fail_closed(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    kwargs = {
        "expected_entrypoint": "scripts/data_sync.py",
        "universe": "csi1800", "target_date": "20260821",
    }
    with pytest.raises(ValueError, match="receipt missing"):
        store.validate_resume_run(resume_from_run_id="missing-run", **kwargs)

    malformed_path = audit_root / "source_runs" / "malformed-run" / "receipt.json"
    malformed_path.parent.mkdir(parents=True)
    malformed_path.write_text("{not-json")
    with pytest.raises(ValueError, match="invalid JSON"):
        store.validate_resume_run(resume_from_run_id="malformed-run", **kwargs)


def test_resume_rejects_trusted_or_wrong_lineage_source_run(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    trusted = "trusted-run"
    store.append_event(trusted, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    result = store.finalize_run(
        run_id=trusted, source="tushare", scope_key="csi1800",
        range_start="20260821", range_end="20260821", fields=["close"],
        gates={name: True for name in (
            "fetch", "raw_payloads", "canonical_commit", "qlib_readback",
            "readiness", "contiguous_range",
        )},
        receipt_root=audit_root / "source_runs",
        previous_open_session="20260820",
    )
    assert result["trust_state"] == "trusted"
    with pytest.raises(ValueError, match="trusted terminal"):
        store.validate_resume_run(
            resume_from_run_id=trusted, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )
    trusted_receipt_path = Path(result["receipt_path"])
    tampered_trusted = json.loads(trusted_receipt_path.read_text())
    tampered_trusted["trust_state"] = "untrusted"
    trusted_receipt_path.write_text(json.dumps(tampered_trusted, sort_keys=True))
    with pytest.raises(ValueError, match="trusted terminal"):
        SourceAuditStore(audit_root / "audit.db").validate_resume_run(
            resume_from_run_id=trusted, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )

    trusted_unchanged = "trusted-unchanged-run"
    store.append_event(trusted_unchanged, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    unchanged_result = store.finalize_unchanged(
        run_id=trusted_unchanged,
        gates={
            "fetch": True, "raw_payloads": True, "canonical_commit": True,
            "qlib_readback": True, "readiness": True, "contiguous_range": True,
        },
        receipt_root=audit_root / "source_runs",
        prior_trusted=True,
    )
    unchanged_path = Path(unchanged_result["receipt_path"])
    unchanged_receipt = json.loads(unchanged_path.read_text())
    unchanged_receipt["trust_state"] = "untrusted"
    unchanged_path.write_text(json.dumps(unchanged_receipt, sort_keys=True))
    with pytest.raises(ValueError, match="trusted terminal"):
        SourceAuditStore(audit_root / "audit.db").validate_resume_run(
            resume_from_run_id=trusted_unchanged,
            expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )

    failed = "wrong-lineage"
    store.append_event(failed, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi800",
        "target_date": "20260820",
    })
    store.record_crash_receipt(
        run_id=failed, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="failed",
    )
    with pytest.raises(ValueError, match="run_started lineage mismatch"):
        store.validate_resume_run(
            resume_from_run_id=failed, expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )


@pytest.mark.parametrize("terminal_mode", ["failed_gates", "untrusted_unchanged"])
def test_genuine_untrusted_terminal_modes_remain_resumable(
    tmp_path: Path, terminal_mode: str,
) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    run_id = f"genuine-{terminal_mode}"
    store.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    if terminal_mode == "failed_gates":
        result = store.finalize_run(
            run_id=run_id, source="tushare", scope_key="csi1800",
            range_start="20260821", range_end="20260821", fields=["close"],
            gates={
                "fetch": False, "raw_payloads": False,
                "canonical_commit": False, "qlib_readback": False,
                "readiness": False, "contiguous_range": True,
            },
            receipt_root=audit_root / "source_runs",
            previous_open_session="20260820",
        )
    else:
        result = store.finalize_unchanged(
            run_id=run_id,
            gates={
                "fetch": False, "raw_payloads": False,
                "canonical_commit": False, "qlib_readback": False,
                "readiness": False, "contiguous_range": True,
            },
            receipt_root=audit_root / "source_runs",
            prior_trusted=False,
        )
    assert result["trust_state"] == "untrusted"
    proof = store.validate_resume_run(
        resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    assert proof["resume_from_run_id"] == run_id


def test_resume_lineage_skips_malformed_event_before_exact_valid_event(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    valid_after_bad = "valid-after-malformed"
    store.append_event(valid_after_bad, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": None,
    })
    store.append_event(valid_after_bad, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    store.record_crash_receipt(
        run_id=valid_after_bad, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="failed",
    )
    proof = store.validate_resume_run(
        resume_from_run_id=valid_after_bad,
        expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    assert proof["target_date"] == "20260821"

    malformed_only = "malformed-only"
    store.append_event(malformed_only, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
    })
    store.record_crash_receipt(
        run_id=malformed_only, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="failed",
    )
    with pytest.raises(ValueError, match="run_started lineage mismatch"):
        store.validate_resume_run(
            resume_from_run_id=malformed_only,
            expected_entrypoint="scripts/data_sync.py",
            universe="csi1800", target_date="20260821",
        )


def test_resume_requires_proof_current_lineage_and_exact_checkpoint_scope(tmp_path: Path) -> None:
    audit_root = tmp_path / "audit"
    store = SourceAuditStore(audit_root / "audit.db")
    old_run = "exact-source"
    store.append_event(old_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    frame = _row(11.0)
    exact_scope = _resume_scope("daily")
    store.record_fetch(
        run_id=old_run, source="tushare", endpoint="daily", status="success",
        requested_scope=exact_scope, returned_rows=1, attempt_count=1,
        payload_frame=frame, **normalized_response_metadata(frame),
    )
    store.record_crash_receipt(
        run_id=old_run, receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py", error="failed",
    )
    proof = store.validate_resume_run(
        resume_from_run_id=old_run, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date="20260821",
    )
    with pytest.raises(ValueError, match="current run_started lineage"):
        store.reuse_fetch_shard(
            run_id="no-current-lineage", resume_proof=proof, source="tushare",
            endpoint="daily", contract_version="1", requested_scope=exact_scope,
        )

    new_run = "exact-fresh"
    store.append_event(new_run, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": "20260821",
    })
    mismatches = [
        ("other", "daily", "1", _resume_scope("daily", source="other")),
        ("tushare", "adj_factor", "1", _resume_scope("adj_factor")),
        ("tushare", "daily", "2", _resume_scope("daily", contract_version="2")),
        ("tushare", "daily", "1", _resume_scope("daily", scope_key="other")),
        ("tushare", "daily", "1", _resume_scope("daily", universe="csi800")),
        ("tushare", "daily", "1", _resume_scope(
            "daily", date_start="20260820", date_end="20260820",
        )),
        ("tushare", "daily", "1", _resume_scope("daily", symbols=("000002.SZ",))),
    ]
    for source, endpoint, contract_version, scope in mismatches:
        assert store.reuse_fetch_shard(
            run_id=new_run, resume_proof=proof, source=source,
            endpoint=endpoint, contract_version=contract_version,
            requested_scope=scope,
        ) is None
    tampered_scope = dict(exact_scope)
    tampered_scope["checkpoint_key"] = "0" * 64
    with pytest.raises(ValueError, match="checkpoint_key mismatch"):
        store.reuse_fetch_shard(
            run_id=new_run, resume_proof=proof, source="tushare",
            endpoint="daily", contract_version="1", requested_scope=tampered_scope,
        )


def test_failed_terminal_gate_leaves_watermark_byte_for_byte_unchanged(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    _record_fetch(store, run_id="run-failed", status="failure", scope={"date": "20260821"})
    before = store.watermark_snapshot_bytes()
    result = store.finalize_run(
        run_id="run-failed",
        source="tushare",
        scope_key="csi1800",
        range_start="20260821",
        range_end="20260821",
        fields=["close"],
        gates={
            "fetch": False,
            "raw_payloads": False,
            "canonical_commit": False,
            "qlib_readback": False,
            "readiness": False,
            "contiguous_range": True,
        },
        receipt_root=tmp_path / "receipts",
    )
    after = store.watermark_snapshot_bytes()
    assert result["watermark_advanced"] is False
    assert before == after


def test_explicit_initial_history_can_seed_one_verified_range(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    _record_fetch(store, run_id="history-seed", status="success", scope={"range": "full"})
    result = store.finalize_run(
        run_id="history-seed", source="tushare", scope_key="csi1800",
        range_start="20140313", range_end="20260731", fields=["close"],
        gates={
            "fetch": True, "raw_payloads": True, "canonical_commit": True,
            "qlib_readback": True, "readiness": True, "contiguous_range": True,
        },
        receipt_root=tmp_path / "receipts",
        allow_initial_history=True,
    )
    assert result["watermark_advanced"] is True
    assert store.has_trusted_range(
        source="tushare", scope_key="csi1800",
        range_start="20140313", range_end="20260731", fields=["close"],
    )


def test_initial_history_can_add_only_previously_unseen_fields(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    receipt_root = tmp_path / "receipts"
    gates = {
        "fetch": True,
        "raw_payloads": True,
        "canonical_commit": True,
        "qlib_readback": True,
        "readiness": True,
        "contiguous_range": True,
    }
    _record_fetch(store, run_id="core-20", status="success", scope={"date": "20260820"})
    seeded = store.finalize_run(
        run_id="core-20", source="tushare", scope_key="csi1800",
        range_start="20260820", range_end="20260820", fields=["close"],
        gates=gates, receipt_root=receipt_root,
    )
    assert seeded["watermark_advanced"] is True
    before = store.watermark_snapshot_bytes()

    _record_fetch(store, run_id="new-field-denied", status="success", scope={"date": "20260821"})
    denied = store.finalize_run(
        run_id="new-field-denied", source="tushare", scope_key="csi1800",
        range_start="20260821", range_end="20260821",
        fields=["close", "holder_num"], gates=gates,
        receipt_root=receipt_root, previous_open_session="20260820",
    )
    assert denied["watermark_advanced"] is False
    assert store.watermark_snapshot_bytes() == before

    _record_fetch(store, run_id="new-field-verified", status="success", scope={"date": "20260821"})
    verified = store.finalize_run(
        run_id="new-field-verified", source="tushare", scope_key="csi1800",
        range_start="20260821", range_end="20260821",
        fields=["close", "holder_num"], gates=gates,
        receipt_root=receipt_root, previous_open_session="20260820",
        allow_initial_history=True,
    )
    assert verified["watermark_advanced"] is True
    assert store.has_trusted_range(
        source="tushare", scope_key="csi1800",
        range_start="20260821", range_end="20260821",
        fields=["close", "holder_num"],
    )


def test_initial_history_does_not_bypass_existing_field_contiguity(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    receipt_root = tmp_path / "receipts"
    gates = {
        "fetch": True,
        "raw_payloads": True,
        "canonical_commit": True,
        "qlib_readback": True,
        "readiness": True,
        "contiguous_range": True,
    }
    _record_fetch(store, run_id="core-20", status="success", scope={"date": "20260820"})
    seeded = store.finalize_run(
        run_id="core-20", source="tushare", scope_key="csi1800",
        range_start="20260820", range_end="20260820", fields=["close"],
        gates=gates, receipt_root=receipt_root,
    )
    assert seeded["watermark_advanced"] is True
    before = store.watermark_snapshot_bytes()

    _record_fetch(store, run_id="gap-with-new-field", status="success", scope={"date": "20260822"})
    skipped = store.finalize_run(
        run_id="gap-with-new-field", source="tushare", scope_key="csi1800",
        range_start="20260822", range_end="20260822",
        fields=["close", "holder_num"], gates=gates,
        receipt_root=receipt_root, previous_open_session="20260821",
        allow_initial_history=True,
    )
    assert skipped["watermark_advanced"] is False
    assert skipped["trust_state"] == "untrusted"
    assert store.watermark_snapshot_bytes() == before


def test_same_day_runs_do_not_overwrite_and_legacy_cannot_be_trusted(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    receipt_root = tmp_path / "receipts"
    for run_id in ("20260821_run_a", "20260821_run_b"):
        _record_fetch(store, run_id=run_id, status="success", scope={"date": "20260821"})
        result = store.finalize_run(
            run_id=run_id,
            source="tushare",
            scope_key="csi1800",
            range_start="20260821",
            range_end="20260821",
            fields=["close"],
            gates={
                "fetch": True,
                "raw_payloads": True,
                "canonical_commit": True,
                "qlib_readback": True,
                "readiness": True,
                "contiguous_range": True,
            },
            receipt_root=receipt_root,
            trust_state=LEGACY_UNTRUSTED if run_id.endswith("b") else "trusted",
        )

    first = receipt_root / "20260821_run_a" / "receipt.json"
    second = receipt_root / "20260821_run_b" / "receipt.json"
    assert first.is_file() and second.is_file()
    assert first.read_bytes() != second.read_bytes()
    assert result["watermark_advanced"] is False
    assert json.loads(second.read_text())["trust_state"] == LEGACY_UNTRUSTED
    with sqlite3.connect(tmp_path / "audit.db") as conn:
        watermark_run, receipt_sha = conn.execute(
            "SELECT run_id,terminal_receipt_sha256 FROM trusted_watermarks WHERE source='tushare' AND field_name='close'"
        ).fetchone()
    assert watermark_run == "20260821_run_a"
    import hashlib

    assert receipt_sha == hashlib.sha256(first.read_bytes()).hexdigest()


def test_watermark_cannot_jump_over_previous_open_session(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    gates = {
        "fetch": True,
        "raw_payloads": True,
        "canonical_commit": True,
        "qlib_readback": True,
        "readiness": True,
        "contiguous_range": True,
    }
    _record_fetch(store, run_id="run-20", status="success", scope={"date": "20260820"})
    first = store.finalize_run(
        run_id="run-20",
        source="tushare",
        scope_key="csi1800",
        range_start="20260820",
        range_end="20260820",
        fields=["close"],
        gates=gates,
        receipt_root=tmp_path / "receipts",
        previous_open_session="20260819",
    )
    assert first["watermark_advanced"] is True
    before = store.watermark_snapshot_bytes()

    _record_fetch(store, run_id="run-22", status="success", scope={"date": "20260822"})
    skipped = store.finalize_run(
        run_id="run-22",
        source="tushare",
        scope_key="csi1800",
        range_start="20260822",
        range_end="20260822",
        fields=["close"],
        gates=gates,
        receipt_root=tmp_path / "receipts",
        # 20260821 is the actual previous open session, but it is untrusted.
        previous_open_session="20260821",
    )
    assert skipped["watermark_advanced"] is False
    assert skipped["trust_state"] == "untrusted"
    assert store.watermark_snapshot_bytes() == before


def test_watermark_cannot_expand_backward_over_unproven_gap(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    gates = {
        "fetch": True,
        "raw_payloads": True,
        "canonical_commit": True,
        "qlib_readback": True,
        "readiness": True,
        "contiguous_range": True,
    }
    _record_fetch(store, run_id="run-21", status="success", scope={"date": "20260821"})
    first = store.finalize_run(
        run_id="run-21", source="tushare", scope_key="csi1800",
        range_start="20260821", range_end="20260821", fields=["close"],
        gates=gates, receipt_root=tmp_path / "receipts",
        previous_open_session="20260820",
    )
    assert first["watermark_advanced"] is True
    before = store.watermark_snapshot_bytes()

    _record_fetch(store, run_id="run-19", status="success", scope={"date": "20260819"})
    backward = store.finalize_run(
        run_id="run-19", source="tushare", scope_key="csi1800",
        range_start="20260819", range_end="20260819", fields=["close"],
        gates=gates, receipt_root=tmp_path / "receipts",
        previous_open_session="20260818",
    )
    assert backward["watermark_advanced"] is False
    assert backward["trust_state"] == "untrusted"
    assert store.watermark_snapshot_bytes() == before


def test_schema_version_and_run_id_fail_closed(tmp_path: Path) -> None:
    assert validate_run_id("data_sync_ok-1.2") == "data_sync_ok-1.2"
    store = SourceAuditStore(tmp_path / "audit.db")
    with sqlite3.connect(tmp_path / "audit.db") as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    with pytest.raises(ValueError, match="invalid run_id"):
        store.append_event("../escape", "bad")

    incompatible = tmp_path / "incompatible.db"
    with sqlite3.connect(incompatible) as conn:
        conn.execute("CREATE TABLE old_schema(x TEXT)")
    with pytest.raises(RuntimeError, match="user_version=0"):
        SourceAuditStore(incompatible)


def test_field_receipts_gate_required_endpoints_not_optional(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    daily = _row(11.0)
    daily_meta = normalized_response_metadata(daily)
    daily_id = store.record_fetch(
        run_id="field-run", source="tushare", endpoint="daily", status="success",
        requested_scope={}, returned_rows=1, attempt_count=1, payload_frame=daily, **daily_meta,
    )
    store.record_field_receipt_links(run_id="field-run", receipt_id=daily_id, fields=["close", "volume"])
    empty = pd.DataFrame()
    adj_id = store.record_fetch(
        run_id="field-run", source="tushare", endpoint="adj_factor", status="empty",
        requested_scope={}, returned_rows=0, attempt_count=1, **normalized_response_metadata(empty),
    )
    store.record_field_receipt_links(run_id="field-run", receipt_id=adj_id, fields=["factor"])
    store.record_fetch(
        run_id="field-run", source="tushare", endpoint="moneyflow", status="failure",
        requested_scope={}, returned_rows=0, attempt_count=1, error="optional", **normalized_response_metadata(empty),
    )

    result = store.evaluate_field_receipts(
        run_id="field-run",
        field_endpoints={"close": "daily", "volume": "daily", "factor": "adj_factor"},
    )
    assert result["fields"]["close"]["status"] == "success"
    assert result["fields"]["volume"]["status"] == "success"
    assert result["fields"]["factor"]["reason"] == "endpoint_status_empty"
    assert result["status"] == "failed"


@pytest.mark.parametrize(
    ("endpoint", "field_name", "status"),
    [
        ("daily", "close", "empty"),
        ("daily", "close", "partial"),
        ("daily", "close", "failure"),
        ("adj_factor", "factor", "empty"),
        ("adj_factor", "factor", "partial"),
        ("adj_factor", "factor", "failure"),
    ],
)
def test_each_required_field_endpoint_non_success_blocks(
    tmp_path: Path, endpoint: str, field_name: str, status: str
) -> None:
    store = SourceAuditStore(tmp_path / "audit.db")
    frame = _row(11.0) if status == "partial" else pd.DataFrame()
    receipt_id = store.record_fetch(
        run_id="required-gate",
        source="tushare",
        endpoint=endpoint,
        status=status,
        requested_scope={},
        returned_rows=len(frame),
        attempt_count=1,
        payload_frame=frame if status == "partial" else None,
        error="source failed" if status == "failure" else None,
        **normalized_response_metadata(frame),
    )
    store.record_field_receipt_links(
        run_id="required-gate", receipt_id=receipt_id, fields=[field_name]
    )

    result = store.evaluate_field_receipts(
        run_id="required-gate", field_endpoints={field_name: endpoint}
    )
    assert result["status"] == "failed"
    assert result["fields"][field_name]["reason"] == f"endpoint_status_{status}"


def test_data_root_writer_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    with data_writer_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already holds writer lock"):
            with data_writer_lock(tmp_path):
                pass


def test_inherited_writer_lock_validates_same_inode(tmp_path: Path) -> None:
    with data_writer_lock(tmp_path) as parent_lock:
        with data_writer_lock(tmp_path, inherited_fd=parent_lock.fileno()) as child_lock:
            assert child_lock.inherited is True
        wrong_path = tmp_path / "wrong.lock"
        wrong_path.touch()
        wrong_fd = os.open(wrong_path, os.O_RDWR)
        try:
            with pytest.raises(RuntimeError, match="does not match.*inode"):
                with data_writer_lock(tmp_path, inherited_fd=wrong_fd):
                    pass
        finally:
            os.close(wrong_fd)


def test_writer_lock_fd_is_inherited_and_validated_by_child_process(tmp_path: Path) -> None:
    code = (
        "from qsys.data.source_audit import data_writer_lock; import sys; "
        "lock=data_writer_lock.from_environment(sys.argv[1]); "
        "lock.__enter__(); assert lock.inherited; lock.__exit__(None,None,None)"
    )
    with data_writer_lock(tmp_path) as parent_lock:
        env = os.environ.copy()
        env["QSYS_DATA_WRITER_LOCK_FD"] = str(parent_lock.fileno())
        result = subprocess.run(
            [sys.executable, "-c", code, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            pass_fds=(parent_lock.fileno(),),
            check=False,
        )
        with pytest.raises(RuntimeError, match="already holds writer lock"):
            with data_writer_lock(tmp_path):
                pass
    assert result.returncode == 0


def test_crash_receipt_is_untrusted_immutable_and_never_advances_watermark(tmp_path: Path) -> None:
    store = SourceAuditStore(tmp_path / "audit" / "audit.db")
    store.append_event("crash-run", "run_started", {"entrypoint": "test"})
    before = store.watermark_snapshot_bytes()
    first = store.record_crash_receipt(
        run_id="crash-run",
        receipt_root=tmp_path / "audit" / "source_runs",
        entrypoint="scripts/data_sync.py",
        error="token=must-not-leak",
    )
    receipt = Path(first["receipt_path"])
    original = receipt.read_bytes()
    assert first["trust_state"] == "untrusted"
    assert b"must-not-leak" not in original
    assert store.watermark_snapshot_bytes() == before

    second = store.record_crash_receipt(
        run_id="crash-run",
        receipt_root=tmp_path / "audit" / "source_runs",
        entrypoint="scripts/data_sync.py",
        error="second crash",
    )
    assert second["status"] == "existing"
    assert receipt.read_bytes() == original
    assert store.watermark_snapshot_bytes() == before
