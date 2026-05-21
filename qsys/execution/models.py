from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Order status constants ──────────────────────────────────────────────

OS_PENDING = "pending"
OS_SUBMITTED = "submitted"
OS_SUBMIT_UNKNOWN = "submit_unknown"  # HTTP error after submit call — broker may or may not have received it
OS_PARTIAL = "partial"
OS_FILLED = "filled"
OS_CANCELLED = "cancelled"
OS_REJECTED = "rejected"

FINAL_STATUSES = {OS_FILLED, OS_CANCELLED, OS_REJECTED}

# Valid state transitions: current -> [next]
VALID_TRANSITIONS: dict[str, list[str]] = {
    OS_PENDING: [OS_SUBMITTED, OS_SUBMIT_UNKNOWN, OS_REJECTED],
    OS_SUBMIT_UNKNOWN: [OS_SUBMITTED, OS_PARTIAL, OS_FILLED, OS_CANCELLED, OS_REJECTED],
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

    Phase 1 enforces strict validation:
    - ``order_type`` must be ``"limit"`` (market orders not supported)
    - ``limit_price``, ``price_source``, and ``price_snapshot_time`` are all required
    - ``quantity`` must be a multiple of 100 (lot-size aligned)

    *intent_id* links back to the strategy's original order intent for traceability.
    """

    intent_id: str
    symbol: str
    side: str  # "buy" / "sell"
    order_type: str  # "limit" (Phase 1 only — market orders are not supported)
    quantity: int  # must be lot-size aligned (multiple of 100)
    limit_price: float | None = None  # required for limit orders
    price_source: str = ""  # required (e.g. "close@2026-04-25", "quote_last")
    price_snapshot_time: str = ""  # required (ISO time when the price was captured)
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
