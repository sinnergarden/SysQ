"""Shared execution kernel — match, settlement, MTM, daily summary.

Extracted from BacktestRunner.  Both ``run_range`` and
``run_from_signal_cache`` delegate the execution+accounting core
to this module so that bug fixes and accounting changes apply
to both paths.
"""

from __future__ import annotations

from typing import Any, Mapping
import math

import pandas as pd

from qsys.trader.account import Account
from qsys.trader.matcher import MatchEngine
from qsys.backtest.accounting import BacktestAccount, ValuationState


EXECUTION_ARTIFACT_SCHEMA_VERSION = "backtest_executions_v2"
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
    "order_value",
    "adv",
    "participation_rate",
    "liquidity_status",
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
            "order_value": float(match_result.get("order_value", filled_qty * float(order.get("price", 0.0) or 0.0)) or 0.0),
            "adv": float(match_result.get("adv", 0.0) or 0.0),
            "participation_rate": float(match_result.get("participation_rate", 0.0) or 0.0),
            "liquidity_status": str(match_result.get("liquidity_status") or "not_checked"),
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
    valuation_state: ValuationState | None = None,
    corporate_actions: Any = None,
    adv_by_instrument: Mapping[str, float] | None = None,
    max_participation_rate: float | None = None,
    liquidity_gate_mode: str = "warning",
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

    if liquidity_gate_mode not in {"warning", "reject"}:
        raise ValueError("liquidity_gate_mode must be 'warning' or 'reject'")
    if max_participation_rate is not None and max_participation_rate <= 0:
        raise ValueError("max_participation_rate must be positive when enabled")

    # Date-aware accounting is opt-in by account type and remains compatible
    # with the historical Account API.  A second invocation on the same date
    # deliberately does not release shares bought by the first invocation.
    if isinstance(account, BacktestAccount):
        account.start_day(trade_date)
        if corporate_actions is not None:
            # Compatibility only: canonical daily loops apply events once
            # before order generation.  If an older caller still supplies the
            # parameter, guard it per account/date so sell and buy phases do
            # not duplicate application or ledger rows.
            applied_dates = getattr(account, "_execution_ca_applied_dates", set())
            if trade_date not in applied_dates:
                events = corporate_actions.for_date(trade_date) if hasattr(corporate_actions, "for_date") else corporate_actions
                event_list = list(events)
                already = getattr(account, "_applied_corporate_actions", set())
                if not event_list or not all(str(event.get("event_id") or "") in already for event in event_list):
                    account.apply_corporate_actions(event_list, trade_date)
                applied_dates.add(trade_date)
                account._execution_ca_applied_dates = applied_dates
        if valuation_state is None:
            valuation_state = getattr(account, "_valuation_state", None)
            if valuation_state is None:
                valuation_state = ValuationState()
                account._valuation_state = valuation_state
        # The before-state is an open-decision valuation and must not consume
        # today's close.  End-of-day closes are admitted only after matching,
        # filtered to positions that actually remain in the account.

    # Before-state (MTM at close prices)
    if isinstance(account, BacktestAccount) and valuation_state is not None:
        pos_before = valuation_state.mark_to_market(account, trade_date)
    else:
        pos_before = positions_frame(account, mtm_prices)
    cash_before = float(account.cash)
    mv_before = float(pos_before["market_value"].sum()) if not pos_before.empty else 0.0
    receivable_before = float(getattr(account, "total_receivable", 0.0))
    tv_before = cash_before + receivable_before + mv_before

    # Execute
    matcher = MatchEngine(
        commission=commission, stamp_duty=stamp_duty,
        min_commission=min_commission, slippage=slippage,
    )
    # Execution prices are never filled from the valuation cache.  A missing,
    # NaN, or non-positive execution price is a hard rejection; stale marks
    # are strictly for valuation only.
    legal_exec_prices = {
        str(symbol): float(price)
        for symbol, price in exec_prices.items()
        if price is not None and math.isfinite(float(price)) and float(price) > 0
    }
    gate_results: list[dict[str, Any] | None] = []
    match_orders: list[dict[str, Any]] = []
    match_liquidity_meta: list[dict[str, Any]] = []
    for order in orders:
        symbol = str(order.get("symbol") or "")
        qty = int(order.get("amount", 0) or 0)
        # A complete exit is deliberately represented as an order even when
        # T+1 leaves no shares sellable (for example, a same-day purchase or
        # newly issued bonus shares).  Do not pass qty<=0 to MatchEngine:
        # BacktestAccount correctly rejects non-positive deals, but that
        # exception would lose the requested intent and abort the run.  Keep
        # the order in the result/collector stream with an explicit reason so
        # sequence/hash semantics remain stable and the rejection is auditable.
        if qty <= 0:
            if str(order.get("side") or "") == "sell":
                position = account.positions.get(symbol)
                sellable = int(getattr(position, "sellable_amount", 0) or 0)
                reason = (
                    "T+1: zero sellable amount"
                    if sellable <= 0
                    else "zero requested quantity"
                )
            else:
                reason = "zero requested quantity"
            gate_results.append({
                "order": order,
                "status": "rejected",
                "reason": reason,
                "order_value": 0.0,
                "adv": 0.0,
                "participation_rate": 0.0,
                "liquidity_status": "not_checked",
            })
            continue
        ref_price = legal_exec_prices.get(symbol)
        order_value = float(qty * ref_price) if ref_price is not None else 0.0
        adv = None if adv_by_instrument is None else adv_by_instrument.get(symbol)
        adv_value = float(adv) if adv is not None else float("nan")
        participation = order_value / adv_value if math.isfinite(adv_value) and adv_value > 0 else float("nan")
        liquidity_status = "not_checked"
        if max_participation_rate is not None:
            if not math.isfinite(adv_value) or adv_value <= 0:
                liquidity_status = (
                    "warning" if liquidity_gate_mode == "warning" else "rejected"
                )
                if liquidity_gate_mode == "reject":
                    gate_results.append({
                        "order": order, "status": "rejected", "reason": "Missing/invalid ADV",
                        "order_value": order_value, "adv": 0.0, "participation_rate": 0.0,
                        "liquidity_status": liquidity_status,
                    })
                    continue
            if participation > max_participation_rate:
                liquidity_status = "warning" if liquidity_gate_mode == "warning" else "rejected"
                if liquidity_gate_mode == "reject":
                    gate_results.append({
                        "order": order, "status": "rejected", "reason": "Liquidity participation exceeds limit",
                        "order_value": order_value, "adv": adv_value,
                        "participation_rate": participation, "liquidity_status": liquidity_status,
                    })
                    continue
        gate_results.append(None)
        match_liquidity_meta.append({
            "order_value": order_value, "adv": 0.0 if not math.isfinite(adv_value) else adv_value,
            "participation_rate": 0.0 if not math.isfinite(participation) else participation,
            "liquidity_status": liquidity_status,
        })
        match_orders.append(order)
    matched = matcher.match(match_orders, account, market_status, legal_exec_prices)
    results: list[dict[str, Any]] = []
    match_i = 0
    for gate_result in gate_results:
        if gate_result is not None:
            results.append(gate_result)
            continue
        result = matched[match_i]
        match_i += 1
        result.update(match_liquidity_meta[match_i - 1])
        results.append(result)
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
    if isinstance(account, BacktestAccount):
        account.settlement(trade_date)
    else:
        account.settlement()

    # After-state (MTM)
    if isinstance(account, BacktestAccount) and valuation_state is not None:
        valuation_state.update(
            {
                symbol: price for symbol, price in mtm_prices.items()
                if symbol in account.positions
            },
            trade_date,
        )
        pos_after = valuation_state.mark_to_market(account, trade_date)
    else:
        pos_after = positions_frame(account, mtm_prices)
    mv_after = float(pos_after["market_value"].sum()) if not pos_after.empty else 0.0
    cash_after = float(account.cash)
    receivable_after = float(getattr(account, "total_receivable", 0.0))
    tv_after = cash_after + receivable_after + mv_after

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
        "receivable_before": receivable_before,
        "market_value_before": mv_before,
        "total_value_before": tv_before,
        "cash_after": cash_after,
        "receivable_after": receivable_after,
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
