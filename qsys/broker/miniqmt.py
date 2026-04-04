from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
import json


class BrokerOrderStatus(str, Enum):
    PENDING = "pending"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass
class MiniQMTOrderIntent:
    intent_id: str
    account_name: str
    symbol: str
    side: str
    amount: int
    price: float
    execution_bucket: str = "review"
    cash_dependency: str = "review"
    t1_rule: str = "review"
    price_policy: str = "reference"
    signal_date: str = ""
    execution_date: str = ""
    model_version: str = ""
    risk_tags: list[str] = field(default_factory=list)
    note: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MiniQMTOrderIntent":
        return cls(
            intent_id=str(payload.get("intent_id") or ""),
            account_name=str(payload.get("account_name") or "real"),
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or "review").lower(),
            amount=int(payload.get("amount") or 0),
            price=float(payload.get("price") or 0.0),
            execution_bucket=str(payload.get("execution_bucket") or "review"),
            cash_dependency=str(payload.get("cash_dependency") or "review"),
            t1_rule=str(payload.get("t1_rule") or "review"),
            price_policy=str(payload.get("price_policy") or payload.get("price_basis", {}).get("field") or "reference"),
            signal_date=str(payload.get("signal_date") or ""),
            execution_date=str(payload.get("execution_date") or ""),
            model_version=str(payload.get("model_version") or payload.get("model_name") or ""),
            risk_tags=list(payload.get("risk_tags") or []),
            note=str(payload.get("note") or ""),
        )


@dataclass
class BrokerOrder:
    broker_order_id: str
    intent_id: str
    symbol: str
    side: str
    amount: int
    price: float
    status: BrokerOrderStatus
    filled_amount: int = 0
    filled_price: float = 0.0
    message: str = ""


@dataclass
class PositionSnapshot:
    symbol: str
    total_amount: int
    sellable_amount: int
    avg_cost: float
    market_value: float


@dataclass
class MiniQMTBridgeResult:
    adapter_name: str
    mode: str
    intent_count: int
    accepted_orders: list[BrokerOrder] = field(default_factory=list)
    rejected_orders: list[BrokerOrder] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "mode": self.mode,
            "intent_count": self.intent_count,
            "accepted_orders": [self._serialize_order(order) for order in self.accepted_orders],
            "rejected_orders": [self._serialize_order(order) for order in self.rejected_orders],
            "notes": list(self.notes),
        }

    @staticmethod
    def _serialize_order(order: BrokerOrder) -> dict[str, Any]:
        payload = asdict(order)
        payload["status"] = order.status.value
        return payload


class MiniQMTAdapter:
    """Thin bridge contract for a future Windows-side MiniQMT implementation.

    The current class is intentionally safe by default: it validates payloads,
    supports dry-run conversion, and exposes read-side method names, but it does
    not submit live orders unless a concrete subclass overrides the methods.
    """

    def __init__(self, *, account_name: str = "real", mode: str = "dry_run"):
        self.account_name = account_name
        self.mode = mode

    def load_order_intents(self, path: str | Path) -> list[MiniQMTOrderIntent]:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        intents = payload.get("intents") or []
        return [MiniQMTOrderIntent.from_dict(item) for item in intents]

    def validate_intent(self, intent: MiniQMTOrderIntent) -> list[str]:
        issues: list[str] = []
        if not intent.intent_id:
            issues.append("missing_intent_id")
        if not intent.symbol:
            issues.append("missing_symbol")
        if intent.side not in {"buy", "sell"}:
            issues.append("invalid_side")
        if intent.amount <= 0:
            issues.append("invalid_amount")
        if intent.price <= 0:
            issues.append("invalid_price")
        if intent.amount % 100 != 0:
            issues.append("amount_not_lot_size")
        return issues

    def convert_intent_to_order(self, intent: MiniQMTOrderIntent) -> BrokerOrder:
        issues = self.validate_intent(intent)
        if issues:
            return BrokerOrder(
                broker_order_id="",
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                amount=intent.amount,
                price=intent.price,
                status=BrokerOrderStatus.REJECTED,
                message=",".join(issues),
            )

        return BrokerOrder(
            broker_order_id=f"dryrun:{intent.intent_id}",
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            amount=intent.amount,
            price=intent.price,
            status=BrokerOrderStatus.PENDING,
            message="converted_for_bridge",
        )

    def read_account_snapshot(self) -> dict[str, Any]:
        raise NotImplementedError("MiniQMT read bridge is not implemented yet")

    def read_positions(self) -> list[PositionSnapshot]:
        raise NotImplementedError("MiniQMT position bridge is not implemented yet")

    def read_orders(self) -> list[BrokerOrder]:
        raise NotImplementedError("MiniQMT order bridge is not implemented yet")

    def read_trades(self) -> list[dict[str, Any]]:
        raise NotImplementedError("MiniQMT trade bridge is not implemented yet")

    def submit_orders(self, intents: list[MiniQMTOrderIntent]) -> MiniQMTBridgeResult:
        accepted: list[BrokerOrder] = []
        rejected: list[BrokerOrder] = []

        for intent in intents:
            order = self.convert_intent_to_order(intent)
            if order.status == BrokerOrderStatus.REJECTED:
                rejected.append(order)
            else:
                accepted.append(order)

        notes = [
            "dry_run_only",
            "windows_native_miniqmt_bridge_not_implemented",
        ]
        return MiniQMTBridgeResult(
            adapter_name="MiniQMTAdapter",
            mode=self.mode,
            intent_count=len(intents),
            accepted_orders=accepted,
            rejected_orders=rejected,
            notes=notes,
        )
