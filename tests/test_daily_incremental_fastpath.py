from __future__ import annotations

import json
import sqlite3
import pandas as pd
import pytest

from qsys.data.collector import TushareCollector
from qsys.data.source_audit import SourceAuditStore
from qsys.data.storage import StockDataStore
from qsys.ops.data_coverage import fetch_suspension_evidence
from scripts.ops.sync_csi800_daily import _do_raw_fetch, _refresh_and_verify_changed_symbols


TARGET = "20260821"


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
