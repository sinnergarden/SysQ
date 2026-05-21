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


# ── Account ────────────────────────────────────────────────────────────


def map_account(xt_account: dict[str, Any]) -> dict[str, Any]:
    """Normalize an xtquant account dict to the server's /account contract."""
    return {
        "account_id": str(xt_account.get("account_id") or xt_account.get("asset_id", "")),
        "total_assets": float(xt_account.get("total_assets", 0.0) or 0.0),
        "available_cash": float(xt_account.get("available_cash", 0.0) or 0.0),
        "market_value": float(xt_account.get("market_value", 0.0) or 0.0),
        "frozen_cash": float(xt_account.get("frozen_cash", 0.0) or 0.0),
        "daily_pnl": float(xt_account.get("daily_pnl", 0.0) or 0.0),
        "updated_at": str(xt_account.get("updated_at", "") or _now()),
    }


def _fallback_account(account_id: str = "") -> dict[str, Any]:
    """Return a zeroed-out account dict when xtquant is unavailable."""
    return {
        "account_id": account_id or "miniqmt_account",
        "total_assets": 0.0,
        "available_cash": 0.0,
        "market_value": 0.0,
        "frozen_cash": 0.0,
        "daily_pnl": 0.0,
        "updated_at": _now(),
    }


# ── Position ───────────────────────────────────────────────────────────


def map_position(xt_position: dict[str, Any]) -> dict[str, Any]:
    """Normalize an xtquant position dict to the server's /positions contract."""
    volume = int(xt_position.get("volume", 0) or 0)
    available = int(xt_position.get("available_volume", xt_position.get("can_use_volume", volume)) or 0)
    market_price = float(xt_position.get("market_price", xt_position.get("last_price", 0.0)) or 0.0)
    market_value = float(xt_position.get("market_value", 0.0) or 0.0)
    if market_value == 0.0 and volume > 0:
        market_value = round(volume * market_price, 2)
    cost_price = float(xt_position.get("cost_price", xt_position.get("open_cost", 0.0)) or 0.0)
    cost_basis = cost_price * volume
    pnl = float(xt_position.get("pnl", xt_position.get("floating_pnl", 0.0)) or 0.0)
    if pnl == 0.0 and cost_basis > 0:
        pnl = round(market_value - cost_basis, 2)
    pnl_pct = round(pnl / cost_basis, 6) if cost_basis else 0.0
    return {
        "symbol": str(xt_position.get("symbol", xt_position.get("stock_code", ""))),
        "volume": volume,
        "available_volume": available,
        "cost_price": cost_price,
        "market_price": market_price,
        "market_value": market_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "update_time": str(xt_position.get("update_time", "") or _now()),
    }


# ── Order ──────────────────────────────────────────────────────────────


def map_order(xt_order: dict[str, Any]) -> dict[str, Any]:
    """Normalize an xtquant order dict to the server's /orders contract."""
    status_code = int(xt_order.get("order_status", xt_order.get("status", 0)) or 0)
    quantity = int(xt_order.get("total_quantity", xt_order.get("quantity", 0)) or 0)
    filled = int(xt_order.get("traded_quantity", xt_order.get("filled_quantity", 0)) or 0)
    return {
        "broker_order_id": str(xt_order.get("order_id", xt_order.get("broker_order_id", ""))),
        "intent_id": str(xt_order.get("intent_id", "")),
        "symbol": str(xt_order.get("stock_code", xt_order.get("symbol", ""))),
        "side": str(xt_order.get("side", "BUY")).upper(),
        "quantity": quantity,
        "order_type": str(xt_order.get("order_type", "LIMIT")).upper(),
        "limit_price": _safe_float(xt_order, "price"),
        "status": map_order_status(status_code),
        "filled_quantity": filled,
        "remaining_quantity": max(0, quantity - filled),
        "filled_price": _safe_float(xt_order, "trade_price", "traded_price", "filled_price"),
        "time_in_force": str(xt_order.get("time_in_force", "DAY")).upper(),
        "reason": str(xt_order.get("reason", xt_order.get("message", ""))),
        "submitted_at": str(xt_order.get("order_time", xt_order.get("submitted_at", "")) or _now()),
        "updated_at": str(xt_order.get("updated_at", "") or _now()),
    }


# ── Trade ──────────────────────────────────────────────────────────────


def map_trade(xt_trade: dict[str, Any]) -> dict[str, Any]:
    """Normalize an xtquant trade record to the server's /trades contract."""
    quantity = int(xt_trade.get("trade_quantity", xt_trade.get("quantity", 0)) or 0)
    price = _safe_float(xt_trade, "trade_price", "price")
    amount = _safe_float(xt_trade, "trade_amount", "amount")
    if amount == 0.0 and quantity > 0 and price > 0:
        amount = round(quantity * price, 2)
    return {
        "trade_id": str(xt_trade.get("trade_id", xt_trade.get("broker_trade_id", ""))),
        "broker_order_id": str(xt_trade.get("order_id", xt_trade.get("broker_order_id", ""))),
        "intent_id": str(xt_trade.get("intent_id", "")),
        "symbol": str(xt_trade.get("stock_code", xt_trade.get("symbol", ""))),
        "side": str(xt_trade.get("side", "BUY")).upper(),
        "quantity": quantity,
        "price": price,
        "amount": amount,
        "executed_at": str(xt_trade.get("trade_time", xt_trade.get("executed_at", "")) or _now()),
    }


# ── Internal helpers ───────────────────────────────────────────────────


def _safe_float(obj: dict[str, Any], *keys: str) -> float:
    for key in keys:
        val = obj.get(key)
        if val is not None:
            return float(val)
    return 0.0


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
