from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from qsys.data.collector import TushareCollector, _supplier_request_sha256
from qsys.data.source_audit import (
    REQUIRED_TERMINAL_GATES,
    SourceAuditStore,
    stable_scope_hash,
)
from qsys.data.storage import StockDataStore
from qsys.ops.data_coverage import (
    fetch_suspension_evidence,
    load_local_suspension_evidence,
)
from scripts.ops.sync_csi800_daily import (
    _abort_if_stage_failed,
    _do_raw_fetch,
    _fetch_daily_industry_after_precheck,
    _fetch_audited_history_suspensions,
    _expected_qlib_value,
    _historical_mutation_readback,
    _qlib_values_equal,
    _refresh_and_verify_changed_symbols,
    _refresh_and_verify_history_mutation_store,
    _verify_history_suspension_receipts,
)


TARGET = "20260821"
HISTORY_START = "20260819"


def test_qlib_readback_compares_the_float32_storage_value():
    expected = 1_515_692_068.0

    assert _qlib_values_equal(expected, np.float32(expected))
    assert not _qlib_values_equal(expected, np.float32(expected) + np.float32(256.0))


def test_qlib_readback_passes_canonical_financial_ratios_through():
    assert _expected_qlib_value("roe", 0.0806185509) == pytest.approx(0.0806185509)
    assert _expected_qlib_value("roe", 0.025) == pytest.approx(0.025)


def _seed_target_watermarks(
    audit: SourceAuditStore, audit_root: Path, *, run_id: str, fields: list[str]
) -> None:
    gates = {name: True for name in REQUIRED_TERMINAL_GATES}
    result = audit.finalize_run(
        run_id=run_id, source="tushare", scope_key="csi1800",
        range_start=TARGET, range_end=TARGET, fields=fields, gates=gates,
        receipt_root=audit_root / "source_runs",
    )
    assert result["status"] == "trusted"


def test_daily_industry_trusted_precheck_noop_makes_zero_supplier_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = tmp_path / "data" / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    _seed_target_watermarks(
        audit, audit_root, run_id="prior-complete",
        fields=["open", "high", "low", "close", "volume", "factor", "industry"],
    )
    calls = []

    def forbidden_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("trusted precheck noop must not call bak_basic")

    monkeypatch.setattr(
        "scripts.ops.sync_csi800_daily.fetch_audited_daily_industry", forbidden_fetch
    )
    core_trusted = audit.has_trusted_range(
        source="tushare", scope_key="csi1800", range_start=TARGET,
        range_end=TARGET, fields=("open", "high", "low", "close", "volume", "factor"),
    )
    industry_trusted = audit.has_trusted_range(
        source="tushare", scope_key="csi1800", range_start=TARGET,
        range_end=TARGET, fields=("industry",),
    )
    summary, receipts = _fetch_daily_industry_after_precheck(
        object(), ["000001.SZ"], TARGET,
        precheck_noop=True, prior_core_trusted=core_trusted,
        prior_industry_trusted=industry_trusted,
        run_id="trusted-noop", audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert summary == {
        "status": "not_required", "reason": "trusted_target_already_complete",
        "target_date": TARGET, "supplier_calls": 0,
    }
    assert receipts == []
    assert calls == []


def test_daily_industry_untrusted_preexisting_requires_history_repair_without_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_root = tmp_path / "data" / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    core_fields = ["open", "high", "low", "close", "volume", "factor"]
    _seed_target_watermarks(
        audit, audit_root, run_id="prior-core-only", fields=core_fields,
    )
    monkeypatch.setattr(
        "scripts.ops.sync_csi800_daily.fetch_audited_daily_industry",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("normal daily must not silently repair history")
        ),
    )
    before = audit.watermark_snapshot_bytes()
    summary, receipts = _fetch_daily_industry_after_precheck(
        object(), ["000001.SZ"], TARGET,
        precheck_noop=True, prior_core_trusted=True,
        prior_industry_trusted=False,
        run_id="repair-required", audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert summary["status"] == "failed"
    assert summary["error"].startswith("REPAIR_REQUIRED:")
    assert summary["supplier_calls"] == 0
    assert receipts == []
    with pytest.raises(RuntimeError, match="REPAIR_REQUIRED"):
        _abort_if_stage_failed(
            {}, stage="daily_industry_evidence", summary=summary,
            do_apply=True, audit_dir=audit_root,
            evidence={
                "store": audit, "run_id": "repair-required",
                "universe": "csi1800", "target_date": TARGET,
                "receipt_root": audit_root / "source_runs",
            },
            outer_owned_terminal=False,
        )
    assert audit.watermark_snapshot_bytes() == before
    assert not audit.has_trusted_range(
        source="tushare", scope_key="csi1800", range_start=TARGET,
        range_end=TARGET, fields=("industry",),
    )


def test_supplier_request_hash_is_order_independent_and_rejects_ambiguous_values() -> None:
    first = _supplier_request_sha256({
        "fields": "ts_code,trade_date,close",
        "trade_date": TARGET,
    })
    second = _supplier_request_sha256({
        "trade_date": TARGET,
        "fields": "ts_code,trade_date,close",
    })
    assert first == second
    assert first != _supplier_request_sha256({
        "fields": "ts_code,trade_date,close,open",
        "trade_date": TARGET,
    })
    with pytest.raises(ValueError, match="cannot be empty"):
        _supplier_request_sha256({})
    with pytest.raises(ValueError, match="not canonically serializable"):
        _supplier_request_sha256({"fields": {"close", "open"}})


def _append_run_started(audit: SourceAuditStore, run_id: str) -> None:
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": "csi1800",
        "target_date": TARGET,
    })


def _failed_run_proof(
    audit: SourceAuditStore, audit_root: Path, run_id: str,
) -> dict[str, str]:
    audit.record_crash_receipt(
        run_id=run_id,
        receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py",
        error="injected test failure",
    )
    return audit.validate_resume_run(
        resume_from_run_id=run_id,
        expected_entrypoint="scripts/data_sync.py",
        universe="csi1800",
        target_date=TARGET,
    )


def test_raw_fetch_single_day_uses_trade_date_path_only():
    class Collector:
        def __init__(self):
            self.daily_calls = []
            self.history_calls = []

        def update_daily(self, *args, **kwargs):
            self.daily_calls.append((args, kwargs))

        def update_universe_history(self, **kwargs):
            self.history_calls.append(kwargs)

    collector = Collector()
    result = _do_raw_fetch(collector, ["000001.SZ", "000002.SZ"], TARGET, since_date=TARGET)

    assert result["status"] == "success", result
    assert result["path"] == "single_day_trade_date"
    assert collector.daily_calls == [
        ((TARGET,), {"codes": ["000001.SZ", "000002.SZ"], "include_financial": True, "force": True})
    ]
    assert collector.history_calls == []


def test_raw_fetch_multi_day_keeps_history_path():
    class Collector:
        def __init__(self):
            self.daily_calls = []
            self.history_calls = []

        def update_daily(self, *args, **kwargs):
            self.daily_calls.append((args, kwargs))

        def update_universe_history(self, **kwargs):
            self.history_calls.append(kwargs)

    collector = Collector()
    result = _do_raw_fetch(collector, ["000001.SZ"], TARGET, since_date="20260820")

    assert result["status"] == "success"
    assert result["path"] == "history_range"
    assert collector.daily_calls == []
    assert collector.history_calls[0]["start_date"] == "20260820"


def test_raw_fetch_propagates_resume_scope_for_daily_and_history(tmp_path: Path):
    class Collector:
        def __init__(self):
            self.daily_calls = []
            self.history_calls = []

        def update_daily(self, *args, **kwargs):
            self.daily_calls.append((args, kwargs))
            return {"status": "success", "mutations": []}

        def update_universe_history(self, **kwargs):
            self.history_calls.append(kwargs)

    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    audit.append_event("resume-current", "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": TARGET,
    })
    proof = {
        "resume_from_run_id": "old",
        "receipt_path": "/tmp/old",
        "receipt_sha256": "a" * 64,
        "entrypoint": "scripts/data_sync.py",
        "universe": "csi1800",
        "target_date": TARGET,
    }
    collector = Collector()
    result = _do_raw_fetch(
        collector,
        ["000001.SZ"],
        TARGET,
        run_id="resume-current",
        audit_store=audit,
        resume_proof=proof,
        scope_key="csi1800",
        universe="csi1800",
    )
    kwargs = collector.daily_calls[0][1]
    assert kwargs["resume_proof"] is proof
    assert kwargs["scope_key"] == "csi1800"
    assert kwargs["universe"] == "csi1800"
    assert result["status"] == "success"

    historical = Collector()
    history_result = _do_raw_fetch(
        historical,
        ["000001.SZ"],
        TARGET,
        since_date="20260820",
        resume_proof=proof,
    )
    assert history_result["status"] == "success"
    assert historical.daily_calls == []
    assert historical.history_calls[0]["resume_proof"] is proof
    assert historical.history_calls[0]["start_date"] == "20260820"


def test_history_stock_endpoint_uses_one_durable_exact_symbol_shard() -> None:
    collector = TushareCollector.__new__(TushareCollector)
    collector._collector_interfaces = {
        "daily_basic": {"fields": ["ts_code", "trade_date", "pe"]}
    }
    calls = []

    def fetch(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        code = kwargs["requested_scope"]["symbols"][0]
        return pd.DataFrame({
            "ts_code": [code], "trade_date": ["20200102"], "pe": [10.0],
        }), f"receipt-{code}"

    collector._fetch_daily_endpoint_with_receipt = fetch
    result = collector._fetch_history_stock_endpoint(
        "daily_basic", ["000001.SZ", "000002.SZ"], "20200101", "20201231",
        run_id="history-run", audit_store=object(), resume_proof={"proof": True},
        scope_key="csi1800", universe="csi1800", evidence_fields=("pe",),
    )

    assert result["ts_code"].tolist() == ["000001.SZ", "000002.SZ"]
    assert [call[1]["requested_scope"]["symbols"] for call in calls] == [
        ["000001.SZ"], ["000002.SZ"],
    ]
    assert all(call[1]["resume_proof"] == {"proof": True} for call in calls)


def _history_suspension_collector(responses):
    calls = []
    collector = TushareCollector.__new__(TushareCollector)
    collector.max_retries = 1
    collector._collector_interfaces = {}

    def api(**kwargs):
        calls.append(dict(kwargs))
        response = responses[kwargs["ts_code"]]
        if isinstance(response, Exception):
            raise response
        return response.copy()

    collector._get_interface_api = lambda endpoint: api
    collector._fetch_with_retry = lambda func, **kwargs: func(**kwargs)
    return collector, calls


def _append_history_run_started(
    audit: SourceAuditStore, run_id: str,
) -> None:
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py",
        "universe": "csi1800",
        "target_date": TARGET,
        "range_start": HISTORY_START,
    })


