from __future__ import annotations

import pandas as pd
import pytest

from qsys.data.collector import TushareCollector
from scripts.ops.sync_csi800_daily import _do_raw_fetch


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

    assert result["status"] == "success"
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


def test_daily_fastpath_writes_only_target_and_fetches_candidate_financials():
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
    collector.update_daily("2026-08-21", codes=["000001.SZ"], force=True)

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
