from __future__ import annotations

import pandas as pd
import pytest

from qsys.backtest._execution import execute_trade_day
from qsys.trader.account import Account


def _status(*, limit_up: bool = False) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "is_suspended": [False],
            "is_limit_up": [limit_up],
            "is_limit_down": [False],
        },
        index=["A"],
    )


def test_execution_collector_records_fill_fees_and_reason() -> None:
    account = Account(init_cash=100_000.0)
    rows: list[dict] = []
    execute_trade_day(
        account,
        [{
            "symbol": "A",
            "side": "buy",
            "amount": 100,
            "price": 10.0,
            "execution_phase": "entry",
            "trade_reason": "top_n_entry",
        }],
        {"A": 10.0},
        _status(),
        {"A": 10.0},
        "2026-06-01",
        commission=0.0003,
        min_commission=5.0,
        slippage=0.001,
        execution_collector=rows,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["instrument"] == "A"
    assert row["status"] == "filled"
    assert row["filled_qty"] == 100
    assert row["reference_price"] == 10.0
    assert row["deal_price"] == pytest.approx(10.01)
    assert row["gross_amount"] == pytest.approx(1001.0)
    assert row["commission"] == 5.0
    assert row["tax"] == 0.0
    assert row["total_fee"] == 5.0
    assert row["trade_reason"] == "top_n_entry"


def test_execution_collector_records_rejection_without_fake_fill() -> None:
    account = Account(init_cash=100_000.0)
    rows: list[dict] = []
    execute_trade_day(
        account,
        [{"symbol": "A", "side": "buy", "amount": 100, "price": 10.0}],
        {"A": 10.0},
        _status(limit_up=True),
        {"A": 10.0},
        "2026-06-01",
        execution_collector=rows,
    )
    assert rows[0]["status"] == "rejected"
    assert rows[0]["filled_qty"] == 0
    assert rows[0]["deal_price"] == 0.0
    assert rows[0]["rejection_reason"] == "Limit Up"