def _failed_history_run_proof(
    audit: SourceAuditStore, audit_root: Path, run_id: str,
) -> dict[str, str]:
    audit.record_crash_receipt(
        run_id=run_id,
        receipt_root=audit_root / "source_runs",
        entrypoint="scripts/data_sync.py",
        error="injected after suspension evidence",
    )
    return audit.validate_resume_run(
        resume_from_run_id=run_id,
        expected_entrypoint="scripts/data_sync.py",
        universe="csi1800",
        target_date=TARGET,
        range_start=HISTORY_START,
    )


def _finalize_history_receipt(
    audit: SourceAuditStore,
    audit_root: Path,
    run_id: str,
    *,
    trusted: bool,
) -> dict:
    gates = {name: True for name in REQUIRED_TERMINAL_GATES}
    gates["fetch"] = trusted
    gates["raw_payloads"] = trusted
    return audit.finalize_run(
        run_id=run_id,
        source="tushare",
        scope_key="csi1800",
        range_start=HISTORY_START,
        range_end=TARGET,
        fields=["close"],
        gates=gates,
        receipt_root=audit_root / "source_runs",
        allow_initial_history=True,
    )


def test_history_suspension_stage_receipts_exact_union_and_loader_contract(
    tmp_path: Path, monkeypatch,
) -> None:
    symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
    responses = {
        "000001.SZ": pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": [HISTORY_START],
            "suspend_type": ["S"],
        }),
        "000002.SZ": pd.DataFrame(
            columns=["ts_code", "trade_date", "suspend_type"]
        ),
        "000003.SZ": pd.DataFrame({
            "ts_code": ["000003.SZ"],
            "trade_date": [TARGET],
            "suspend_type": ["S"],
        }),
    }
    collector, calls = _history_suspension_collector(responses)
    audit_root = tmp_path / "data" / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    run_id = "history-suspension-complete"
    sleeps = []
    monkeypatch.setattr("qsys.data.collector.time.sleep", sleeps.append)

    summary, receipt_ids = _fetch_audited_history_suspensions(
        collector,
        symbols,
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=run_id,
        audit_store=audit,
        resume_proof=None,
        scope_key="csi1800",
        universe="csi1800",
    )

    assert summary == {
        "status": "success",
        "date_start": HISTORY_START,
        "date_end": TARGET,
        "symbol_count": 3,
        "symbols_sha256": stable_scope_hash(symbols),
        "receipt_count": 3,
        "success_count": 2,
        "empty_count": 1,
        "row_count": 2,
        "reused_count": 0,
    }
    assert [call["ts_code"] for call in calls] == symbols
    assert all(call["start_date"] == HISTORY_START for call in calls)
    assert all(call["end_date"] == TARGET for call in calls)
    assert all(call["suspend_type"] == "S" for call in calls)
    assert all(call["fields"] == "ts_code,trade_date,suspend_type" for call in calls)
    assert sleeps == [0.35, 0.35, 0.35]
    with sqlite3.connect(audit_root / "audit.db") as connection:
        rows = connection.execute(
            "SELECT status,requested_scope_json FROM fetch_receipts "
            "WHERE run_id=? AND endpoint='suspend_d' ORDER BY rowid",
            (run_id,),
        ).fetchall()
    assert [status for status, _ in rows] == ["success", "empty", "success"]
    scopes = [json.loads(scope) for _, scope in rows]
    assert [scope["symbols"] for scope in scopes] == [[symbol] for symbol in symbols]
    assert all(scope["date_start"] == HISTORY_START for scope in scopes)
    assert all(scope["date_end"] == TARGET for scope in scopes)
    assert all(scope["scope_key"] == "csi1800" for scope in scopes)
    assert all(scope["universe"] == "csi1800" for scope in scopes)
    assert all(scope["symbol_count"] == 1 for scope in scopes)
    assert all(
        scope["request_variant"] == "history_suspend_s_v1" for scope in scopes
    )

    terminal_check = _verify_history_suspension_receipts(
        audit, run_id=run_id, summary=summary, receipt_ids=receipt_ids
    )
    assert terminal_check["status"] == "success"
    finalized = _finalize_history_receipt(
        audit, audit_root, run_id, trusted=True
    )
    assert finalized["trust_state"] == "trusted"
    terminal = json.loads(Path(finalized["receipt_path"]).read_text())
    suspension_rows = [
        row for row in terminal["fetch_receipts"]
        if row["endpoint"] == "suspend_d"
    ]
    assert len(suspension_rows) == 3

    loaded = load_local_suspension_evidence(
        finalized["receipt_path"],
        symbols=set(symbols),
        start_date=HISTORY_START,
        end_date=TARGET,
        universe="csi1800",
    )
    assert loaded["shard_count"] == 3
    assert loaded["suspended_dates_by_symbol"] == {
        "000001.SZ": {"2026-08-19"},
        "000003.SZ": {"2026-08-21"},
    }


def test_history_suspension_stage_resume_reuses_exact_shards_without_supplier_calls(
    tmp_path: Path, monkeypatch,
) -> None:
    symbols = ["000001.SZ", "000002.SZ", "000003.SZ"]
    responses = {
        "000001.SZ": pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [HISTORY_START],
            "suspend_type": ["S"],
        }),
        "000002.SZ": pd.DataFrame(
            columns=["ts_code", "trade_date", "suspend_type"]
        ),
        "000003.SZ": pd.DataFrame({
            "ts_code": ["000003.SZ"], "trade_date": [TARGET],
            "suspend_type": ["S"],
        }),
    }
    audit_root = tmp_path / "data" / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    sleeps = []
    monkeypatch.setattr("qsys.data.collector.time.sleep", sleeps.append)
    old_run = "history-suspension-old"
    _append_history_run_started(audit, old_run)
    old_collector, old_calls = _history_suspension_collector(responses)
    old_summary, _ = _fetch_audited_history_suspensions(
        old_collector,
        symbols,
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=old_run,
        audit_store=audit,
        resume_proof=None,
        scope_key="csi1800",
        universe="csi1800",
    )
    assert old_summary["status"] == "success"
    assert len(old_calls) == 3
    assert sleeps == [0.35, 0.35, 0.35]
    sleeps.clear()
    proof = _failed_history_run_proof(audit, audit_root, old_run)

    new_run = "history-suspension-resumed"
    _append_history_run_started(audit, new_run)
    new_collector, new_calls = _history_suspension_collector({
        symbol: AssertionError("verified shard must be reused") for symbol in symbols
    })
    summary, receipt_ids = _fetch_audited_history_suspensions(
        new_collector,
        symbols,
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=new_run,
        audit_store=audit,
        resume_proof=proof,
        scope_key="csi1800",
        universe="csi1800",
    )

    assert new_calls == []
    assert sleeps == []
    assert summary["status"] == "success"
    assert summary["reused_count"] == 3
    assert len(receipt_ids) == 3
    terminal_check = _verify_history_suspension_receipts(
        audit, run_id=new_run, summary=summary, receipt_ids=receipt_ids
    )
    assert terminal_check["status"] == "success"
    second_proof = _failed_history_run_proof(audit, audit_root, new_run)

    final_run = "history-suspension-resumed-twice"
    _append_history_run_started(audit, final_run)
    final_collector, final_calls = _history_suspension_collector({
        symbol: AssertionError("multi-hop verified shard must be reused")
        for symbol in symbols
    })
    final_summary, final_receipt_ids = _fetch_audited_history_suspensions(
        final_collector,
        symbols,
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=final_run,
        audit_store=audit,
        resume_proof=second_proof,
        scope_key="csi1800",
        universe="csi1800",
    )
    assert final_calls == []
    assert final_summary["status"] == "success"
    assert final_summary["reused_count"] == 3
    assert len(final_receipt_ids) == 3
    assert _verify_history_suspension_receipts(
        audit,
        run_id=final_run,
        summary=final_summary,
        receipt_ids=final_receipt_ids,
    )["status"] == "success"
    finalized = _finalize_history_receipt(
        audit, audit_root, final_run, trusted=True
    )
    assert finalized["trust_state"] == "trusted"
    loaded = load_local_suspension_evidence(
        finalized["receipt_path"],
        symbols=set(symbols),
        start_date=HISTORY_START,
        end_date=TARGET,
        universe="csi1800",
    )
    assert loaded["shard_count"] == 3


