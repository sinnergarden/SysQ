from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pandas as pd
import pytest

from qsys.data.collector import TushareCollector, _supplier_request_sha256
from qsys.data.source_audit import SourceAuditStore, stable_scope_hash
from qsys.data.storage import StockDataStore
from qsys.ops.data_coverage import fetch_suspension_evidence
from scripts.ops.sync_csi800_daily import (
    _do_raw_fetch,
    _refresh_and_verify_changed_symbols,
    _refresh_and_verify_history_mutation_store,
)


TARGET = "20260821"


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
        "income": {"fields": "ts_code,ann_date,end_date,n_income,revenue,oper_cost"},
        "balancesheet": {"fields": "ts_code,ann_date,end_date,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab"},
        "cashflow": {"fields": "ts_code,ann_date,end_date,n_cashflow_act"},
        "fina_indicator": {"fields": "ts_code,ann_date,end_date,roe,grossprofit_margin,debt_to_assets,current_ratio,q_dtprofit,q_gr_yoy"},
        "disclosure_date": {"fields": "ts_code,ann_date,end_date,pre_date,actual_date"},
    }
    collector.financial_cols = ["net_income", "revenue", "oper_cost", "total_assets", "equity", "total_cur_assets", "total_cur_liab", "roe", "op_cashflow", "q_dt_profit", "q_gr_yoy", "grossprofit_margin", "debt_to_assets", "current_ratio"]
    collector.moneyflow_fields = ["buy_elg_amount", "sell_elg_amount", "net_mf_amount"]
    collector._moneyflow_derived = ["big_inflow", "net_inflow"]
    collector.margin_cols = ["margin_balance"]
    collector._expected_extra_cols = []
    collector._numeric_extra_cols = []
    collector._non_numeric_cols = []
    collector._percent_financial_cols = {"roe", "grossprofit_margin", "debt_to_assets", "current_ratio"}
    collector._percent_like_threshold = 3.0
    collector._get_interface_api = lambda name: calls[
        collector._collector_interfaces.get(name, {}).get("interface", name)
    ]
    collector._validate_and_clean = lambda frame, code, ignore_columns=None: frame
    return collector


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
        "n_income": [2.0], "revenue": [10.0], "oper_cost": [6.0],
    }))
    calls["balancesheet"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
        "total_assets": [20.0], "total_hldr_eqy_exc_min_int": [8.0],
        "total_cur_assets": [12.0], "total_cur_liab": [6.0],
    }))
    calls["cashflow"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
        "n_cashflow_act": [4.0],
    }))
    calls["fina_indicator"] = api(pd.DataFrame({
        "ts_code": ["000001.SZ"], "ann_date": [TARGET], "end_date": ["20260630"],
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
        # and a target-only range; no unsupported market-wide ann_date query.
        assert calls[name].last_kwargs["ts_code"] == "000001.SZ"
        assert calls[name].last_kwargs["start_date"] == TARGET
        assert calls[name].last_kwargs["end_date"] == TARGET
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
            "ts_code,ann_date,end_date,n_income,revenue",
            {"ts_code": "000001.SZ", "start_date": TARGET, "end_date": TARGET},
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "ann_date": [TARGET],
                "end_date": ["20260630"], "n_income": [2.0],
            }),
            pd.DataFrame({
                "ts_code": ["000001.SZ"], "ann_date": [TARGET],
                "end_date": ["20260630"], "n_income": [2.0], "revenue": [10.0],
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
            return pd.DataFrame({**common, **values[endpoint]})

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

        def convert_fix_symbols(self, symbols, refresh_universes=None):
            self.fix_calls.append(list(symbols))
            return {"status": "success"}

        def get_features(self, symbols, fields, start_time=None, end_time=None):
            index = pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2020-01-02"), symbols[0])],
                names=["datetime", "instrument"],
            )
            return pd.DataFrame({"$close": [11.0]}, index=index)

    adapter = Adapter()
    result = _refresh_and_verify_history_mutation_store(
        adapter, Store(), audit, run_id, apply=True
    )

    assert queried_symbols == ["000001.SZ", "000002.SZ"]
    assert adapter.fix_calls == [["000002.SZ"]]
    assert result["changed_symbols"] == ["000001.SZ", "000002.SZ"]
    assert result["verified_value_count"] == 2
    assert result["status"] == "success"


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
    collector._fetch_financials = lambda start, end, ts_code: pd.DataFrame(
        {
            "ts_code": [ts_code, ts_code],
            "ann_date": ["20260820", TARGET],
            "end_date": ["20260331", "20260630"],
            "net_income": [1.0, 2.0],
        }
    )

    result = collector._fetch_financials_for_daily(TARGET, {"000001.SZ"})

    assert result["ann_date"].astype(str).tolist() == [TARGET]
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
