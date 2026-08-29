from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qsys.config import cfg
from qsys.data.collector import TushareCollector
from qsys.data.source_audit import SourceAuditStore
from qsys.data.storage import StockDataStore
from qsys.ops.industry_sync import (
    BAK_BASIC_ROW_LIMIT,
    fetch_audited_daily_industry,
    fetch_audited_history_industry,
    UNCLASSIFIED_INDUSTRY,
    validate_history_industry_response,
)

TARGET = "20260821"
HISTORY_START = "20180313"
SYMBOL = "000001.SZ"


@pytest.fixture
def configured_store(tmp_path: Path):
    original = cfg.dirs.copy()
    cfg.dirs = {
        "root": tmp_path / "data",
        "canonical_dir": tmp_path / "data" / "canonical" / "daily",
        "meta": tmp_path / "data" / "meta",
        "qlib_bin": tmp_path / "data" / "qlib",
    }
    for value in cfg.dirs.values():
        Path(value).mkdir(parents=True, exist_ok=True)
    store = StockDataStore()
    store.save_meta_stocks(pd.DataFrame({
        "ts_code": [SYMBOL], "symbol": ["000001"], "name": ["A"],
        "area": ["SZ"], "industry": ["NewSector"], "market": ["主板"],
        "list_date": ["19910403"],
    }))
    pd.DataFrame({
        "ts_code": [SYMBOL, SYMBOL],
        "trade_date": [HISTORY_START, TARGET],
        "close": [10.0, 11.0],
    }).to_feather(store.canonical_dir / f"{SYMBOL}.feather")
    try:
        yield store, tmp_path / "data"
    finally:
        cfg.dirs = original


def _response() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": [SYMBOL] * 4,
        "trade_date": ["20151231", HISTORY_START, TARGET, "20260822"],
        "industry": ["HistoricalOnly", "OldSector", "NewSector", "NewSector"],
    })


def _collector(store: StockDataStore, response: pd.DataFrame | list[pd.DataFrame]):
    calls: list[dict] = []
    responses = response if isinstance(response, list) else [response]

    def api(**kwargs):
        calls.append(dict(kwargs))
        return responses[min(len(calls) - 1, len(responses) - 1)].copy()

    collector = TushareCollector.__new__(TushareCollector)
    collector.store = store
    collector.max_retries = 1
    collector._collector_interfaces = {}
    collector.pro = SimpleNamespace(bak_basic=api)
    collector._fetch_with_retry = lambda func, **kwargs: func(**kwargs)
    return collector, calls


def _started(audit: SourceAuditStore, run_id: str) -> None:
    audit.append_event(run_id, "run_started", {
        "entrypoint": "scripts/data_sync.py", "universe": "csi1800",
        "target_date": TARGET, "range_start": HISTORY_START,
    })


def _proof(audit: SourceAuditStore, root: Path, run_id: str) -> dict:
    audit.record_crash_receipt(
        run_id=run_id, receipt_root=root / "audit" / "source_runs",
        entrypoint="scripts/data_sync.py", error="injected stage boundary",
    )
    return audit.validate_resume_run(
        resume_from_run_id=run_id, expected_entrypoint="scripts/data_sync.py",
        universe="csi1800", target_date=TARGET, range_start=HISTORY_START,
    )


def test_history_validator_keeps_valid_outside_projection_and_rejects_semantic_gaps() -> None:
    assert validate_history_industry_response(
        pd.DataFrame(), symbol=SYMBOL, target_date=TARGET, required_dates=set()
    ) is None
    assert validate_history_industry_response(
        pd.DataFrame(), symbol=SYMBOL, target_date=TARGET, required_dates={TARGET}
    )["reason"] == "required_history_empty"
    frame = _response()
    assert validate_history_industry_response(
        frame, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START, TARGET},
    ) is None
    bad = frame.copy()
    bad.loc[1, "ts_code"] = "000002.SZ"
    assert validate_history_industry_response(
        bad, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START, TARGET},
    )["reason"] == "response_symbol_out_of_scope"
    duplicate = pd.concat([frame, frame.iloc[[1]]], ignore_index=True)
    assert validate_history_industry_response(
        duplicate, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START, TARGET},
    )["reason"] == "response_duplicate_key"
    truncated = pd.concat([frame.iloc[[1]]] * BAK_BASIC_ROW_LIMIT, ignore_index=True)
    assert validate_history_industry_response(
        truncated, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START},
    )["reason"] == "possible_supplier_truncation"
    changed_after_target = frame.copy()
    changed_after_target.loc[changed_after_target["trade_date"] == "20260822", "industry"] = "FutureRename"
    assert validate_history_industry_response(
        changed_after_target, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START, TARGET},
    ) is None
    no_prior_state = frame.loc[frame["trade_date"] >= TARGET]
    assert validate_history_industry_response(
        no_prior_state, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START},
    ) is None
    all_unclassified = frame.copy()
    all_unclassified["industry"] = None
    assert validate_history_industry_response(
        all_unclassified, symbol=SYMBOL, target_date=TARGET,
        required_dates={HISTORY_START},
    )["reason"] == "required_history_unclassified"