@pytest.mark.parametrize(
    "case,response",
    [
        ("failure", RuntimeError("supplier down")),
        (
            "partial_missing_suspend_type",
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "trade_date": [HISTORY_START],
            }),
        ),
        (
            "wrong_symbol",
            pd.DataFrame({
                "ts_code": ["999999.SZ"], "trade_date": [HISTORY_START],
                "suspend_type": ["S"],
            }),
        ),
        (
            "wrong_date",
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "trade_date": ["20260818"],
                "suspend_type": ["S"],
            }),
        ),
        (
            "resume_event",
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "trade_date": [HISTORY_START],
                "suspend_type": ["R"],
            }),
        ),
    ],
)
def test_history_suspension_stage_failures_cannot_finalize_trusted(
    tmp_path: Path, case: str, response,
) -> None:
    collector, _ = _history_suspension_collector({"000001.SZ": response})
    audit_root = tmp_path / case / "data" / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    run_id = f"history-suspension-{case}"

    summary, receipt_ids = _fetch_audited_history_suspensions(
        collector,
        ["000001.SZ"],
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=run_id,
        audit_store=audit,
        resume_proof=None,
        scope_key="csi1800",
        universe="csi1800",
    )
    assert summary["status"] == "failed"
    terminal_check = _verify_history_suspension_receipts(
        audit, run_id=run_id, summary=summary, receipt_ids=receipt_ids
    )
    assert terminal_check["status"] == "failed"
    finalized = _finalize_history_receipt(
        audit, audit_root, run_id, trusted=False
    )
    assert finalized["trust_state"] == "untrusted"
    with sqlite3.connect(audit_root / "audit.db") as connection:
        receipt = connection.execute(
            "SELECT status,error_json FROM fetch_receipts WHERE run_id=?",
            (run_id,),
        ).fetchone()
        assert receipt is not None
        if case == "failure":
            assert receipt[0] == "failure"
        else:
            assert receipt[0] == "partial"
            assert json.loads(receipt[1])["detail"]["kind"] == (
                "response_validation_failed"
            )
        assert connection.execute(
            "SELECT COUNT(*) FROM trusted_watermarks WHERE run_id=?", (run_id,)
        ).fetchone()[0] == 0


def test_invalid_history_suspension_partial_is_refetched_on_resume(
    tmp_path: Path,
) -> None:
    audit_root = tmp_path / "data" / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    old_run = "history-suspension-invalid"
    _append_history_run_started(audit, old_run)
    old_collector, old_calls = _history_suspension_collector({
        "000001.SZ": pd.DataFrame({
            "ts_code": ["999999.SZ"],
            "trade_date": [HISTORY_START],
            "suspend_type": ["S"],
        })
    })
    old_summary, _ = _fetch_audited_history_suspensions(
        old_collector,
        ["000001.SZ"],
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=old_run,
        audit_store=audit,
        resume_proof=None,
        scope_key="csi1800",
        universe="csi1800",
    )
    assert old_summary["status"] == "failed"
    assert len(old_calls) == 1
    proof = _failed_history_run_proof(audit, audit_root, old_run)

    new_run = "history-suspension-refetched"
    _append_history_run_started(audit, new_run)
    new_collector, new_calls = _history_suspension_collector({
        "000001.SZ": pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "trade_date": [HISTORY_START],
            "suspend_type": ["S"],
        })
    })
    summary, _ = _fetch_audited_history_suspensions(
        new_collector,
        ["000001.SZ"],
        HISTORY_START,
        TARGET,
        is_history_repair=True,
        run_id=new_run,
        audit_store=audit,
        resume_proof=proof,
        scope_key="csi1800",
        universe="csi1800",
    )

    assert summary["status"] == "success"
    assert summary["reused_count"] == 0
    assert len(new_calls) == 1
    with sqlite3.connect(audit_root / "audit.db") as connection:
        assert connection.execute(
            "SELECT status FROM fetch_receipts WHERE run_id=?",
            (new_run,),
        ).fetchone()[0] == "success"


@pytest.mark.parametrize(
    "universe,is_history_repair",
    [("csi1800", False), ("csi800", True)],
)
def test_history_suspension_stage_does_not_call_supplier_outside_csi1800_history(
    tmp_path: Path, universe: str, is_history_repair: bool,
) -> None:
    collector, calls = _history_suspension_collector({
        "000001.SZ": AssertionError("supplier must not be called")
    })
    audit = SourceAuditStore(tmp_path / universe / "audit" / "audit.db")

    summary, receipt_ids = _fetch_audited_history_suspensions(
        collector,
        ["000001.SZ"],
        HISTORY_START,
        TARGET,
        is_history_repair=is_history_repair,
        run_id=f"not-required-{universe}-{is_history_repair}",
        audit_store=audit,
        resume_proof=None,
        scope_key=universe,
        universe=universe,
    )

    assert summary == {"status": "not_required"}
    assert receipt_ids == []
    assert calls == []
    assert audit.run_evidence_summary(
        f"not-required-{universe}-{is_history_repair}"
    )["fetch_statuses"] == []


def test_historical_income_receipt_links_canonical_and_income_sidecar(tmp_path: Path) -> None:
    collector = TushareCollector.__new__(TushareCollector)
    collector.max_retries = 1
    collector._collector_interfaces = {
        "income": {"fields": "ts_code,ann_date,end_date,report_type,n_income,revenue,oper_cost"},
        "balancesheet": {"fields": "ts_code,ann_date,end_date,total_assets"},
        "cashflow": {"fields": "ts_code,ann_date,end_date,n_cashflow_act"},
        "fina_indicator": {"fields": "ts_code,ann_date,end_date,roe"},
    }
    collector._get_interface_api = lambda _endpoint: (lambda **_kwargs: pd.DataFrame())
    collector._fetch_with_retry = lambda api, **kwargs: api(**kwargs)
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    run_id = "history-income"

    collector._fetch_financials(
        "20140313", "20260821", ts_code="000001.SZ",
        run_id=run_id, audit_store=audit,
        scope_key="csi1800", universe="csi1800",
    )

    with sqlite3.connect(tmp_path / "audit" / "audit.db") as conn:
        links = set(conn.execute(
            "SELECT dataset,field_name FROM field_receipt_links WHERE run_id=?",
            (run_id,),
        ).fetchall())
    assert ("canonical_daily", "revenue") in links
    assert ("income_sidecar", "revenue") in links
    assert ("income_sidecar", "report_type") in links


def _build_daily_collector(store, calls):
    collector = TushareCollector.__new__(TushareCollector)
    collector.store = store
    collector.max_retries = 1
    collector._financial_interfaces = ["income", "balancesheet", "cashflow", "fina_indicator"]
    collector._collector_interfaces = {
        "daily": {"fields": "ts_code,trade_date,open,close,amount"},
        "daily_basic": {"fields": "ts_code,trade_date,turnover_rate"},
        "adj_factor": {"fields": "ts_code,trade_date,adj_factor"},
        "stk_limit": {"fields": "ts_code,trade_date,up_limit,down_limit"},
        "moneyflow": {"fields": "ts_code,trade_date,buy_elg_amount,sell_elg_amount,net_mf_amount"},
        "margin": {
            "interface": "margin_detail",
            "fields": "ts_code,trade_date,rzye",
            "rename": {"rzye": "margin_balance"},
        },
        "income": {"fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_income,revenue,oper_cost"},
        "balancesheet": {"fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab"},
        "cashflow": {"fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_cashflow_act"},
        "fina_indicator": {"fields": "ts_code,ann_date,end_date,update_flag,roe,grossprofit_margin,debt_to_assets,current_ratio,q_dtprofit,q_gr_yoy"},
        "disclosure_date": {"fields": "ts_code,ann_date,end_date,pre_date,actual_date"},
    }
    collector.financial_cols = ["net_income", "revenue", "oper_cost", "total_assets", "equity", "total_cur_assets", "total_cur_liab", "roe", "op_cashflow", "q_dt_profit", "q_gr_yoy", "grossprofit_margin", "debt_to_assets", "current_ratio"]
    collector.moneyflow_fields = ["buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
    collector._moneyflow_derived = ["big_inflow", "net_inflow"]
    collector.margin_cols = ["margin_balance"]
    collector._expected_extra_cols = []
    collector._numeric_extra_cols = []
    collector._non_numeric_cols = []
    collector._get_interface_api = lambda name: calls[
        collector._collector_interfaces.get(name, {}).get("interface", name)
    ]
    collector._validate_and_clean = lambda frame, code, ignore_columns=None: frame
    return collector


def _financial_supplier_calls(*, include_revision_evidence: bool):
    common = {
        "ts_code": ["000001.SZ"],
        "ann_date": [TARGET],
        "end_date": ["20260630"],
    }
    statement_evidence = {
        "f_ann_date": [TARGET],
        "report_type": ["1"],
        "comp_type": ["1"],
        "end_type": ["2"],
        "update_flag": ["0"],
    }
    values = {
        "income": {"n_income": [2.0], "revenue": [10.0], "oper_cost": [6.0]},
        "balancesheet": {
            "total_assets": [20.0],
            "total_hldr_eqy_exc_min_int": [8.0],
            "total_cur_assets": [12.0],
            "total_cur_liab": [6.0],
        },
        "cashflow": {"n_cashflow_act": [4.0]},
        "fina_indicator": {
            "roe": [25.0],
            "grossprofit_margin": [40.0],
            "debt_to_assets": [60.0],
            "current_ratio": [2.0],
            "q_dtprofit": [2.0],
            "q_gr_yoy": [10.0],
        },
    }
    calls = {}
    for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
        evidence = {}
        if include_revision_evidence:
            evidence = (
                {"update_flag": ["0"]}
                if endpoint == "fina_indicator"
                else statement_evidence
            )
        frame = pd.DataFrame({**common, **evidence, **values[endpoint]})
        call_log = []

        def fetch(_frame=frame, _call_log=call_log, **kwargs):
            _call_log.append(kwargs)
            return _frame.copy()

        fetch.calls = call_log
        calls[endpoint] = fetch
    return calls


def test_financial_revision_metadata_is_preserved_only_in_raw_evidence(tmp_path: Path):
    calls = _financial_supplier_calls(include_revision_evidence=True)
    collector = _build_daily_collector(object(), calls)
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")

    canonical_financial = collector._fetch_financials(
        TARGET,
        TARGET,
        ts_code="000001.SZ",
        run_id="financial-evidence-schema",
        audit_store=audit,
        scope_key="csi1800",
        universe="csi1800",
        exact_ann_date=TARGET,
    )

    statement_evidence = {
        "f_ann_date", "report_type", "comp_type", "end_type", "update_flag",
    }
    with sqlite3.connect(tmp_path / "audit" / "audit.db") as connection:
        rows = connection.execute(
            "SELECT endpoint,payload_path FROM fetch_receipts "
            "WHERE run_id=? ORDER BY endpoint",
            ("financial-evidence-schema",),
        ).fetchall()
    assert {endpoint for endpoint, _ in rows} == {
        "income", "balancesheet", "cashflow", "fina_indicator",
    }
    for endpoint, payload_path in rows:
        raw = pd.read_parquet(tmp_path / payload_path)
        if endpoint == "fina_indicator":
            assert "update_flag" in raw.columns
            assert statement_evidence.isdisjoint(
                set(raw.columns) - {"update_flag"}
            )
        else:
            assert statement_evidence.issubset(raw.columns)
        requested_fields = set(calls[endpoint].calls[0]["fields"].split(","))
        assert set(raw.columns).issubset(requested_fields)

    assert statement_evidence.isdisjoint(canonical_financial.columns)


def test_financial_revision_metadata_does_not_change_canonical_columns_or_values():
    evidence_collector = _build_daily_collector(
        object(), _financial_supplier_calls(include_revision_evidence=True)
    )
    daily = pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET], "close": [11.0],
    })

    with_evidence = evidence_collector._merge_financials(
        daily,
        evidence_collector._fetch_financials(
            TARGET, TARGET, ts_code="000001.SZ", exact_ann_date=TARGET,
        ),
    )

    assert with_evidence.loc[0, "net_income"] == 2.0
    assert with_evidence.loc[0, "revenue"] == 10.0
    assert with_evidence.loc[0, "total_assets"] == 20.0
    assert {
        "f_ann_date", "report_type", "comp_type", "end_type", "update_flag",
    }.isdisjoint(with_evidence.columns)

    narrow_collector = _build_daily_collector(
        object(), _financial_supplier_calls(include_revision_evidence=False)
    )
    with pytest.raises(RuntimeError, match="missing_financial_fields"):
        narrow_collector._fetch_financials(
            TARGET, TARGET, ts_code="000001.SZ", exact_ann_date=TARGET,
        )


