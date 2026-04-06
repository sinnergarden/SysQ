from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


VALID_SIDES = {"BUY", "SELL"}
VALID_ORDER_TYPES = {"LIMIT", "MARKET"}
VALID_TIME_IN_FORCE = {"DAY", "IOC", "FOK"}
FINAL_ORDER_STATUSES = {"canceled", "filled", "rejected"}


@dataclass
class ValidationIssue:
    code: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.field:
            payload["field"] = self.field
        return payload


@dataclass
class OrderIntent:
    intent_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: float | None = None
    time_in_force: str = "DAY"
    reason: str = ""
    target_weight: float | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OrderIntent":
        limit_price = payload.get("limit_price")
        target_weight = payload.get("target_weight")
        return cls(
            intent_id=str(payload.get("intent_id") or ""),
            symbol=str(payload.get("symbol") or "").upper(),
            side=str(payload.get("side") or "").upper(),
            quantity=int(payload.get("quantity") or 0),
            order_type=str(payload.get("order_type") or "LIMIT").upper(),
            limit_price=float(limit_price) if limit_price is not None else None,
            time_in_force=str(payload.get("time_in_force") or "DAY").upper(),
            reason=str(payload.get("reason") or ""),
            target_weight=float(target_weight) if target_weight is not None else None,
            notes=str(payload.get("notes") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self, index: int) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        prefix = f"orders[{index}]"
        if not self.intent_id:
            issues.append(ValidationIssue("missing_intent_id", "intent_id is required", f"{prefix}.intent_id"))
        if not self.symbol:
            issues.append(ValidationIssue("missing_symbol", "symbol is required", f"{prefix}.symbol"))
        elif "." not in self.symbol:
            issues.append(ValidationIssue("invalid_symbol", "symbol should look like 600000.SH", f"{prefix}.symbol"))
        if self.side not in VALID_SIDES:
            issues.append(ValidationIssue("invalid_side", "side must be BUY or SELL", f"{prefix}.side"))
        if self.quantity <= 0:
            issues.append(ValidationIssue("invalid_quantity", "quantity must be positive", f"{prefix}.quantity"))
        elif self.quantity % 100 != 0:
            issues.append(ValidationIssue("invalid_lot_size", "quantity must be a multiple of 100", f"{prefix}.quantity"))
        if self.order_type not in VALID_ORDER_TYPES:
            issues.append(ValidationIssue("invalid_order_type", "order_type must be LIMIT or MARKET", f"{prefix}.order_type"))
        if self.order_type == "LIMIT" and (self.limit_price is None or self.limit_price <= 0):
            issues.append(ValidationIssue("invalid_limit_price", "LIMIT order requires positive limit_price", f"{prefix}.limit_price"))
        if self.order_type == "MARKET" and self.limit_price is not None:
            issues.append(ValidationIssue("unexpected_limit_price", "MARKET order should not send limit_price", f"{prefix}.limit_price"))
        if self.time_in_force not in VALID_TIME_IN_FORCE:
            issues.append(ValidationIssue("invalid_time_in_force", "time_in_force must be DAY, IOC or FOK", f"{prefix}.time_in_force"))
        return issues


@dataclass
class OrderRequest:
    request_id: str
    strategy_id: str
    trade_date: str
    account_id: str
    dry_run: bool
    orders: list[OrderIntent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OrderRequest":
        return cls(
            request_id=str(payload.get("request_id") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            account_id=str(payload.get("account_id") or ""),
            dry_run=bool(payload.get("dry_run", False)),
            orders=[OrderIntent.from_dict(item) for item in payload.get("orders") or []],
        )

    def validate(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not self.request_id:
            issues.append(ValidationIssue("missing_request_id", "request_id is required", "request_id"))
        if not self.strategy_id:
            issues.append(ValidationIssue("missing_strategy_id", "strategy_id is required", "strategy_id"))
        if not self.trade_date:
            issues.append(ValidationIssue("missing_trade_date", "trade_date is required", "trade_date"))
        else:
            try:
                datetime.strptime(self.trade_date, "%Y-%m-%d")
            except ValueError:
                issues.append(ValidationIssue("invalid_trade_date", "trade_date must be YYYY-MM-DD", "trade_date"))
        if not self.account_id:
            issues.append(ValidationIssue("missing_account_id", "account_id is required", "account_id"))
        if not self.orders:
            issues.append(ValidationIssue("missing_orders", "orders must not be empty", "orders"))
        seen_intent_ids: set[str] = set()
        for index, order in enumerate(self.orders):
            issues.extend(order.validate(index))
            if order.intent_id:
                if order.intent_id in seen_intent_ids:
                    issues.append(
                        ValidationIssue(
                            "duplicate_intent_id",
                            f"duplicate intent_id: {order.intent_id}",
                            f"orders[{index}].intent_id",
                        )
                    )
                seen_intent_ids.add(order.intent_id)
        return issues


@dataclass
class CancelRequest:
    request_id: str
    account_id: str
    broker_order_ids: list[str] = field(default_factory=list)
    client_order_ids: list[str] = field(default_factory=list)
    reason: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CancelRequest":
        return cls(
            request_id=str(payload.get("request_id") or ""),
            account_id=str(payload.get("account_id") or ""),
            broker_order_ids=[str(item) for item in payload.get("broker_order_ids") or []],
            client_order_ids=[str(item) for item in payload.get("client_order_ids") or []],
            reason=str(payload.get("reason") or ""),
        )

    def validate(self) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if not self.request_id:
            issues.append(ValidationIssue("missing_request_id", "request_id is required", "request_id"))
        if not self.account_id:
            issues.append(ValidationIssue("missing_account_id", "account_id is required", "account_id"))
        if not self.broker_order_ids and not self.client_order_ids:
            issues.append(
                ValidationIssue(
                    "missing_cancel_targets",
                    "broker_order_ids or client_order_ids is required",
                    "broker_order_ids",
                )
            )
        return issues


@dataclass
class OrderRecord:
    broker_order_id: str
    client_order_id: str
    request_id: str
    strategy_id: str
    trade_date: str
    account_id: str
    intent_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: float | None
    time_in_force: str
    status: str
    reason: str = ""
    target_weight: float | None = None
    notes: str = ""
    dry_run: bool = False
    submitted_at: str = ""
    updated_at: str = ""
    filled_quantity: int = 0
    cancel_reason: str = ""
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "OrderRecord":
        return cls(
            broker_order_id=str(payload.get("broker_order_id") or ""),
            client_order_id=str(payload.get("client_order_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            account_id=str(payload.get("account_id") or ""),
            intent_id=str(payload.get("intent_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or ""),
            quantity=int(payload.get("quantity") or 0),
            order_type=str(payload.get("order_type") or "LIMIT"),
            limit_price=float(payload["limit_price"]) if payload.get("limit_price") is not None else None,
            time_in_force=str(payload.get("time_in_force") or "DAY"),
            status=str(payload.get("status") or "unknown"),
            reason=str(payload.get("reason") or ""),
            target_weight=float(payload["target_weight"]) if payload.get("target_weight") is not None else None,
            notes=str(payload.get("notes") or ""),
            dry_run=bool(payload.get("dry_run", False)),
            submitted_at=str(payload.get("submitted_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            filled_quantity=int(payload.get("filled_quantity") or 0),
            cancel_reason=str(payload.get("cancel_reason") or ""),
            error=payload.get("error"),
        )


@dataclass
class TradeRecord:
    trade_id: str
    broker_order_id: str
    request_id: str
    strategy_id: str
    trade_date: str
    account_id: str
    intent_id: str
    symbol: str
    side: str
    quantity: int
    price: float
    amount: float
    executed_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "TradeRecord":
        return cls(
            trade_id=str(payload.get("trade_id") or ""),
            broker_order_id=str(payload.get("broker_order_id") or ""),
            request_id=str(payload.get("request_id") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            account_id=str(payload.get("account_id") or ""),
            intent_id=str(payload.get("intent_id") or ""),
            symbol=str(payload.get("symbol") or ""),
            side=str(payload.get("side") or ""),
            quantity=int(payload.get("quantity") or 0),
            price=float(payload.get("price") or 0.0),
            amount=float(payload.get("amount") or 0.0),
            executed_at=str(payload.get("executed_at") or ""),
        )


@dataclass
class SubmitReceipt:
    request_id: str
    request_fingerprint: str
    strategy_id: str
    trade_date: str
    account_id: str
    dry_run: bool
    normalized_orders: list[dict[str, Any]] = field(default_factory=list)
    response: dict[str, Any] = field(default_factory=dict)
    recorded_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SubmitReceipt":
        return cls(
            request_id=str(payload.get("request_id") or ""),
            request_fingerprint=str(payload.get("request_fingerprint") or ""),
            strategy_id=str(payload.get("strategy_id") or ""),
            trade_date=str(payload.get("trade_date") or ""),
            account_id=str(payload.get("account_id") or ""),
            dry_run=bool(payload.get("dry_run", False)),
            normalized_orders=list(payload.get("normalized_orders") or []),
            response=dict(payload.get("response") or {}),
            recorded_at=str(payload.get("recorded_at") or ""),
        )