def test_history_stage_projects_latest_prior_industry_without_future_fill(
    configured_store, monkeypatch,
) -> None:
    store, root = configured_store
    response = _response()
    response.loc[response["trade_date"] == TARGET, "trade_date"] = "20260820"
    collector, calls = _collector(store, response)
    monkeypatch.setattr("qsys.data.collector.time.sleep", lambda _seconds: None)
    audit = SourceAuditStore(root / "audit" / "audit.db")

    summary, receipts = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id="industry-asof", audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )

    assert summary["status"] == "success"
    assert len(receipts) == 1
    assert len(calls) == 1
    canonical = pd.read_feather(store.canonical_dir / f"{SYMBOL}.feather")
    assert canonical["industry"].tolist() == ["OldSector", "NewSector"]


def test_history_stage_ignores_null_state_before_first_canonical_date(
    configured_store, monkeypatch,
) -> None:
    store, root = configured_store
    response = _response()
    response.loc[len(response)] = {
        "ts_code": SYMBOL,
        "trade_date": "20170101",
        "industry": None,
    }
    collector, calls = _collector(store, response)
    monkeypatch.setattr("qsys.data.collector.time.sleep", lambda _seconds: None)
    audit = SourceAuditStore(root / "audit" / "audit.db")

    summary, receipts = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id="industry-precanonical-null", audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )

    assert summary["status"] == "success"
    assert len(receipts) == 1
    assert len(calls) == 1


def test_history_stage_marks_leading_unknown_without_future_fill(
    configured_store, monkeypatch,
) -> None:
    store, root = configured_store
    response = _response()
    response.loc[response["trade_date"] == HISTORY_START, "industry"] = None
    collector, calls = _collector(store, response)
    monkeypatch.setattr("qsys.data.collector.time.sleep", lambda _seconds: None)
    audit = SourceAuditStore(root / "audit" / "audit.db")

    summary, receipts = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id="industry-leading-unknown", audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )

    assert summary["status"] == "success"
    assert summary["unclassified_row_count"] == 1
    assert len(receipts) == 1
    assert len(calls) == 1
    canonical = pd.read_feather(store.canonical_dir / f"{SYMBOL}.feather")
    assert canonical["industry"].tolist() == [UNCLASSIFIED_INDUSTRY, "NewSector"]


def test_canonical_merge_replaces_legacy_and_a_later_receipt_can_correct(configured_store) -> None:
    store, _root = configured_store
    path = store.canonical_dir / f"{SYMBOL}.feather"
    legacy = pd.read_feather(path)
    legacy["industry"] = "WrongLegacy"
    legacy.to_feather(path)
    mutations = store.merge_daily_industry(
        _response(), SYMBOL,
        source_run_id="failed-before-terminal-run-a",
        source_receipt_id="receipt-a",
    )
    result = pd.read_feather(path)
    assert result["close"].tolist() == [10.0, 11.0]
    assert result["industry"].tolist() == ["OldSector", "NewSector"]
    assert {item["fields"][0] for item in mutations} == {"industry"}
    conflict = _response().copy()
    conflict.loc[conflict["trade_date"] == TARGET, "industry"] = "ChangedAgain"
    corrections = store.merge_daily_industry(
        conflict, SYMBOL, source_run_id="corrective-run-b", source_receipt_id="receipt-b"
    )
    corrected = pd.read_feather(path)
    assert corrected.loc[1, "industry"] == "ChangedAgain"
    assert corrected.loc[1, "industry_source_run_id"] == "corrective-run-b"
    target_mutation = next(item for item in corrections if item["date_start"] == TARGET)
    assert target_mutation["mutation_type"] == "update"
    assert target_mutation["fields"] == ["industry"]