def test_daily_fastpath_writes_only_target_and_fetches_candidate_financials(tmp_path):
    class Store:
        def __init__(self):
            self.saved = []

        def get_calendar(self):
            return pd.DataFrame({"cal_date": [TARGET], "is_open": [1]})

        def get_global_latest_date(self):
            # The targeted repair must not be skipped by the global watermark.
            return TARGET

        def load_daily(self, code):
            return pd.DataFrame({
                "ts_code": [code], "trade_date": ["20260820"], "net_income": [1.0],
            })

        def save_daily(self, frame, code, existing_df=None):
            self.saved.append((code, frame.copy()))
            return [{
                "symbol": code,
                "dataset": "canonical_daily",
                "source": "tushare",
                "endpoint": "daily",
                "fetch_receipt_id": None,
                "date_start": TARGET,
                "date_end": TARGET,
                "fields": ["close"],
                "mutation_type": "insert",
                "before_hash": "a" * 64,
                "after_hash": "b" * 64,
                "ingested_at": "2026-08-21T12:00:00Z",
            }]

    store = Store()
    calls = {}

    def api(frame):
        def fetch(**kwargs):
            fetch.last_kwargs = kwargs
            fetch.calls.append(kwargs)
            return frame.copy()

        fetch.last_kwargs = {}
        fetch.calls = []
        return fetch

    calls["daily"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ"],
        "trade_date": [TARGET, "20260820"],
        "open": [10.0, 9.0], "close": [11.0, 10.0], "amount": [1.0, 1.0],
    }))
    calls["daily_basic"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET], "turnover_rate": [0.1],
    }))
    calls["adj_factor"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET], "adj_factor": [1.0],
    }))
    calls["stk_limit"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET], "up_limit": [12.0], "down_limit": [8.0],
    }))
    calls["moneyflow"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET], "buy_elg_amount": [3.0],
        "sell_elg_amount": [1.0], "net_mf_amount": [2.0],
    }))
    calls["margin_detail"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET], "rzye": [5.0],
    }))
    calls["disclosure_date"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ"],
        "ann_date": [TARGET, "20260820"],
        "actual_date": [TARGET, "20260820"],
        "end_date": ["20260630", "20260630"],
    }))
    calls["income"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
        "f_ann_date": [TARGET], "report_type": ["1"], "comp_type": ["1"],
        "end_type": ["2"], "update_flag": ["0"],
        "n_income": [2.0], "revenue": [10.0], "oper_cost": [6.0],
    }))
    calls["balancesheet"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
        "f_ann_date": [TARGET], "report_type": ["1"], "comp_type": ["1"],
        "end_type": ["2"], "update_flag": ["0"],
        "total_assets": [20.0], "total_hldr_eqy_exc_min_int": [8.0],
        "total_cur_assets": [12.0], "total_cur_liab": [6.0],
    }))
    calls["cashflow"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
        "f_ann_date": [TARGET], "report_type": ["1"], "comp_type": ["1"],
        "end_type": ["2"], "update_flag": ["0"],
        "n_cashflow_act": [4.0],
    }))
    calls["fina_indicator"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
        "update_flag": ["0"],
        "roe": [0.25], "grossprofit_margin": [0.4], "debt_to_assets": [0.6],
        "current_ratio": [2.0], "q_dtprofit": [2.0], "q_gr_yoy": [0.1],
    }))

    collector = _build_daily_collector(store, calls)
    audit = SourceAuditStore(tmp_path / "audit.db")
    collector.update_daily(
        "2026-08-21",
        codes=["000001.SZ"],
        force=True,
        run_id="daily-bundle-run",
        audit_store=audit,
    )

    assert len(store.saved) == 1
    saved = store.saved[0][1]
    assert saved["trade_date"].astype(str).tolist() == [TARGET]
    assert saved.iloc[0]["net_income"] == 2.0
    # Both discovery predicates are market-wide and unioned.  actual_date is
    # the publication signal; ann_date is retained only for revisions.
    assert {key for call in calls["disclosure_date"].calls for key in call} >= {
        "actual_date", "ann_date", "fields"
    }
    assert all("ts_code" not in call for call in calls["disclosure_date"].calls)
    for name in ("income", "balancesheet", "cashflow", "fina_indicator"):
        # Ordinary financial endpoints always receive their required ts_code
        # and exact announcement date.  This is also required for indicator,
        # whose start/end parameters use the report-period axis.
        assert calls[name].last_kwargs["ts_code"] == "000001.SZ"
        assert calls[name].last_kwargs["ann_date"] == TARGET
        assert "start_date" not in calls[name].last_kwargs
        assert "end_date" not in calls[name].last_kwargs
    changed = audit.changed_mutations("daily-bundle-run")
    assert changed[0]["endpoint"] == "daily_bundle"
    assert changed[0]["fetch_receipt_id"]
    evidence = audit.run_evidence_summary("daily-bundle-run")
    assert evidence["fetch_statuses"][-1] == "success"
    with sqlite3.connect(tmp_path / "audit.db") as conn:
        scopes = [json.loads(row[0]) for row in conn.execute(
            "SELECT requested_scope_json FROM fetch_receipts WHERE run_id='daily-bundle-run'"
        ).fetchall()]
    assert all("symbols" not in scope for scope in scopes)
    assert all({"symbol_count", "symbols_sha256"}.issubset(scope) for scope in scopes)


def test_market_endpoint_resume_reuses_verified_success_and_empty_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qsys.data._fetch_strategies.time.sleep", lambda _seconds: None)
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    old_run = "market-failed"
    _append_run_started(audit, old_run)
    base_scope = {
        "date_start": TARGET,
        "date_end": TARGET,
        "symbol_count": 1,
        "symbols_sha256": stable_scope_hash(["000001.SZ"]),
    }

    def spy(frame=None, *, error: Exception | None = None):
        calls = []

        def fetch(**kwargs):
            calls.append(kwargs)
            if error is not None:
                raise error
            return frame.copy()

        fetch.calls = calls
        return fetch

    old_calls = {
        "daily": spy(pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [TARGET], "close": [11.0],
        })),
        "daily_basic": spy(pd.DataFrame()),
        "stk_limit": spy(pd.DataFrame({"ts_code": ["000001.SZ"], "up_limit": [12.0]})),
        "moneyflow": spy(error=RuntimeError("supplier down")),
        "margin_detail": spy(pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [TARGET], "rzye": [5.0],
        })),
    }
    collector = _build_daily_collector(object(), old_calls)
    for endpoint, required in (
        ("daily", True), ("daily_basic", False), ("stk_limit", False),
        ("moneyflow", False), ("margin", False),
    ):
        collector._fetch_daily_endpoint_with_receipt(
            endpoint,
            run_id=old_run,
            audit_store=audit,
            requested_scope=base_scope,
            scope_key="csi1800",
            universe="csi1800",
            required_endpoint=required,
            trade_date=TARGET,
        )
    proof = _failed_run_proof(audit, audit_root, old_run)
    with sqlite3.connect(audit_root / "audit.db") as conn:
        margin_payload = conn.execute(
            "SELECT payload_path FROM fetch_receipts "
            "WHERE run_id=? AND endpoint='margin'",
            (old_run,),
        ).fetchone()[0]
    tampered = tmp_path / margin_payload
    tampered.write_bytes(tampered.read_bytes() + b"tampered")

    def forbidden(**_kwargs):
        raise AssertionError("verified durable shard must not call supplier")

    fresh_calls = {
        "daily": forbidden,
        "daily_basic": forbidden,
        "stk_limit": spy(pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [TARGET], "up_limit": [12.0],
        })),
        "moneyflow": spy(pd.DataFrame()),
        "margin_detail": spy(pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [TARGET], "rzye": [5.0],
        })),
    }
    resumed = _build_daily_collector(object(), fresh_calls)
    new_run = "market-resumed"
    _append_run_started(audit, new_run)
    statuses = {}
    for endpoint, required in (
        ("daily", True), ("daily_basic", False), ("stk_limit", False),
        ("moneyflow", False), ("margin", False),
    ):
        frame, receipt_id = resumed._fetch_daily_endpoint_with_receipt(
            endpoint,
            run_id=new_run,
            audit_store=audit,
            resume_proof=proof,
            requested_scope=base_scope,
            scope_key="csi1800",
            universe="csi1800",
            required_endpoint=required,
            trade_date=TARGET,
        )
        statuses[endpoint] = (len(frame), receipt_id)

    assert statuses["daily"][0] == 1
    assert statuses["daily_basic"][0] == 0
    assert len(fresh_calls["stk_limit"].calls) == 1
    assert len(fresh_calls["moneyflow"].calls) == 1
    assert len(fresh_calls["margin_detail"].calls) == 1
    reused_events = [
        event["payload"]
        for event in audit.run_evidence_summary(new_run)["events"]
        if event["event_type"] == "fetch_shard_reused"
    ]
    assert {event["endpoint"] for event in reused_events} == {"daily", "daily_basic"}

    # A second failed run is self-contained: the next explicit resume points
    # only at its terminal receipt and clones its current-run receipt identity.
    second_proof = _failed_run_proof(audit, audit_root, new_run)
    third_run = "market-resumed-again"
    _append_run_started(audit, third_run)
    third_collector = _build_daily_collector(object(), {"daily": forbidden})
    third_frame, _ = third_collector._fetch_daily_endpoint_with_receipt(
        "daily",
        run_id=third_run,
        audit_store=audit,
        resume_proof=second_proof,
        requested_scope=base_scope,
        scope_key="csi1800",
        universe="csi1800",
        trade_date=TARGET,
    )
    assert len(third_frame) == 1
    third_event = [
        event["payload"]
        for event in audit.run_evidence_summary(third_run)["events"]
        if event["event_type"] == "fetch_shard_reused"
    ][0]
    assert third_event["resume_from_run_id"] == new_run
    assert third_event["source_receipt_id"] == statuses["daily"][1]


