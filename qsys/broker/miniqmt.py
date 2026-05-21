from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
import json

import requests

from qsys.execution.models import (
    BrokerOrderAck,
    BrokerOrderRequest,
    ExecutionReport,
    Fill,
    OS_CANCELLED,
    OS_FILLED,
    OS_PARTIAL,
    OS_SUBMITTED,
)


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
    """Bridge to a MiniQMT HTTP server.

    Keeps the original dry-run / validation methods for backward compatibility
    and adds HTTP client methods (``submit_broker_requests``, ``fetch_*``)
    that communicate with the ``miniqmt_server`` REST API.

    Set *base_url* to point at a running miniqmt server (e.g.
    ``http://localhost:8080``) to enable live broker interaction.
    """

    def __init__(self, *, account_name: str = "real", mode: str = "dry_run", base_url: str | None = None):
        self.account_name = account_name
        self.mode = mode
        self.base_url = base_url.rstrip("/") if base_url else None
        self._http_session = requests.Session() if base_url else None

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

    # ── HTTP client methods (Phase 1) ─────────────────────────────────────

    def _check_http_ready(self) -> None:
        if not self.base_url or not self._http_session:
            raise RuntimeError(
                "MiniQMTAdapter HTTP client is not configured. "
                "Pass base_url= to the constructor (e.g. base_url='http://localhost:8080')."
            )

    def submit_broker_requests(
        self,
        requests: list[BrokerOrderRequest],
        *,
        strategy_id: str = "",
        trade_date: str = "",
        request_id: str = "",
        dry_run: bool = False,
    ) -> list[BrokerOrderAck]:
        """Submit orders to the MiniQMT server via ``POST /orders/submit``.

        Returns one ``BrokerOrderAck`` per submitted intent.
        """
        self._check_http_ready()
        if not trade_date:
            trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not request_id:
            request_id = f"exec:{trade_date}:{strategy_id or 'unknown'}:{datetime.now(timezone.utc).strftime('%H%M%S')}"

        # Convert BrokerOrderRequest -> server-side OrderIntent format
        orders_payload = []
        for req in requests:
            orders_payload.append(
                {
                    "intent_id": req.intent_id,
                    "symbol": req.symbol,
                    "side": req.side.upper(),
                    "quantity": req.quantity,
                    "order_type": req.order_type.upper(),
                    "limit_price": req.limit_price,
                    "time_in_force": req.time_in_force.upper(),
                    "reason": req.reason or "live_execution",
                    "target_weight": req.target_weight,
                    "notes": "",
                }
            )

        body = {
            "request_id": request_id,
            "strategy_id": strategy_id,
            "trade_date": trade_date,
            "account_id": self.account_name,
            "dry_run": dry_run,
            "orders": orders_payload,
        }

        response = self._http_session.post(
            f"{self.base_url}/orders/submit",
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()

        acks: list[BrokerOrderAck] = []
        # The server returns accepted + rejected lists under "accepted" and "rejected"
        for item in payload.get("accepted") or []:
            acks.append(
                BrokerOrderAck(
                    intent_id=item.get("intent_id", ""),
                    broker_order_id=item.get("broker_order_id", ""),
                    status="accepted",
                    extra=item,
                )
            )
        for item in payload.get("rejected") or []:
            reasons = "; ".join(r.get("message", "") for r in (item.get("reasons") or []))
            acks.append(
                BrokerOrderAck(
                    intent_id=item.get("intent_id", ""),
                    broker_order_id="",
                    status="rejected",
                    message=reasons or "rejected_by_broker",
                    extra=item,
                )
            )
        return acks

    def cancel_broker_orders(
        self,
        broker_order_ids: list[str],
        *,
        request_id: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Cancel orders via ``POST /orders/cancel``."""
        self._check_http_ready()
        if not request_id:
            request_id = f"cancel:{'-'.join(broker_order_ids[:3])}"
        body = {
            "request_id": request_id,
            "account_id": self.account_name,
            "broker_order_ids": broker_order_ids,
            "reason": reason,
        }
        response = self._http_session.post(
            f"{self.base_url}/orders/cancel",
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def fetch_account_snapshot(self) -> AccountSnapshot:
        """Fetch account snapshot via ``GET /account``."""
        self._check_http_ready()
        response = self._http_session.get(f"{self.base_url}/account", timeout=15)
        response.raise_for_status()
        payload = response.json()
        return AccountSnapshot.from_dict(
            {
                "cash": payload.get("available_cash", payload.get("cash", 0.0)),
                "available_cash": payload.get("available_cash", 0.0),
                "frozen_cash": payload.get("frozen_cash", 0.0),
                "market_value": payload.get("market_value", 0.0),
                "total_assets": payload.get("total_assets", 0.0),
                "account_name": payload.get("account_name", self.account_name),
            },
            account_name=self.account_name,
        )

    def fetch_positions(self) -> list[PositionSnapshot]:
        """Fetch positions via ``GET /positions``."""
        self._check_http_ready()
        response = self._http_session.get(f"{self.base_url}/positions", timeout=15)
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("positions") or []
        return [PositionSnapshot.from_dict(item) for item in raw]

    def fetch_orders(
        self,
        filters: dict[str, str] | None = None,
    ) -> list[ExecutionReport]:
        """Fetch orders via ``GET /orders`` with optional query filters."""
        self._check_http_ready()
        response = self._http_session.get(
            f"{self.base_url}/orders",
            params=filters or {},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("orders") or []
        reports: list[ExecutionReport] = []
        for item in raw:
            status = item.get("status", "unknown")
            # Map server status to our OS_* constants
            status_map = {
                "submitted": OS_SUBMITTED,
                "pending": OS_SUBMITTED,
                "partial": OS_PARTIAL,
                "partial_fill": OS_PARTIAL,
                "filled": OS_FILLED,
                "canceled": OS_CANCELLED,
                "cancelled": OS_CANCELLED,
                "rejected": OS_REJECTED,
            }
            normalised = status_map.get(status, status)
            filled_qty = int(item.get("filled_quantity", 0))
            total_qty = int(item.get("quantity", 0))
            reports.append(
                ExecutionReport(
                    broker_order_id=str(item.get("broker_order_id", "")),
                    intent_id=str(item.get("intent_id", "")),
                    symbol=str(item.get("symbol", "")),
                    side=str(item.get("side", "")).lower(),
                    status=normalised,
                    filled_quantity=filled_qty,
                    filled_price=float(item.get("limit_price") or 0.0),
                    remaining_quantity=max(total_qty - filled_qty, 0),
                    message=str(item.get("reason", "")),
                    extra=item,
                )
            )
        return reports

    def fetch_trades(
        self,
        filters: dict[str, str] | None = None,
    ) -> list[Fill]:
        """Fetch fills via ``GET /trades`` with optional query filters."""
        self._check_http_ready()
        response = self._http_session.get(
            f"{self.base_url}/trades",
            params=filters or {},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("trades") or []
        fills: list[Fill] = []
        for item in raw:
            fills.append(
                Fill(
                    broker_trade_id=str(item.get("trade_id", "")),
                    broker_order_id=str(item.get("broker_order_id", "")),
                    intent_id=str(item.get("intent_id", "")),
                    symbol=str(item.get("symbol", "")),
                    side=str(item.get("side", "")).lower(),
                    quantity=int(item.get("quantity", 0)),
                    price=float(item.get("price", 0.0)),
                    filled_at=str(item.get("executed_at", "")),
                    extra=item,
                )
            )
        return fills
