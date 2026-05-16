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