@pytest.mark.parametrize(
    "endpoint,identity_columns,old_fields,new_fields,query_kwargs,old_frame,new_frame",
    [
        (
            "daily",
            ("ts_code", "trade_date"),
            "ts_code,trade_date,close",
            "ts_code,trade_date,close,open",
            {"trade_date": TARGET},
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "trade_date": [TARGET], "close": [11.0],
            }),
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "trade_date": [TARGET],
                "close": [11.0], "open": [10.0],
            }),
        ),
        (
            "income",
            ("ts_code", "ann_date"),
            "ts_code,ann_date,end_date,n_income",
            (
                "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,"
                "end_type,update_flag,n_income"
            ),
            {"ts_code": "000001.SZ", "start_date": TARGET, "end_date": TARGET},
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "ann_date": [TARGET],
                "end_date": ["20260630"], "n_income": [2.0],
            }),
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "ann_date": [TARGET],
                "end_date": ["20260630"], "f_ann_date": [TARGET],
                "report_type": ["1"], "comp_type": ["1"], "end_type": ["2"],
                "update_flag": ["1"], "n_income": [2.0],
            }),
        ),
    ],
)
def test_changed_supplier_fields_never_reuse_stale_market_or_financial_shard(
    tmp_path: Path,
    endpoint: str,
    identity_columns: tuple[str, ...],
    old_fields: str,
    new_fields: str,
    query_kwargs: dict[str, str],
    old_frame: pd.DataFrame,
    new_frame: pd.DataFrame,
) -> None:
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    base_scope = {
        "date_start": TARGET,
        "date_end": TARGET,
        "symbol_count": 1,
        "symbols_sha256": stable_scope_hash(["000001.SZ"]),
    }

    old_calls = []

    def old_api(**kwargs):
        old_calls.append(kwargs)
        return old_frame.copy()

    old_run = f"query-fields-old-{endpoint}"
    _append_run_started(audit, old_run)
    old_collector = _build_daily_collector(object(), {endpoint: old_api})
    old_collector._fetch_daily_endpoint_with_receipt(
        endpoint,
        run_id=old_run,
        audit_store=audit,
        requested_scope=base_scope,
        scope_key="csi1800",
        universe="csi1800",
        identity_columns=identity_columns,
        **query_kwargs,
        fields=old_fields,
    )
    proof = _failed_run_proof(audit, audit_root, old_run)
    assert len(old_calls) == 1

    fresh_calls = []

    def fresh_api(**kwargs):
        fresh_calls.append(kwargs)
        return new_frame.copy()

    new_run = f"query-fields-new-{endpoint}"
    _append_run_started(audit, new_run)
    new_collector = _build_daily_collector(object(), {endpoint: fresh_api})
    frame, _ = new_collector._fetch_daily_endpoint_with_receipt(
        endpoint,
        run_id=new_run,
        audit_store=audit,
        resume_proof=proof,
        requested_scope=base_scope,
        scope_key="csi1800",
        universe="csi1800",
        identity_columns=identity_columns,
        **query_kwargs,
        fields=new_fields,
    )
    assert len(fresh_calls) == 1
    pd.testing.assert_frame_equal(frame, new_frame)

    with sqlite3.connect(audit_root / "audit.db") as connection:
        scopes = [
            json.loads(row[0])
            for row in connection.execute(
                "SELECT requested_scope_json FROM fetch_receipts "
                "WHERE endpoint=? ORDER BY rowid",
                (endpoint,),
            ).fetchall()
        ]
    assert len(scopes) == 2
    assert scopes[0]["request_sha256"] != scopes[1]["request_sha256"]
    assert scopes[0]["checkpoint_key"] != scopes[1]["checkpoint_key"]
    assert not any(
        event["event_type"] == "fetch_shard_reused"
        for event in audit.run_evidence_summary(new_run)["events"]
    )


def test_update_daily_resume_skips_completed_endpoint_and_rebuilds_daily_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qsys.data._fetch_strategies.time.sleep", lambda _seconds: None)

    class Store:
        def __init__(self):
            self.saved = []

        def get_calendar(self):
            return pd.DataFrame({"cal_date": [TARGET], "is_open": [1]})

        def get_global_latest_date(self):
            return None

        def load_daily(self, _code):
            return None

        def save_daily(self, frame, code, existing_df=None):
            self.saved.append((code, frame.copy()))
            return []

    def spy(frame=None, *, error: Exception | None = None):
        def fetch(**kwargs):
            fetch.calls.append(kwargs)
            if error is not None:
                raise error
            return frame.copy()

        fetch.calls = []
        return fetch

    daily_frame = pd.DataFrame({
        "ts_code": ["000001.SZ"], "trade_date": [TARGET],
        "open": [10.0], "high": [12.0], "low": [9.0], "close": [11.0],
        "vol": [100.0], "amount": [1.0],
    })
    empty = pd.DataFrame()
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    old_run = "chain-failed"
    _append_run_started(audit, old_run)
    old_calls = {
        "daily": spy(daily_frame),
        "daily_basic": spy(empty),
        "adj_factor": spy(error=RuntimeError("adj unavailable")),
        "stk_limit": spy(empty), "moneyflow": spy(empty),
        "margin_detail": spy(empty), "disclosure_date": spy(empty),
    }
    for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
        old_calls[endpoint] = spy(empty)
    failed_collector = _build_daily_collector(Store(), old_calls)
    with pytest.raises(Exception, match="Max retries exceeded"):
        failed_collector.update_daily(
            TARGET,
            codes=["000001.SZ"],
            force=True,
            run_id=old_run,
            audit_store=audit,
            scope_key="csi1800",
            universe="csi1800",
        )
    proof = _failed_run_proof(audit, audit_root, old_run)
    old_receipt = Path(proof["receipt_path"])
    old_bytes = old_receipt.read_bytes()

    new_run = "chain-resumed"
    _append_run_started(audit, new_run)
    fresh_calls = {
        "daily": spy(error=AssertionError("daily must be reused")),
        "daily_basic": spy(error=AssertionError("empty must be reused")),
        "adj_factor": spy(pd.DataFrame({
            "ts_code": ["000001.SZ"], "trade_date": [TARGET], "adj_factor": [1.0],
        })),
        "stk_limit": spy(empty), "moneyflow": spy(empty),
        "margin_detail": spy(empty), "disclosure_date": spy(empty),
    }
    for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
        fresh_calls[endpoint] = spy(empty)
    resumed_store = Store()
    resumed_collector = _build_daily_collector(resumed_store, fresh_calls)
    result = resumed_collector.update_daily(
        TARGET,
        codes=["000001.SZ"],
        force=True,
        run_id=new_run,
        audit_store=audit,
        resume_proof=proof,
        scope_key="csi1800",
        universe="csi1800",
    )

    assert result["status"] == "success"
    assert fresh_calls["daily"].calls == []
    assert fresh_calls["daily_basic"].calls == []
    assert len(fresh_calls["adj_factor"].calls) == 1
    assert len(resumed_store.saved) == 1
    with sqlite3.connect(audit_root / "audit.db") as conn:
        endpoints = [row[0] for row in conn.execute(
            "SELECT endpoint FROM fetch_receipts WHERE run_id=? ORDER BY rowid",
            (new_run,),
        ).fetchall()]
    assert endpoints.count("daily_bundle") == 1
    reused_endpoints = {
        event["payload"]["endpoint"]
        for event in audit.run_evidence_summary(new_run)["events"]
        if event["event_type"] == "fetch_shard_reused"
    }
    assert reused_endpoints == {"daily", "daily_basic"}
    assert "daily_bundle" not in reused_endpoints
    assert old_receipt.read_bytes() == old_bytes


