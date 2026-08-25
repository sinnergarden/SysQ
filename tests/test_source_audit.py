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
    normalized_response_metadata,
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
