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


EXECUTION_ARTIFACT_SCHEMA_VERSION = "backtest_executions_v1"
EXECUTION_ARTIFACT_COLUMNS = [
    "execution_id",
    "trade_date",
    "sequence",
    "instrument",
    "side",
    "execution_phase",
    "trade_reason",
    "requested_qty",
    "requested_price",
    "execution_price_mode",
    "reference_price",
    "status",
    "filled_qty",
    "deal_price",
    "gross_amount",
    "commission",
    "tax",
    "total_fee",
    "rejection_reason",
]


def _append_execution_rows(
    collector: list[dict[str, Any]],
    *,
    orders: list[dict[str, Any]],
    results: list[dict[str, Any]],
    exec_prices: dict[str, float],
    trade_date: str,
    execution_price_mode: str,
    stamp_duty: float,
) -> None:
    """Append one flat, deterministic audit row per simulated order."""
    for order, match_result in zip(orders, results):
        instrument = str(order.get("symbol") or "")
        side = str(order.get("side") or "")
        status = str(match_result.get("status") or "unknown")
        filled_qty = int(match_result.get("filled_amount", 0) or 0)
        deal_price = float(match_result.get("deal_price", 0.0) or 0.0)
        gross_amount = float(filled_qty * deal_price)
        total_fee = float(match_result.get("fee", 0.0) or 0.0)
        tax = float(gross_amount * stamp_duty) if status == "filled" and side == "sell" else 0.0
        commission = max(total_fee - tax, 0.0)
        sequence = len(collector)
        collector.append({
            "execution_id": f"{trade_date}:{sequence:06d}:{instrument}:{side}",
            "trade_date": trade_date,
            "sequence": sequence,
            "instrument": instrument,
            "side": side,
            "execution_phase": str(order.get("execution_phase") or "rebalance"),
            "trade_reason": str(order.get("trade_reason") or "rebalance_to_target_weight"),
            "requested_qty": int(order.get("amount", 0) or 0),
            "requested_price": float(order.get("price", 0.0) or 0.0),
            "execution_price_mode": execution_price_mode,
            "reference_price": float(exec_prices.get(instrument, 0.0) or 0.0),
            "status": status,
            "filled_qty": filled_qty,
            "deal_price": deal_price,
            "gross_amount": gross_amount,
            "commission": commission,
            "tax": tax,
            "total_fee": total_fee,
            "rejection_reason": str(match_result.get("reason") or ""),
        })


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
    execution_collector: list[dict[str, Any]] | None = None,
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
    if execution_collector is not None:
        _append_execution_rows(
            execution_collector,
            orders=orders,
            results=results,
            exec_prices=exec_prices,
            trade_date=trade_date,
            execution_price_mode=execution_price_mode,
            stamp_duty=stamp_duty,
        )
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