def test_financial_discovery_and_per_symbol_shards_resume_without_supplier_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("qsys.data.collector.time.sleep", lambda _seconds: None)
    codes = ["000001.SZ", "000002.SZ"]

    def disclosure_api(**kwargs):
        disclosure_api.calls.append(kwargs)
        return pd.DataFrame({
            "ts_code": codes,
            "ann_date": [TARGET, TARGET],
            "actual_date": [TARGET, TARGET],
            "end_date": ["20260630", "20260630"],
        })

    disclosure_api.calls = []

    def statement_api(endpoint):
        def fetch(**kwargs):
            fetch.calls.append(kwargs)
            code = kwargs["ts_code"]
            common = {
                "ts_code": [code], "ann_date": [TARGET], "end_date": ["20260630"],
            }
            evidence = (
                {"update_flag": ["0"]}
                if endpoint == "fina_indicator"
                else {
                    "f_ann_date": [TARGET], "report_type": ["1"],
                    "comp_type": ["1"], "end_type": ["2"],
                    "update_flag": ["0"],
                }
            )
            values = {
                "income": {"n_income": [2.0], "revenue": [10.0], "oper_cost": [6.0]},
                "balancesheet": {
                    "total_assets": [20.0], "total_hldr_eqy_exc_min_int": [8.0],
                    "total_cur_assets": [12.0], "total_cur_liab": [6.0],
                },
                "cashflow": {"n_cashflow_act": [4.0]},
                "fina_indicator": {
                    "roe": [0.25], "grossprofit_margin": [0.4],
                    "debt_to_assets": [0.6], "current_ratio": [2.0],
                    "q_dtprofit": [2.0], "q_gr_yoy": [0.1],
                },
            }
            return pd.DataFrame({**common, **evidence, **values[endpoint]})

        fetch.calls = []
        return fetch

    old_calls = {"disclosure_date": disclosure_api}
    for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
        old_calls[endpoint] = statement_api(endpoint)
    old_collector = _build_daily_collector(object(), old_calls)
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    old_run = "financial-failed"
    _append_run_started(audit, old_run)
    old_result = old_collector._fetch_financials_for_daily(
        TARGET,
        set(codes),
        run_id=old_run,
        audit_store=audit,
        scope_key="csi1800",
        universe="csi1800",
    )
    assert set(old_result["ts_code"]) == set(codes)
    proof = _failed_run_proof(audit, audit_root, old_run)

    with sqlite3.connect(audit_root / "audit.db") as conn:
        receipt_rows = conn.execute(
            "SELECT endpoint,requested_scope_json FROM fetch_receipts "
            "WHERE run_id=? ORDER BY rowid",
            (old_run,),
        ).fetchall()
    assert len(receipt_rows) == 10
    scopes_by_endpoint = {}
    for endpoint, scope_json in receipt_rows:
        scopes_by_endpoint.setdefault(endpoint, []).append(json.loads(scope_json))
    disclosure_scopes = scopes_by_endpoint["disclosure_date"]
    assert {scope["request_variant"] for scope in disclosure_scopes} == {
        "actual_date", "ann_date",
    }
    assert len({scope["checkpoint_key"] for scope in disclosure_scopes}) == 2
    for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
        assert len(scopes_by_endpoint[endpoint]) == 2
        assert len({scope["symbols_sha256"] for scope in scopes_by_endpoint[endpoint]}) == 2
        assert len({scope["checkpoint_key"] for scope in scopes_by_endpoint[endpoint]}) == 2

    def forbidden_api(**kwargs):
        forbidden_api.calls.append(kwargs)
        raise AssertionError("financial shard must be reused")

    forbidden_api.calls = []
    fresh_calls = {"disclosure_date": forbidden_api}
    for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
        fresh_calls[endpoint] = forbidden_api
    resumed_collector = _build_daily_collector(object(), fresh_calls)
    new_run = "financial-resumed"
    _append_run_started(audit, new_run)
    resumed_result = resumed_collector._fetch_financials_for_daily(
        TARGET,
        set(codes),
        run_id=new_run,
        audit_store=audit,
        resume_proof=proof,
        scope_key="csi1800",
        universe="csi1800",
    )

    assert set(resumed_result["ts_code"]) == set(codes)
    assert forbidden_api.calls == []
    reused_events = [
        event["payload"]
        for event in audit.run_evidence_summary(new_run)["events"]
        if event["event_type"] == "fetch_shard_reused"
    ]
    assert len(reused_events) == 10
    with sqlite3.connect(audit_root / "audit.db") as conn:
        links = set(conn.execute(
            """SELECT f.endpoint,l.field_name,l.run_id
               FROM field_receipt_links l
               JOIN fetch_receipts f ON f.receipt_id=l.receipt_id
               WHERE l.run_id=?""",
            (new_run,),
        ).fetchall())
    assert ("income", "n_income", new_run) in links
    assert ("cashflow", "n_cashflow_act", new_run) in links
    assert ("fina_indicator", "q_dtprofit", new_run) in links
    assert ("income", "net_income", new_run) not in links


def test_mutation_refresh_dump_fixes_only_updates_but_reads_back_inserts_too():
    mutations = [
        {"symbol": "000001.SZ", "date_start": TARGET, "date_end": TARGET, "fields": ["close"], "mutation_type": "insert"},
        {"symbol": "000002.SZ", "date_start": TARGET, "date_end": TARGET, "fields": ["close"], "mutation_type": "update"},
    ]

    class Store:
        def load_daily(self, symbol):
            return pd.DataFrame({"trade_date": [TARGET], "close": [11.0 if symbol == "000001.SZ" else 22.0]})

    class Adapter:
        def __init__(self):
            self.fix_calls = []

        def convert_fix_symbols(self, symbols, refresh_universes=None):
            self.fix_calls.append(list(symbols))
            return {"status": "success", "symbols_count": len(symbols)}

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-08-21"), "000001.SZ"), (pd.Timestamp("2026-08-21"), "000002.SZ")],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame({"$close": [11.0, 22.0]}, index=index)

    adapter = Adapter()
    result = _refresh_and_verify_changed_symbols(
        adapter, Store(), mutations, target_dt=TARGET, apply=True
    )

    assert adapter.fix_calls == [["000002.SZ"]]
    assert result["changed_symbols"] == ["000001.SZ", "000002.SZ"]
    assert result["revision_symbols"] == ["000002.SZ"]
    assert result["verified_value_count"] == 2
    assert result["status"] == "success"


@pytest.mark.parametrize(
    ("qlib_industry", "expected_status"),
    [(7.0, "success"), (8.0, "failed"), (None, "failed")],
)
def test_daily_industry_mutation_readback_uses_mapping_id(qlib_industry, expected_status):
    mutations = [{
        "symbol": "000001.SZ", "date_start": TARGET, "date_end": TARGET,
        "fields": ["industry"], "mutation_type": "update",
    }]

    class Store:
        def load_daily_window(self, symbol, *, start_date, end_date, columns):
            assert columns == ["industry"]
            return pd.DataFrame({"trade_date": [TARGET], "industry": ["Sector"]})

        def get_stock_list(self):
            return pd.DataFrame({"ts_code": ["000001.SZ"], "industry": ["Sector"]})

    class Adapter:
        def convert_fix_symbols(self, symbols, refresh_universes=None):
            return {"status": "success", "symbols_count": len(symbols)}

        def _load_industry_map(self, _stock_list):
            return {"Sector": 7}

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-08-21"), "000001.SZ")],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame(
                {} if qlib_industry is None else {"$industry": [qlib_industry]},
                index=index,
            )

    result = _refresh_and_verify_changed_symbols(
        Adapter(), Store(), mutations, target_dt=TARGET, apply=True
    )
    assert result["status"] == expected_status
    assert result.get("verified_value_count", 0) == (1 if expected_status == "success" else 0)


def test_historical_mutation_readback_uses_mutation_date_not_target_date():
    mutation_date = "20200102"
    mutations = [{
        "symbol": "000001.SZ", "date_start": mutation_date,
        "date_end": mutation_date, "fields": ["close"], "mutation_type": "update",
    }]

    class Store:
        def load_daily_window(self, symbol, *, start_date, end_date, columns):
            assert symbol == "000001.SZ"
            assert (start_date, end_date, columns) == (mutation_date, mutation_date, ["close"])
            return pd.DataFrame({"trade_date": [mutation_date], "close": [11.0]})

    class Adapter:
        def convert_fix_symbols(self, symbols, refresh_universes=None):
            return {"status": "success", "symbols_count": len(symbols)}

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            assert (start_time, end_time) == ("2020-01-02", "2020-01-02")
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2020-01-02"), "000001.SZ")],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame({"$close": [11.0]}, index=index)

    result = _refresh_and_verify_changed_symbols(
        Adapter(), Store(), mutations, target_dt=TARGET, apply=True, history_mode=True,
    )

    assert result["status"] == "success"
    assert result["mode"] == "historical_mutation_fix"
    assert result["verified_value_count"] == 1