def test_history_stage_all_history_cutoff_and_crash_resume_multihop(
    configured_store, monkeypatch,
) -> None:
    store, root = configured_store
    sleeps = []
    monkeypatch.setattr("qsys.data.collector.time.sleep", sleeps.append)
    collector, calls = _collector(store, _response())
    audit = SourceAuditStore(root / "audit" / "audit.db")
    run_a = "industry-run-a"
    _started(audit, run_a)
    original_record = audit.record_mutations
    audit.record_mutations = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("after atomic feather"))
    with pytest.raises(RuntimeError, match="after atomic feather"):
        fetch_audited_history_industry(
            collector, [SYMBOL], TARGET, is_history_repair=True,
            run_id=run_a, audit_store=audit, resume_proof=None,
            scope_key="csi1800", universe="csi1800",
        )
    audit.record_mutations = original_record
    assert calls == [{"ts_code": SYMBOL, "fields": "trade_date,ts_code,industry"}]
    assert sleeps == [0.35]
    canonical = pd.read_feather(store.canonical_dir / f"{SYMBOL}.feather")
    assert canonical["industry"].tolist() == ["OldSector", "NewSector"]

    proof_a = _proof(audit, root, run_a)
    run_b = "industry-run-b"
    _started(audit, run_b)
    summary_b, receipts_b = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id=run_b, audit_store=audit, resume_proof=proof_a,
        scope_key="csi1800", universe="csi1800",
    )
    assert summary_b["status"] == "success"
    assert summary_b["excluded_before_rows"] == 1
    assert summary_b["excluded_future_rows"] == 1
    assert len(calls) == 1
    assert sleeps == [0.35]

    proof_b = _proof(audit, root, run_b)
    run_c = "industry-run-c"
    _started(audit, run_c)
    summary_c, receipts_c = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id=run_c, audit_store=audit, resume_proof=proof_b,
        scope_key="csi1800", universe="csi1800",
    )
    assert summary_c["status"] == "success"
    assert len(calls) == 1
    with sqlite3.connect(audit.db_path) as connection:
        scope = json.loads(connection.execute(
            "SELECT requested_scope_json FROM fetch_receipts WHERE receipt_id=?",
            (receipts_c[0],),
        ).fetchone()[0])
        mutation = connection.execute(
            "SELECT endpoint,fetch_receipt_id,mutation_type FROM canonical_mutations WHERE run_id=?",
            (run_c,),
        ).fetchone()
        link = connection.execute(
            """SELECT dataset,field_name FROM field_receipt_links
               WHERE run_id=? AND receipt_id=?""",
            (run_c, receipts_c[0]),
        ).fetchone()
    assert scope["query_axis"] == "all_history"
    assert scope["availability_cutoff"] == TARGET
    assert scope["request_variant"] == "history_bak_basic_industry_v1"
    assert mutation == ("bak_basic", receipts_c[0], "noop")
    assert link == ("canonical_daily", "industry")
    assert receipts_b[0] != receipts_c[0]
    canonical = pd.read_feather(store.canonical_dir / f"{SYMBOL}.feather")
    assert set(canonical["industry_source_run_id"].dropna()) == {run_a}
    reused = [
        event["payload"] for event in audit.run_evidence_summary(run_c)["events"]
        if event["event_type"] == "fetch_shard_reused"
    ]
    assert len(reused) == 1
    assert reused[0]["resume_from_run_id"] == run_b
    assert reused[0]["source_receipt_id"] == receipts_b[0]
    assert reused[0]["receipt_id"] == receipts_c[0]
    assert audit.evaluate_history_field_receipts(
        run_id=run_c, field_endpoints={"industry": "bak_basic"}
    )["status"] == "success"
    gates = {
        "fetch": True, "raw_payloads": True, "canonical_commit": True,
        "qlib_readback": True, "readiness": True, "contiguous_range": True,
    }
    terminal = audit.finalize_run(
        run_id=run_c, source="tushare", scope_key="csi1800",
        range_start=HISTORY_START, range_end=TARGET, fields=["industry"],
        gates=gates, receipt_root=root / "audit" / "source_runs",
        allow_initial_history=True,
        field_range_starts={"industry": HISTORY_START},
    )
    assert terminal["status"] == "trusted"


def test_semantic_partial_is_not_reused(configured_store) -> None:
    store, root = configured_store
    bad = _response()
    bad.loc[1, "ts_code"] = "000002.SZ"
    collector, calls = _collector(store, [bad, _response()])
    audit = SourceAuditStore(root / "audit" / "audit.db")
    run_a = "industry-partial-a"
    _started(audit, run_a)
    failed, _ = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id=run_a, audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert failed["status"] == "failed"
    with sqlite3.connect(audit.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM fetch_receipts WHERE run_id=?", (run_a,)
        ).fetchone()[0] == "partial"
    proof = _proof(audit, root, run_a)
    run_b = "industry-partial-b"
    _started(audit, run_b)
    recovered, _ = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id=run_b, audit_store=audit, resume_proof=proof,
        scope_key="csi1800", universe="csi1800",
    )
    assert recovered["status"] == "success"
    assert len(calls) == 2


