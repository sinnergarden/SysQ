from unittest.mock import Mock

import pandas as pd

from qsys.data.collector import TushareCollector


def _build_collector():
    collector = TushareCollector.__new__(TushareCollector)
    collector.financial_cols = [
        "net_income",
        "revenue",
        "oper_cost",
        "total_assets",
        "equity",
        "total_cur_assets",
        "total_cur_liab",
        "roe",
        "op_cashflow",
        "q_dt_profit",
        "q_gr_yoy",
        "roe_ttm",
        "grossprofit_margin",
        "debt_to_assets",
        "current_ratio",
    ]
    collector.store = Mock()
    collector.store.save_daily.return_value = []
    collector._validate_and_clean = lambda df, code, ignore_columns=None: df
    return collector


def test_save_batch_results_ffills_financial_columns_from_existing_history():
    collector = _build_collector()
    collector.store.load_daily.return_value = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260417"],
            "net_income": [123.0],
            "roe": [0.12],
        }
    )

    incoming = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"],
            "trade_date": ["20260420"],
            "net_income": [None],
            "roe": [None],
        }
    )

    collector._save_batch_results(incoming, ["000001.SZ"])

    saved = collector.store.save_daily.call_args.args[0]
    latest = saved.sort_values("trade_date").iloc[-1]
    assert latest["net_income"] == 123.0
    assert latest["roe"] == 0.12


def test_coalesced_save_matches_sequential_quarter_final_frame():
    class MemoryStore:
        def __init__(self, frame):
            self.frame = frame.copy()

        def load_daily(self, _code):
            return self.frame.copy()

        def save_daily(self, frame, _code, existing_df=None):
            self.frame = frame.copy()
            return []

    initial = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["20260331"],
        "net_income": [10.0],
        "roe": [0.1],
    })
    first = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["20260401"],
        "net_income": [None],
        "roe": [None],
    })
    second = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": ["20260701"],
        "net_income": [20.0],
        "roe": [None],
    })

    sequential = _build_collector()
    sequential.store = MemoryStore(initial)
    sequential._save_batch_results(first, ["000001.SZ"])
    sequential._save_batch_results(second, ["000001.SZ"])

    coalesced = _build_collector()
    coalesced.store = MemoryStore(initial)
    coalesced._save_batch_results(
        pd.concat([first, second], ignore_index=True), ["000001.SZ"]
    )

    pd.testing.assert_frame_equal(
        sequential.store.frame.reset_index(drop=True),
        coalesced.store.frame.reset_index(drop=True),
        check_dtype=True,
    )

    sequential_empty = _build_collector()
    sequential_empty.store = MemoryStore(pd.DataFrame())
    sequential_empty._save_batch_results(first, ["000001.SZ"])
    sequential_empty._save_batch_results(second, ["000001.SZ"])

    coalesced_empty = _build_collector()
    coalesced_empty.store = MemoryStore(pd.DataFrame())
    coalesced_empty._save_batch_results(
        pd.concat([first, second], ignore_index=True),
        ["000001.SZ"],
        fill_financial_without_existing=True,
    )

    pd.testing.assert_frame_equal(
        sequential_empty.store.frame.reset_index(drop=True),
        coalesced_empty.store.frame.reset_index(drop=True),
        check_dtype=True,
    )
