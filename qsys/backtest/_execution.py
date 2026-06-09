"""Shared execution kernel — match, settlement, MTM, daily summary.

Extracted from BacktestRunner.  Both ``run_range`` and
``run_from_signal_cache`` delegate the execution+accounting core
to this module so that bug fixes and accounting changes apply
to both paths.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from qsys.trader.account import Account
from qsys.trader.matcher import MatchEngine


def execute_trade_day(
    account: Account,
    orders: list[dict[str, Any]],
    exec_prices: dict[str, float],
    market_status: pd.DataFrame,
    mtm_prices: dict[str, float],
    trade_date: str,
    *,
    commission: float = 0.0,
    stamp_duty: float = 0.0,
    min_commission: float = 0.0,
    slippage: float = 0.0,
    execution_price_mode: str = "open",
) -> dict[str, Any]:
    """Execute orders, settle, MTM, and return a daily_summary dict.

    Parameters
    ----------
    account:
        Trading account, mutated in-place.
    orders:
        List of order dicts (with keys: symbol, side, amount, price).
    exec_prices, market_status:
        Market data for execution.
    mtm_prices:
        Prices for end-of-day mark-to-market.
    trade_date:
        Current trade date (used in ``daily_summary`` dict).

    Returns
    -------
    dict
        Standard daily_summary row with all accounting fields.
    """
    from qsys.ops.shadow_execution import positions_frame

    # Before-state (MTM at close prices)
    pos_before = positions_frame(account, mtm_prices)
    cash_before = float(account.cash)
    mv_before = float(pos_before["market_value"].sum()) if not pos_before.empty else 0.0
    tv_before = cash_before + mv_before

    # Execute
    matcher = MatchEngine(
        commission=commission, stamp_duty=stamp_duty,
        min_commission=min_commission, slippage=slippage,
    )
    results = matcher.match(orders, account, market_status, exec_prices)
    account.settlement()

    # After-state (MTM)
    pos_after = positions_frame(account, mtm_prices)
    mv_after = float(pos_after["market_value"].sum()) if not pos_after.empty else 0.0
    cash_after = float(account.cash)
    tv_after = cash_after + mv_after

    # Accounting
    buy_count = sum(1 for o in orders if o["side"] == "buy")
    sell_count = sum(1 for o in orders if o["side"] == "sell")
    filled_count = sum(1 for r in results if r["status"] == "filled")
    rejected_count = sum(1 for r in results if r["status"] == "rejected")
    turnover = float(sum(
        float(r.get("filled_amount", 0)) * float(r.get("deal_price", 0.0))
        for r in results if r["status"] == "filled"
    ))

    return {
        "trade_date": trade_date,
        "execution_price_mode": execution_price_mode,
        "cash_before": cash_before,
        "market_value_before": mv_before,
        "total_value_before": tv_before,
        "cash_after": cash_after,
        "market_value_after": mv_after,
        "total_value_after": tv_after,
        "order_count": len(orders),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "filled_count": filled_count,
        "rejected_count": rejected_count,
        "turnover": turnover,
        "position_count": len(account.positions),
        "status": "success",
    }