def test_historical_mutation_readback_verifies_bounded_multi_day_scope():
    dates = pd.date_range("2020-01-02", periods=3, freq="D")
    canonical_dates = dates.strftime("%Y%m%d").tolist()
    mutation = [{
        "symbol": "000001.SZ",
        "date_start": canonical_dates[0],
        "date_end": canonical_dates[-1],
        "fields": ["close"],
        "mutation_type": "update",
    }]

    class Store:
        def load_daily_window(self, symbol, *, start_date, end_date, columns):
            assert (start_date, end_date, columns) == (
                canonical_dates[0], canonical_dates[-1], ["close"]
            )
            return pd.DataFrame({
                "trade_date": canonical_dates,
                "close": [11.0, 12.0, 13.0],
            })

    class Adapter:
        def get_features(self, symbols, fields, start_time=None, end_time=None):
            index = pd.MultiIndex.from_arrays(
                [dates, ["000001.SZ"] * len(dates)],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame({"$close": [11.0, 12.0, 13.0]}, index=index)

    result = _historical_mutation_readback(Adapter(), Store(), mutation)

    assert result["status"] == "success"
    assert result["verified_value_count"] == 3
    assert result["mismatch_count"] == 0


def test_historical_mutation_readback_indexes_dates_and_loads_industry_map_once(monkeypatch):
    dates = pd.date_range("2020-01-02", periods=250, freq="D")
    canonical_dates = dates.strftime("%Y%m%d").tolist()
    mutations = [
        {
            "symbol": "000001.SZ",
            "date_start": date,
            "date_end": date,
            "fields": ["close", "industry"],
            "mutation_type": "update",
        }
        for date in canonical_dates
    ]

    class Store:
        def __init__(self):
            self.stock_list_calls = 0

        def load_daily_window(self, symbol, *, start_date, end_date, columns):
            assert symbol == "000001.SZ"
            return pd.DataFrame({
                "trade_date": canonical_dates,
                "close": [11.0] * len(dates),
                "industry": ["Sector"] * len(dates),
            })

        def get_stock_list(self):
            self.stock_list_calls += 1
            return pd.DataFrame({"ts_code": ["000001.SZ"], "industry": ["Sector"]})

    class Adapter:
        def __init__(self):
            self.industry_map_calls = 0

        def _load_industry_map(self, _stock_list):
            self.industry_map_calls += 1
            return {"Sector": 7}

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            index = pd.MultiIndex.from_arrays(
                [dates, ["000001.SZ"] * len(dates)],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame(
                {"$close": [11.0] * len(dates), "$industry": [7.0] * len(dates)},
                index=index,
            )

    equality_calls = 0
    original_eq = pd.Series.__eq__

    def tracked_eq(series, other):
        nonlocal equality_calls
        if series.name == "instrument":
            equality_calls += 1
        return original_eq(series, other)

    monkeypatch.setattr(pd.Series, "__eq__", tracked_eq)
    store = Store()
    adapter = Adapter()
    result = _historical_mutation_readback(adapter, store, mutations)

    assert result["status"] == "success"
    assert result["verified_value_count"] == 500
    assert equality_calls == 1
    assert store.stock_list_calls == 1
    assert adapter.industry_map_calls == 1


def test_historical_mutation_store_reads_one_symbol_at_a_time(tmp_path):
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    run_id = "history-stream"
    audit.append_event(run_id, "run_started", {"entrypoint": "test"})
    audit.record_mutations(
        run_id=run_id,
        mutations=[
            {
                "symbol": symbol,
                "date_start": "20200102",
                "date_end": "20200102",
                "fields": ["close"],
                "mutation_type": mutation_type,
                "before_hash": "before",
                "after_hash": "after",
            }
            for symbol, mutation_type in (
                ("000001.SZ", "insert"),
                ("000002.SZ", "update"),
            )
        ],
    )
    queried_symbols = []
    changed_mutations = audit.changed_mutations

    def tracked_changed_mutations(run_id, *, symbol=None):
        queried_symbols.append(symbol)
        return changed_mutations(run_id, symbol=symbol)

    audit.changed_mutations = tracked_changed_mutations

    class Store:
        def load_daily_window(self, symbol, *, start_date, end_date, columns):
            return pd.DataFrame({"trade_date": ["20200102"], "close": [11.0]})

    class Adapter:
        def __init__(self):
            self.fix_calls = []

        def convert_fix_symbols(self, symbols, **kwargs):
            self.fix_calls.append((list(symbols), kwargs))
            return {"status": "success"}

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2020-01-02"), symbols[0])],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame({"$close": [11.0]}, index=index)

    adapter = Adapter()
    result = _refresh_and_verify_history_mutation_store(
        adapter, Store(), audit, [run_id], apply=True,
        require_pit_industry=True, pit_industry_until_date="20260821",
    )

    assert queried_symbols == ["000001.SZ", "000002.SZ"]
    assert adapter.fix_calls == [(["000002.SZ"], {
        "refresh_universes": [],
        "require_pit_industry": True,
        "pit_industry_until_date": "20260821",
    })]
    assert result["changed_symbols"] == ["000001.SZ", "000002.SZ"]
    assert result["verified_value_count"] == 2
    assert result["mismatch_count"] == 0
    assert result["status"] == "success"


def test_historical_mutation_mismatch_samples_are_bounded(monkeypatch):
    symbols = [f"{number:06d}.SZ" for number in range(150)]

    class Audit:
        def changed_mutation_symbols(self, _run_id, *, mutation_type=None):
            return [] if mutation_type == "update" else symbols

        def changed_mutations(self, _run_id, *, symbol=None):
            return [{"symbol": symbol, "mutation_type": "insert"}]

    monkeypatch.setattr(
        "scripts.ops.sync_csi800_daily._historical_mutation_readback",
        lambda _adapter, _store, changed: {
            "verified_fields": [],
            "verified_value_count": 0,
            "mismatches": [{"symbol": changed[0]["symbol"], "reason": "test"}],
        },
    )

    result = _refresh_and_verify_history_mutation_store(
        object(), object(), Audit(), ["history-run"], apply=True
    )

    assert result["status"] == "failed"
    assert result["mismatch_count"] == 150
    assert len(result["mismatches"]) == 100


def test_real_store_insert_volume_alias_reads_back_one_qlib_volume(tmp_path):
    store = StockDataStore.__new__(StockDataStore)
    store.canonical_dir = tmp_path / "canonical"
    store.canonical_dir.mkdir()
    store.meta_db_path = tmp_path / "meta.db"
    store._init_db()
    incoming = pd.DataFrame(
        {"ts_code": ["000001.SZ"], "trade_date": [TARGET], "close": [11.0], "vol": [10.0]}
    )
    mutations = store.save_daily(incoming, "000001.SZ")

    class Adapter:
        def convert_fix_symbols(self, symbols, refresh_universes=None):
            raise AssertionError("insert must not dump_fix")

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            assert fields.count("$volume") == 1
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2026-08-21"), "000001.SZ")], names=["datetime", "instrument"]
            )
            return pd.DataFrame({"$close": [11.0], "$volume": [1000.0]}, index=index)

    result = _refresh_and_verify_changed_symbols(
        Adapter(), store, mutations, target_dt=TARGET, apply=True
    )
    assert result["status"] == "success", result
    assert result["verified_fields"].count("$volume") == 1
    assert result["verified_value_count"] >= 1


def test_requested_symbol_gap_requires_suspension_explanation(monkeypatch):
    class Collector:
        def update_daily(self, *args, **kwargs):
            return {
                "status": "success",
                "mutations": [],
                "required_endpoint_missing_symbols": {
                    "daily": ["000002.SZ", "000003.SZ"],
                    "adj_factor": [],
                },
            }

    monkeypatch.setattr(
        "qsys.ops.data_coverage.fetch_suspension_evidence",
        lambda **kwargs: {
            "status": "success",
            "suspended_dates_by_symbol": {"000002.SZ": {"2026-08-21"}},
            "raw_frame": pd.DataFrame(
                {"ts_code": ["000002.SZ"], "trade_date": [TARGET]}
            ),
            "errors": [],
            "attempt_count": 2,
        },
    )
    result = _do_raw_fetch(Collector(), ["000001.SZ", "000002.SZ", "000003.SZ"], TARGET)
    coverage = result["source_scope_coverage"]
    assert coverage["suspended_exceptions"] == ["000002.SZ"]
    assert coverage["unexplained_missing"] == ["000003.SZ"]
    assert coverage["status"] == "failed"


def test_adj_factor_requested_scope_gap_blocks_factor_trust(monkeypatch):
    class Collector:
        def update_daily(self, *args, **kwargs):
            return {
                "status": "success",
                "mutations": [],
                "required_endpoint_missing_symbols": {
                    "daily": [],
                    "adj_factor": ["000002.SZ"],
                },
            }

    monkeypatch.setattr(
        "qsys.ops.data_coverage.fetch_suspension_evidence",
        lambda **kwargs: {
            "status": "empty",
            "suspended_dates_by_symbol": {},
            "raw_frame": pd.DataFrame(),
            "errors": [],
            "attempt_count": 1,
        },
    )
    result = _do_raw_fetch(Collector(), ["000001.SZ", "000002.SZ"], TARGET)
    coverage = result["source_scope_coverage"]

    assert coverage["status"] == "failed"
    assert coverage["unexplained_missing_by_endpoint"] == {
        "daily": [],
        "adj_factor": ["000002.SZ"],
    }


def test_suspension_exception_has_raw_receipt_and_query_failure_blocks(monkeypatch, tmp_path):
    class Collector:
        def update_daily(self, *args, **kwargs):
            return {
                "status": "success",
                "mutations": [],
                "required_endpoint_missing_symbols": {
                    "daily": ["000002.SZ"],
                    "adj_factor": ["000002.SZ"],
                },
            }

    raw = pd.DataFrame(
        {"ts_code": ["000002.SZ"], "trade_date": [TARGET], "suspend_timing": ["S"]}
    )
    monkeypatch.setattr(
        "qsys.ops.data_coverage.fetch_suspension_evidence",
        lambda **kwargs: {
            "status": "success",
            "suspended_dates_by_symbol": {"000002.SZ": {"2026-08-21"}},
            "raw_frame": raw,
            "errors": [],
            "attempt_count": 1,
        },
    )
    audit = SourceAuditStore(tmp_path / "audit" / "audit.db")
    result = _do_raw_fetch(
        Collector(), ["000001.SZ", "000002.SZ"], TARGET,
        run_id="suspension-success", audit_store=audit,
    )
    coverage = result["source_scope_coverage"]
    assert coverage["status"] == "success"
    assert coverage["suspension_receipt_id"]
    assert audit.verify_fetch_receipt(
        run_id="suspension-success", receipt_id=coverage["suspension_receipt_id"]
    )["status"] == "success"

    monkeypatch.setattr(
        "qsys.ops.data_coverage.fetch_suspension_evidence",
        lambda **kwargs: {
            "status": "failure",
            "suspended_dates_by_symbol": {},
            "raw_frame": pd.DataFrame(),
            "errors": ["supplier unavailable"],
            "attempt_count": 1,
        },
    )
    failed = _do_raw_fetch(
        Collector(), ["000001.SZ", "000002.SZ"], TARGET,
        run_id="suspension-failure", audit_store=audit,
    )["source_scope_coverage"]
    assert failed["status"] == "failed"
    assert failed["suspension_query_status"] == "failure"
    assert audit.verify_fetch_receipt(
        run_id="suspension-failure", receipt_id=failed["suspension_receipt_id"]
    )["status"] == "failed"


@pytest.mark.parametrize("supplier_status", ["success", "empty"])
def test_suspension_success_and_empty_resume_without_supplier_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, supplier_status: str,
) -> None:
    missing_symbol = "000002.SZ"

    class Collector:
        def update_daily(self, *args, **kwargs):
            return {
                "status": "success",
                "mutations": [],
                "required_endpoint_missing_symbols": {
                    "daily": [missing_symbol], "adj_factor": [missing_symbol],
                },
            }

    raw = (
        pd.DataFrame({
            "ts_code": [missing_symbol], "trade_date": [TARGET],
            "suspend_timing": ["S"],
        })
        if supplier_status == "success"
        else pd.DataFrame()
    )
    mapping = {missing_symbol: {"2026-08-21"}} if supplier_status == "success" else {}
    calls = []

    def first_fetch(**kwargs):
        calls.append(kwargs)
        return {
            "status": supplier_status,
            "suspended_dates_by_symbol": mapping,
            "raw_frame": raw,
            "errors": [],
            "attempt_count": 1,
        }

    monkeypatch.setattr("qsys.ops.data_coverage.fetch_suspension_evidence", first_fetch)
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    old_run = f"suspend-{supplier_status}-old"
    _append_run_started(audit, old_run)
    _do_raw_fetch(
        Collector(), ["000001.SZ", missing_symbol], TARGET,
        run_id=old_run, audit_store=audit,
        scope_key="csi1800", universe="csi1800",
    )
    proof = _failed_run_proof(audit, audit_root, old_run)
    old_receipt = Path(proof["receipt_path"])
    old_bytes = old_receipt.read_bytes()
    assert len(calls) == 1

    def forbidden(**kwargs):
        calls.append(kwargs)
        raise AssertionError("verified suspend_d shard must be reused")

    monkeypatch.setattr("qsys.ops.data_coverage.fetch_suspension_evidence", forbidden)
    new_run = f"suspend-{supplier_status}-new"
    _append_run_started(audit, new_run)
    result = _do_raw_fetch(
        Collector(), ["000001.SZ", missing_symbol], TARGET,
        run_id=new_run, audit_store=audit, resume_proof=proof,
        scope_key="csi1800", universe="csi1800",
    )
    assert len(calls) == 1
    assert result["source_scope_coverage"]["suspension_query_status"] == supplier_status
    reused = [
        event["payload"]
        for event in audit.run_evidence_summary(new_run)["events"]
        if event["event_type"] == "fetch_shard_reused"
    ]
    assert [event["endpoint"] for event in reused] == ["suspend_d"]
    assert old_receipt.read_bytes() == old_bytes


