from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# xtquant order status codes → normalized server status strings.
# Reference: xtquant constant definitions.
#   0 = order placed (not filled yet)
#   1 = partially filled
#   2 = fully filled
#   3 = cancelled
#   4 = rejected
#   5 = pending (not yet submitted)
#   6 = awaiting submission
XT_STATUS_MAP: dict[int, str] = {
    0: "submitted",
    1: "partial",
    2: "filled",
    3: "canceled",
    4: "rejected",
    5: "submitted",
    6: "submitted",
}


def map_order_status(xt_status: int) -> str:
    """Map xtquant integer order status to the normalized server status string."""
    return XT_STATUS_MAP.get(xt_status, "submitted")


# ── Internal helpers ───────────────────────────────────────────────────


def _get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Get a value from an object supporting both dict and attribute access.

    Tries each *key* in order; returns the first non-None value found.
    Falls back to *default* if every key returns None.
    """
    for key in keys:
        if isinstance(obj, dict):
            val = obj.get(key)
        else:
            val = getattr(obj, key, None)
        if val is not None:
            return val
    return default


# ── Account ────────────────────────────────────────────────────────────


def map_account(xt_account: Any) -> dict[str, Any]:
    """Normalize an xtquant account dict to the server's /account contract."""
    account_id = _get(xt_account, "account_id", "asset_id") or ""
    total_assets = float(_get(xt_account, "total_assets") or 0.0)
    available_cash = float(_get(xt_account, "available_cash") or 0.0)
    market_value = float(_get(xt_account, "market_value") or 0.0)
    frozen_cash = float(_get(xt_account, "frozen_cash") or 0.0)
    daily_pnl = float(_get(xt_account, "daily_pnl") or 0.0)
    cash = float(_get(xt_account, "cash") or 0.0)
    return {
        "account_id": account_id,
        "account_name": account_id,
        "cash": cash,
        "total_assets": total_assets,
        "available_cash": available_cash,
        "market_value": market_value,
        "frozen_cash": frozen_cash,
        "daily_pnl": daily_pnl,
        "updated_at": str(_get(xt_account, "updated_at") or _now()),
    }


def _fallback_account(account_id: str = "") -> dict[str, Any]:
    """Return a zeroed-out account dict when xtquant is unavailable."""
    aid = account_id or "miniqmt_account"
    return {
        "account_id": aid,
        "account_name": aid,
        "cash": 0.0,
        "total_assets": 0.0,
        "available_cash": 0.0,
        "market_value": 0.0,
        "frozen_cash": 0.0,
        "daily_pnl": 0.0,
        "updated_at": _now(),
    }


# ── Position ───────────────────────────────────────────────────────────


def map_position(xt_position: Any) -> dict[str, Any]:
    """Normalize an xtquant position dict to the server's /positions contract."""
    volume = int(_get(xt_position, "volume") or 0)
    available = int(
        _get(xt_position, "available_volume", "can_use_volume") or volume
    )
    market_price = float(_get(xt_position, "market_price", "last_price") or 0.0)
    market_value = float(_get(xt_position, "market_value") or 0.0)
    if market_value == 0.0 and volume > 0:
        market_value = round(volume * market_price, 2)
    cost_price = float(_get(xt_position, "cost_price", "open_cost") or 0.0)
    cost_basis = cost_price * volume
    pnl = float(_get(xt_position, "pnl", "floating_pnl") or 0.0)
    if pnl == 0.0 and cost_basis > 0:
        pnl = round(market_value - cost_basis, 2)
    pnl_pct = round(pnl / cost_basis, 6) if cost_basis else 0.0
    symbol = str(_get(xt_position, "symbol", "stock_code") or "")
    update_time = str(_get(xt_position, "update_time") or _now())
    return {
        # Canonical fields consumed by PositionSnapshot.from_dict()
        "symbol": symbol,
        "total_amount": volume,
        "sellable_amount": available,
        "avg_cost": cost_price,
        "last_price": market_price,
        "market_value": market_value,
        # Raw / debug fields kept for backward compat
        "volume": volume,
        "available_volume": available,
        "cost_price": cost_price,
        "market_price": market_price,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "update_time": update_time,
    }


# ── Order ──────────────────────────────────────────────────────────────


def map_order(xt_order: Any) -> dict[str, Any]:
    """Normalize an xtquant order dict to the server's /orders contract."""
    status_code = int(_get(xt_order, "order_status", "status") or 0)
    quantity = int(_get(xt_order, "total_quantity", "quantity") or 0)
    filled = int(_get(xt_order, "traded_quantity", "filled_quantity") or 0)
    return {
        "broker_order_id": str(_get(xt_order, "order_id", "broker_order_id") or ""),
        "intent_id": str(_get(xt_order, "intent_id") or ""),
        "symbol": str(_get(xt_order, "stock_code", "symbol") or ""),
        "side": str(_get(xt_order, "side") or "BUY").upper(),
        "quantity": quantity,
        "order_type": str(_get(xt_order, "order_type") or "LIMIT").upper(),
        "limit_price": float(_get(xt_order, "price") or 0.0),
        "status": map_order_status(status_code),
        "filled_quantity": filled,
        "remaining_quantity": max(0, quantity - filled),
        "filled_price": float(
            _get(xt_order, "trade_price", "traded_price", "filled_price") or 0.0
        ),
        "time_in_force": str(_get(xt_order, "time_in_force") or "DAY").upper(),
        "reason": str(_get(xt_order, "reason", "message") or ""),
        "submitted_at": str(_get(xt_order, "order_time", "submitted_at") or _now()),
        "updated_at": str(_get(xt_order, "updated_at") or _now()),
    }


# ── Trade ──────────────────────────────────────────────────────────────


def map_trade(xt_trade: Any) -> dict[str, Any]:
    """Normalize an xtquant trade record to the server's /trades contract."""
    quantity = int(_get(xt_trade, "trade_quantity", "quantity") or 0)
    price = float(_get(xt_trade, "trade_price", "price") or 0.0)
    amount = float(_get(xt_trade, "trade_amount", "amount") or 0.0)
    if amount == 0.0 and quantity > 0 and price > 0:
        amount = round(quantity * price, 2)
    return {
        "trade_id": str(_get(xt_trade, "trade_id", "broker_trade_id") or ""),
        "broker_order_id": str(_get(xt_trade, "order_id", "broker_order_id") or ""),
        "intent_id": str(_get(xt_trade, "intent_id") or ""),
        "symbol": str(_get(xt_trade, "stock_code", "symbol") or ""),
        "side": str(_get(xt_trade, "side") or "BUY").upper(),
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "executed_at": str(_get(xt_trade, "trade_time", "executed_at") or _now()),
    }


# ── Helpers ────────────────────────────────────────────────────────────


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
