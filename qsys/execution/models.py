from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Order status constants ──────────────────────────────────────────────

OS_PENDING = "pending"
OS_SUBMITTED = "submitted"
OS_PARTIAL = "partial"
OS_FILLED = "filled"
OS_CANCELLED = "cancelled"
OS_REJECTED = "rejected"

FINAL_STATUSES = {OS_FILLED, OS_CANCELLED, OS_REJECTED}

# Valid state transitions: current -> [next]
VALID_TRANSITIONS: dict[str, list[str]] = {
    OS_PENDING: [OS_SUBMITTED, OS_REJECTED],
    OS_SUBMITTED: [OS_PARTIAL, OS_FILLED, OS_CANCELLED, OS_REJECTED],
    OS_PARTIAL: [OS_PARTIAL, OS_FILLED, OS_CANCELLED],
    OS_FILLED: [],
    OS_CANCELLED: [],
    OS_REJECTED: [],
}


def validate_transition(current: str, next_status: str) -> None:
    """Raise ValueError if *current -> next_status* is not a valid transition."""
    allowed = VALID_TRANSITIONS.get(current, [])
    if next_status not in allowed:
        raise ValueError(
            f"Invalid order status transition: {current!r} -> {next_status!r}. "
            f"Allowed transitions from {current!r}: {allowed}"
        )


# ── Broker-agnostic data models ─────────────────────────────────────────


@dataclass
class BrokerOrderRequest:
    """Standardized broker-agnostic order request.

    Input to any broker adapter. The *intent_id* links back to the strategy's
    original order intent (e.g. the row from shadow rebalance output) so the
    full lifecycle is traceable.
    """

    intent_id: str
    symbol: str
    side: str  # "buy" / "sell"
    order_type: str  # "market" / "limit"
    quantity: int  # must be lot-size aligned (multiple of 100)
    price: float | None = None  # required for limit; None for market
    time_in_force: str = "DAY"
    target_weight: float | None = None
    reason: str = ""


@dataclass
class BrokerOrderAck:
    """Acknowledgement from broker after submitting an order."""

    intent_id: str
    broker_order_id: str
    status: str  # "pending" / "accepted" / "rejected"
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionReport:
    """Current state of an order at the broker (polling response)."""

    broker_order_id: str
    intent_id: str
    symbol: str
    side: str
    status: str  # matches OS_* constants
    filled_quantity: int = 0
    filled_price: float = 0.0
    remaining_quantity: int = 0
    message: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Fill:
    """A single fill / trade from the broker."""

    broker_trade_id: str
    broker_order_id: str
    intent_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    fee: float = 0.0
    filled_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