def test_taxonomy_overlap_is_reported_and_gated_at_stage_level(configured_store) -> None:
    store, root = configured_store
    mismatched = _response()
    mismatched.loc[mismatched["trade_date"] == TARGET, "industry"] = "RecentRename"
    collector, _calls = _collector(store, mismatched)
    audit = SourceAuditStore(root / "audit" / "audit.db")
    summary, receipts = fetch_audited_history_industry(
        collector, [SYMBOL], TARGET, is_history_repair=True,
        run_id="industry-taxonomy", audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert summary["status"] == "failed"
    assert summary["failure"]["reason"] == "taxonomy_overlap_below_threshold"
    assert summary["taxonomy_comparison_count"] == 1
    assert summary["taxonomy_mismatch_count"] == 1
    with sqlite3.connect(audit.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM fetch_receipts WHERE receipt_id=?", (receipts[0],)
        ).fetchone()[0] == "success"
    canonical = pd.read_feather(store.canonical_dir / f"{SYMBOL}.feather")
    assert "industry" not in canonical.columns


def test_daily_csi1800_is_one_market_call_and_csi800_is_unchanged(configured_store) -> None:
    store, root = configured_store
    daily = pd.DataFrame({
        "ts_code": [SYMBOL, "999999.SZ"],
        "trade_date": [TARGET, TARGET],
        "industry": ["NewSector", "HistoricalOnly"],
    })
    collector, calls = _collector(store, daily)
    audit = SourceAuditStore(root / "audit" / "audit.db")
    summary, receipts = fetch_audited_daily_industry(
        collector, [SYMBOL], TARGET, run_id="industry-daily",
        audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert summary["status"] == "success"
    assert len(receipts) == 1
    assert calls == [{"trade_date": TARGET, "fields": "trade_date,ts_code,industry"}]
    assert pd.read_feather(store.canonical_dir / f"{SYMBOL}.feather").loc[1, "industry"] == "NewSector"
    skipped, skipped_receipts = fetch_audited_daily_industry(
        collector, [SYMBOL], TARGET, run_id="industry-csi800",
        audit_store=audit, resume_proof=None,
        scope_key="csi800", universe="csi800",
    )
    assert skipped == {"status": "not_required"}
    assert skipped_receipts == []
    assert len(calls) == 1


def test_daily_taxonomy_overlap_allows_small_mismatch_and_blocks_large_ratio(configured_store) -> None:
    store, root = configured_store
    symbols = [f"{index:06d}.SZ" for index in range(1, 21)]
    store.save_meta_stocks(pd.DataFrame({
        "ts_code": symbols, "symbol": [item[:6] for item in symbols],
        "name": symbols, "area": ["SZ"] * 20, "industry": ["Sector"] * 20,
        "market": ["主板"] * 20, "list_date": ["20000101"] * 20,
    }))
    for symbol in symbols:
        pd.DataFrame({
            "ts_code": [symbol], "trade_date": [TARGET], "close": [10.0],
        }).to_feather(store.canonical_dir / f"{symbol}.feather")

    one_mismatch = pd.DataFrame({
        "ts_code": symbols, "trade_date": [TARGET] * 20,
        "industry": ["Renamed", *(["Sector"] * 19)],
    })
    collector, _calls = _collector(store, one_mismatch)
    audit = SourceAuditStore(root / "audit" / "audit.db")
    passed, _ = fetch_audited_daily_industry(
        collector, symbols, TARGET, run_id="industry-daily-overlap-pass",
        audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert passed["status"] == "success"
    assert passed["taxonomy"]["mismatch_count"] == 1
    assert passed["taxonomy"]["match_ratio"] == 0.95

    two_mismatches = one_mismatch.copy()
    two_mismatches.loc[1, "industry"] = "AnotherRename"
    collector, _calls = _collector(store, two_mismatches)
    blocked, receipts = fetch_audited_daily_industry(
        collector, symbols, TARGET, run_id="industry-daily-overlap-block",
        audit_store=audit, resume_proof=None,
        scope_key="csi1800", universe="csi1800",
    )
    assert blocked["status"] == "failed"
    assert blocked["failure"]["reason"] == "taxonomy_overlap_below_threshold"
    assert blocked["taxonomy"]["mismatch_count"] == 2
    with sqlite3.connect(audit.db_path) as connection:
        assert connection.execute(
            "SELECT status FROM fetch_receipts WHERE receipt_id=?", (receipts[0],)
        ).fetchone()[0] == "success"
