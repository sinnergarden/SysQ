from __future__ import annotations

import pandas as pd
import pytest

from qsys.backtest.accounting import BacktestAccount, ValuationState
from qsys.backtest._execution import execute_trade_day


def _status(symbol: str = "A", **kwargs) -> pd.DataFrame:
    row = {"is_suspended": False, "is_limit_up": False, "is_limit_down": False}
    row.update(kwargs)
    return pd.DataFrame([row], index=[symbol])


def test_t1_is_date_aware_across_two_calls() -> None:
    account = BacktestAccount(10_000)
    execute_trade_day(account, [{"symbol": "A", "side": "buy", "amount": 100}],
                      {"A": 10}, _status(), {"A": 10}, "2026-01-01",
                      min_commission=0)
    second = execute_trade_day(account, [{"symbol": "A", "side": "sell", "amount": 100}],
                               {"A": 10}, _status(), {"A": 10}, "2026-01-01",
                               min_commission=0)
    assert second["filled_count"] == 0
    third = execute_trade_day(account, [{"symbol": "A", "side": "sell", "amount": 100}],
                              {"A": 10}, _status(), {"A": 10}, "2026-01-02",
                              min_commission=0)
    assert third["filled_count"] == 1


def test_missing_close_carries_last_legal_mark_and_unpriced_fails_closed() -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    marks = ValuationState()
    marks.update({"A": 10}, "2026-01-01")
    stale = marks.mark_to_market(account, "2026-01-02").iloc[0]
    assert stale["last_price"] == 10
    assert bool(stale["stale_price"]) is True
    assert stale["stale_days"] == 1
    with pytest.raises(ValueError):
        ValuationState().mark_to_market(account, "2026-01-01")


def test_cached_raw_price_is_adjusted_until_next_legal_close() -> None:
    marks = ValuationState({"A": 10}, "2026-01-01")
    adjusted = marks.adjust_for_corporate_action(
        {"instrument": "A", "event_type": "cash_dividend", "cash_per_share": 1}, True
    )
    assert adjusted["price_after"] == 9
    assert marks.value_position("A", "2026-01-02")["price_date"] == "2026-01-01"
    marks.adjust_for_corporate_action(
        {"instrument": "A", "event_type": "split", "share_multiplier": 2}, True
    )
    assert marks.value_position("A", "2026-01-02")["price"] == 4.5
    marks.update({"A": 4.6}, "2026-01-02")
    assert marks.value_position("A", "2026-01-02")["price_date"] == "2026-01-02"


def test_seed_asof_preserves_per_instrument_dates_and_rejects_future() -> None:
    marks = ValuationState()
    count = marks.seed_asof(pd.DataFrame([
        {"instrument": "A", "close": 10, "price_date": "2026-01-01"},
        {"instrument": "B", "close": 20, "price_date": "2026-01-02"},
    ]), "2026-01-02")
    assert count == 2
    assert marks.value_position("A", "2026-01-02")["price_date"] == "2026-01-01"
    assert marks.value_position("B", "2026-01-02")["price_date"] == "2026-01-02"
    with pytest.raises(ValueError, match="future valuation seed"):
        marks.seed_asof({"C": (30, "2026-01-03")}, "2026-01-02")


def test_dividend_receivable_prevents_ex_date_drop_and_pay_date_is_transfer() -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    account.start_day("2026-01-02")
    event = {
        "event_id": "d1", "instrument": "A", "effective_date": "2026-01-02",
        "event_type": "cash_dividend", "cash_per_share": 1.0,
        "settlement_date": "2026-01-04",
    }
    account.apply_corporate_action(event)
    assert account.cash == 9_000
    assert account.total_receivable == 100
    assert account.get_total_equity({"A": 9}) == 10_000
    income = account.corporate_action_income
    account.start_day("2026-01-04")
    assert account.cash == 9_100
    assert account.total_receivable == 0
    assert account.corporate_action_income == income


def test_repeated_corporate_action_is_idempotent_in_ledger() -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 0, "buy")
    account.start_day("2026-01-02")
    event = {
        "event_id": "d1", "instrument": "A", "effective_date": "2026-01-02",
        "event_type": "cash_dividend", "cash_per_share": 1.0,
        "settlement_date": "2026-01-04",
    }

    first = account.apply_corporate_action(event)
    ledger_rows = account.corporate_action_ledger_rows
    second = account.apply_corporate_action(event)

    assert first["status"] == "applied"
    assert second == {"event_id": "d1", "status": "already_applied"}
    assert account.corporate_action_ledger_rows == ledger_rows
    assert account.dividend_receivable == pytest.approx(100.0)


def test_realized_and_unrealized_use_fee_in_basis() -> None:
    account = BacktestAccount(10_000)
    account.start_day("2026-01-01")
    account.update_after_deal("A", 100, 10, 5, "buy")
    account.start_day("2026-01-02")
    assert account.unrealized_pnl({"A": 11}) == pytest.approx(95)
    account.update_after_deal("A", 50, 12, 5, "sell")
    assert account.realized_trade_pnl == pytest.approx(92.5)