@pytest.mark.parametrize("damage", ["tamper", "missing"])
def test_bad_suspension_payload_refetches_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    missing_symbol = "000002.SZ"

    class Collector:
        def update_daily(self, *args, **kwargs):
            return {
                "status": "success", "mutations": [],
                "required_endpoint_missing_symbols": {
                    "daily": [missing_symbol], "adj_factor": [missing_symbol],
                },
            }

    raw = pd.DataFrame({
        "ts_code": [missing_symbol], "trade_date": [TARGET],
        "suspend_timing": ["S"],
    })

    def evidence(**_kwargs):
        return {
            "status": "success",
            "suspended_dates_by_symbol": {missing_symbol: {"2026-08-21"}},
            "raw_frame": raw,
            "errors": [],
            "attempt_count": 1,
        }

    monkeypatch.setattr("qsys.ops.data_coverage.fetch_suspension_evidence", evidence)
    audit_root = tmp_path / "audit"
    audit = SourceAuditStore(audit_root / "audit.db")
    old_run = f"suspend-bad-{damage}"
    _append_run_started(audit, old_run)
    result = _do_raw_fetch(
        Collector(), ["000001.SZ", missing_symbol], TARGET,
        run_id=old_run, audit_store=audit,
        scope_key="csi1800", universe="csi1800",
    )
    proof = _failed_run_proof(audit, audit_root, old_run)
    with sqlite3.connect(audit_root / "audit.db") as conn:
        relative = conn.execute(
            "SELECT payload_path FROM fetch_receipts WHERE receipt_id=?",
            (result["source_scope_coverage"]["suspension_receipt_id"],),
        ).fetchone()[0]
    payload = tmp_path / relative
    if damage == "tamper":
        payload.write_bytes(payload.read_bytes() + b"tampered")
    else:
        payload.unlink()

    calls = []

    def refetch(**kwargs):
        calls.append(kwargs)
        return evidence(**kwargs)

    monkeypatch.setattr("qsys.ops.data_coverage.fetch_suspension_evidence", refetch)
    new_run = f"suspend-refetch-{damage}"
    _append_run_started(audit, new_run)
    fresh = _do_raw_fetch(
        Collector(), ["000001.SZ", missing_symbol], TARGET,
        run_id=new_run, audit_store=audit, resume_proof=proof,
        scope_key="csi1800", universe="csi1800",
    )
    assert len(calls) == 1
    assert fresh["source_scope_coverage"]["status"] == "success"


def _raw_required_field_gap_result(*, factor_values, low_values):
    class Store:
        def get_calendar(self):
            return pd.DataFrame({"cal_date": [TARGET], "is_open": [1]})

        def get_global_latest_date(self):
            return None

        def load_daily(self, _code):
            return None

        def save_daily(self, _frame, _code, existing_df=None):
            return []

    def api(frame):
        return lambda **_kwargs: frame.copy()

    symbols = ["000001.SZ", "000002.SZ"]
    calls = {
        "daily": api(pd.DataFrame({
            "ts_code": symbols, "trade_date": [TARGET, TARGET],
            "open": [10.0, 20.0], "high": [11.0, 21.0],
            "low": low_values, "close": [10.5, 20.5], "vol": [100.0, 200.0],
        })),
        "adj_factor": api(pd.DataFrame({
            "ts_code": symbols, "trade_date": [TARGET, TARGET],
            "adj_factor": factor_values,
        })),
        "daily_basic": api(pd.DataFrame()),
        "stk_limit": api(pd.DataFrame()),
        "moneyflow": api(pd.DataFrame()),
        "margin_detail": api(pd.DataFrame()),
        "disclosure_date": api(pd.DataFrame(columns=["ts_code", "ann_date", "actual_date"])),
    }
    for name in ("income", "balancesheet", "cashflow", "fina_indicator"):
        calls[name] = api(pd.DataFrame())
    collector = _build_daily_collector(Store(), calls)
    return _do_raw_fetch(collector, symbols, TARGET)["source_scope_coverage"]


def test_all_nan_factor_blocks_factor_trust_from_raw_response():
    coverage = _raw_required_field_gap_result(
        factor_values=[float("nan"), float("nan")], low_values=[9.0, 19.0]
    )
    assert coverage["status"] == "failed"
    assert coverage["required_field_missing_symbols"]["adj_factor"]["factor"] == [
        "000001.SZ", "000002.SZ"
    ]


def test_one_symbol_one_required_daily_field_nan_blocks_trust():
    coverage = _raw_required_field_gap_result(
        factor_values=[1.0, 1.0], low_values=[9.0, float("nan")]
    )
    assert coverage["status"] == "failed"
    assert coverage["required_field_missing_symbols"]["daily"]["low"] == [
        "000002.SZ"
    ]


def test_suspend_d_wrong_symbol_response_fails_closed(monkeypatch):
    class Supplier:
        def suspend_d(self, **_kwargs):
            return pd.DataFrame({
                "ts_code": ["999999.SZ"], "trade_date": [TARGET]
            })

    class Collector:
        pro = Supplier()

    monkeypatch.setattr("qsys.data.collector.TushareCollector", Collector)
    result = fetch_suspension_evidence(
        symbols={"000001.SZ"}, start_date=TARGET, end_date=TARGET
    )
    assert result["status"] == "partial"
    assert result["suspended_dates_by_symbol"] == {}
    assert "symbol mismatch" in result["errors"][0]


def test_financial_daily_fetch_fails_closed_without_ann_date_field():
    calls = {}
    calls["disclosure_date"] = lambda **kwargs: pd.DataFrame(
        {"ts_code": ["000001.SZ"], "actual_date": [TARGET]}
    )
    collector = _build_daily_collector(object(), calls)

    with pytest.raises(RuntimeError, match="ann_date"):
        collector._fetch_financials_for_daily(TARGET, {"000001.SZ"})


def test_candidate_financial_response_missing_ann_date_fails_closed():
    calls = {
        "disclosure_date": lambda **kwargs: pd.DataFrame(
            {"ts_code": ["000001.SZ"], "actual_date": [TARGET], "ann_date": [TARGET]}
        ),
    }
    missing_ann = lambda **kwargs: pd.DataFrame(
        {"ts_code": ["000001.SZ"], "end_date": ["20260630"]}
    )
    for name in ("income", "balancesheet", "cashflow", "fina_indicator"):
        calls[name] = missing_ann
    collector = _build_daily_collector(object(), calls)

    with pytest.raises(RuntimeError, match="ann_date"):
        collector._fetch_financials_for_daily(TARGET, {"000001.SZ"})


def test_candidate_financial_cross_date_response_is_filtered_before_merge():
    collector = TushareCollector.__new__(TushareCollector)
    collector._discover_financial_announcement_codes = lambda target, requested: {"000001.SZ"}
    collector._fetch_financials = lambda start, end, ts_code, **kwargs: pd.DataFrame(
        {
            "ts_code": [ts_code, ts_code],
            "availability_date": ["20260820", TARGET],
            "net_income": [1.0, 2.0],
        }
    )

    result = collector._fetch_financials_for_daily(TARGET, {"000001.SZ"})

    assert result["availability_date"].astype(str).tolist() == [TARGET]
    assert result["net_income"].tolist() == [2.0]


def test_no_disclosure_candidates_makes_no_financial_statement_calls():
    calls = {
        "disclosure_date": lambda **kwargs: pd.DataFrame(
            columns=["ts_code", "ann_date", "actual_date"]
        ),
    }

    def forbidden(**kwargs):
        raise AssertionError("ordinary financial endpoint must not be called")

    for name in ("income", "balancesheet", "cashflow", "fina_indicator"):
        calls[name] = forbidden
    collector = _build_daily_collector(object(), calls)

    result = collector._fetch_financials_for_daily(TARGET, {"000001.SZ"})

    assert result.empty


def test_disclosure_candidate_response_missing_filter_date_fails_closed():
    calls = {
        "disclosure_date": lambda **kwargs: pd.DataFrame(
            {"ts_code": ["000001.SZ"], "ann_date": [TARGET]}
        ),
    }
    collector = _build_daily_collector(object(), calls)

    with pytest.raises(RuntimeError, match="actual_date response missing fields"):
        collector._discover_financial_announcement_codes(
            TARGET, {"000001.SZ"}
        )
