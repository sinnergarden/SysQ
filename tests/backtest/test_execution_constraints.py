from __future__ import annotations

import pandas as pd

from qsys.backtest._execution import execute_trade_day
from qsys.backtest.accounting import BacktestAccount, ValuationState


def _status(**kwargs):
    row = {"is_suspended": False, "is_limit_up": False, "is_limit_down": False}
    row.update(kwargs)
    return pd.DataFrame([row], index=["A"])


def test_limits_and_suspension_reach_matcher() -> None:
    for status, reason in [(_status(is_suspended=True), "Suspended"),
                           (_status(is_limit_up=True), "Limit Up")]:
        rows = []
        out = execute_trade_day(BacktestAccount(10_000), [{"symbol": "A", "side": "buy", "amount": 100}],
                                {"A": 10}, status, {"A": 10}, "2026-01-01",
                                min_commission=0, execution_collector=rows)
        assert out["rejected_count"] == 1
        assert rows[0]["rejection_reason"] == reason


def test_liquidity_warning_and_reject() -> None:
    order = [{"symbol": "A", "side": "buy", "amount": 100}]
    warning_rows = []
    out = execute_trade_day(BacktestAccount(10_000), order.copy(), {"A": 10}, _status(), {"A": 10},
                            "2026-01-01", min_commission=0, adv_by_instrument={"A": 100},
                            max_participation_rate=0.5, liquidity_gate_mode="warning",
                            execution_collector=warning_rows)
    assert out["filled_count"] == 1 and warning_rows[0]["liquidity_status"] == "warning"
    reject_rows = []
    out = execute_trade_day(BacktestAccount(10_000), order.copy(), {"A": 10}, _status(), {"A": 10},
                            "2026-01-01", min_commission=0, adv_by_instrument={"A": 100},
                            max_participation_rate=0.5, liquidity_gate_mode="reject",
                            execution_collector=reject_rows)
    assert out["filled_count"] == 0 and reject_rows[0]["liquidity_status"] == "rejected"


def test_missing_adv_warns_and_fills_in_warning_mode_without_mutating_order() -> None:
    order = {"symbol": "A", "side": "buy", "amount": 100}
    rows = []
    out = execute_trade_day(
        BacktestAccount(10_000), [order], {"A": 10}, _status(), {"A": 10},
        "2026-01-01", min_commission=0, max_participation_rate=0.5,
        liquidity_gate_mode="warning", execution_collector=rows,
    )
    assert out["filled_count"] == 1
    assert rows[0]["liquidity_status"] == "warning"
    assert "_liquidity_meta" not in order


def test_missing_adv_is_rejected_in_reject_mode() -> None:
    rows = []
    out = execute_trade_day(
        BacktestAccount(10_000),
        [{"symbol": "A", "side": "buy", "amount": 100}],
        {"A": 10}, _status(), {"A": 10}, "2026-01-01",
        min_commission=0, max_participation_rate=0.5,
        liquidity_gate_mode="reject", execution_collector=rows,
    )
    assert out["rejected_count"] == 1
    assert rows[0]["liquidity_status"] == "rejected"


def test_zero_sellable_exit_is_rejected_without_matching_or_crashing() -> None:
    account = BacktestAccount(10_000)
    trade_date = "2026-01-01"
    account.start_day(trade_date)
    account.update_after_deal("A", 100, 10.0, 0.0, "buy")
    assert account.positions["A"].sellable_amount == 0
    valuation = ValuationState()
    valuation.update({"A": 10.0}, trade_date)

    order = {
        "symbol": "A", "side": "sell", "amount": 0,
        "execution_phase": "exit", "trade_reason": "rank_exit",
    }
    rows = []
    out = execute_trade_day(
        account, [order], {"A": 10.0}, _status(), {"A": 10.0}, trade_date,
        min_commission=0.0, execution_collector=rows,
        valuation_state=valuation,
    )

    assert out["rejected_count"] == 1
    assert out["filled_count"] == 0
    assert len(rows) == 1
    assert rows[0]["status"] == "rejected"
    assert "T+1" in rows[0]["rejection_reason"]
    assert "zero sellable" in rows[0]["rejection_reason"]
    assert account.positions["A"].total_amount == 100
