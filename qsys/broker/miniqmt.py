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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BrokerOrder":
        return cls(
            broker_order_id=str(payload.get("broker_order_id") or ""),
            intent_id=str(payload.get("intent_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or "").lower(),
            amount=int(payload.get("amount") or 0),
            price=float(payload.get("price") or 0.0),
            status=BrokerOrderStatus(str(payload.get("status") or BrokerOrderStatus.PENDING.value)),
            filled_amount=int(payload.get("filled_amount") or 0),
            filled_price=float(payload.get("filled_price") or 0.0),
            message=str(payload.get("message") or ""),
        )


@dataclass
class PositionSnapshot:
    symbol: str
    total_amount: int
    sellable_amount: int
    avg_cost: float
    market_value: float
    last_price: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PositionSnapshot":
        total_amount = int(payload.get("total_amount") or payload.get("amount") or 0)
        market_value = float(payload.get("market_value") or 0.0)
        last_price = float(payload.get("last_price") or payload.get("price") or 0.0)
        if last_price <= 0 and total_amount > 0 and market_value > 0:
            last_price = market_value / total_amount
        return cls(
            symbol=str(payload.get("symbol") or ""),
            total_amount=total_amount,
            sellable_amount=int(payload.get("sellable_amount") or total_amount),
            avg_cost=float(payload.get("avg_cost") or payload.get("cost_basis") or last_price),
            market_value=market_value,
            last_price=last_price,
        )


@dataclass
class AccountSnapshot:
    account_name: str
    cash: float
    total_assets: float
    frozen_cash: float = 0.0
    market_value: float = 0.0
    available_cash: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, account_name: str = "real") -> "AccountSnapshot":
        cash = float(payload.get("cash") or 0.0)
        frozen_cash = float(payload.get("frozen_cash") or 0.0)
        available_cash = float(payload.get("available_cash") or max(cash - frozen_cash, 0.0))
        return cls(
            account_name=str(payload.get("account_name") or account_name),
            cash=cash,
            total_assets=float(payload.get("total_assets") or 0.0),
            frozen_cash=frozen_cash,
            market_value=float(payload.get("market_value") or 0.0),
            available_cash=available_cash,
        )


@dataclass
class TradeSnapshot:
    broker_trade_id: str
    broker_order_id: str
    intent_id: str
    symbol: str
    side: str
    filled_amount: int
    filled_price: float
    fee: float = 0.0
    tax: float = 0.0
    total_cost: float = 0.0
    order_id: str = ""
    note: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TradeSnapshot":
        filled_amount = int(payload.get("filled_amount") or payload.get("amount") or 0)
        filled_price = float(payload.get("filled_price") or payload.get("price") or 0.0)
        fee = float(payload.get("fee") or 0.0)
        tax = float(payload.get("tax") or 0.0)
        total_cost = float(payload.get("total_cost") or 0.0)
        side = str(payload.get("side") or "").lower()
        if total_cost == 0.0 and filled_amount > 0 and filled_price > 0:
            gross = filled_amount * filled_price
            total_cost = gross + fee + tax if side == "buy" else gross - fee - tax
        order_id = str(payload.get("order_id") or payload.get("broker_order_id") or "")
        return cls(
            broker_trade_id=str(payload.get("broker_trade_id") or ""),
            broker_order_id=str(payload.get("broker_order_id") or order_id),
            intent_id=str(payload.get("intent_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            side=side,
            filled_amount=filled_amount,
            filled_price=filled_price,
            fee=fee,
            tax=tax,
            total_cost=total_cost,
            order_id=order_id,
            note=str(payload.get("note") or ""),
        )


@dataclass
class MiniQMTReadback:
    account_snapshot: AccountSnapshot
    positions: list[PositionSnapshot] = field(default_factory=list)
    orders: list[BrokerOrder] = field(default_factory=list)
    trades: list[TradeSnapshot] = field(default_factory=list)
    adapter_name: str = "MiniQMTAdapter"
    account_name: str = "real"
    as_of_date: str = ""
    artifact_type: str = "miniqmt_readback"
    notes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MiniQMTReadback":
        account_name = str(payload.get("account_name") or payload.get("account_snapshot", {}).get("account_name") or "real")
        return cls(
            artifact_type=str(payload.get("artifact_type") or "miniqmt_readback"),
            adapter_name=str(payload.get("adapter_name") or "MiniQMTAdapter"),
            account_name=account_name,
            as_of_date=str(payload.get("as_of_date") or payload.get("date") or ""),
            account_snapshot=AccountSnapshot.from_dict(payload.get("account_snapshot") or {}, account_name=account_name),
            positions=[PositionSnapshot.from_dict(item) for item in payload.get("positions") or []],
            orders=[BrokerOrder.from_dict(item) for item in payload.get("orders") or []],
            trades=[TradeSnapshot.from_dict(item) for item in payload.get("trades") or []],
            notes=list(payload.get("notes") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "adapter_name": self.adapter_name,
            "account_name": self.account_name,
            "as_of_date": self.as_of_date,
            "account_snapshot": asdict(self.account_snapshot),
            "positions": [asdict(item) for item in self.positions],
            "orders": [MiniQMTBridgeResult._serialize_order(item) for item in self.orders],
            "trades": [asdict(item) for item in self.trades],
            "notes": list(self.notes),
        }


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

    def load_readback(self, path: str | Path) -> MiniQMTReadback:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return MiniQMTReadback.from_dict(payload)

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

    def read_account_snapshot(self) -> AccountSnapshot:
        raise NotImplementedError("MiniQMT read bridge is not implemented yet")

    def read_positions(self) -> list[PositionSnapshot]:
        raise NotImplementedError("MiniQMT position bridge is not implemented yet")

    def read_orders(self) -> list[BrokerOrder]:
        raise NotImplementedError("MiniQMT order bridge is not implemented yet")

    def read_trades(self) -> list[TradeSnapshot]:
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
