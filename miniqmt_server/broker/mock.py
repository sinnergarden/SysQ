from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
import uuid

from miniqmt_server.broker.base import BrokerAdapter
from miniqmt_server.config import ServerConfig
from miniqmt_server.models import CancelRequest, FINAL_ORDER_STATUSES, OrderRecord, OrderRequest, TradeRecord, ValidationIssue
from miniqmt_server.storage import JsonlStorage


class MockBrokerAdapter(BrokerAdapter):
    def __init__(self, config: ServerConfig, storage: JsonlStorage) -> None:
        self.config = config
        self.storage = storage
        self.account = deepcopy(config.mock.account)
        self.account.setdefault("account_id", config.mock.account_id)
        self.positions = {item["symbol"]: deepcopy(item) for item in config.mock.positions}
        self.last_sync_time = ""
        latest_snapshot = self.storage.get_latest_snapshot()
        if latest_snapshot:
            self._restore_from_snapshot(latest_snapshot)
        else:
            self._refresh_snapshot(trigger="startup")

    def get_health(self) -> dict[str, Any]:
        trading_date = datetime.now().strftime("%Y-%m-%d")
        return {
            "status": "ok",
            "broker_mode": "mock",
            "miniqmt_connected": self.config.mock.miniqmt_connected,
            "account_query_ready": self.config.mock.query_ready,
            "submit_enabled": self.config.mock.submit_enabled and self.config.mock.allow_submit,
            "server_version": self.config.version,
            "trade_date": trading_date,
            "account_id": self.account.get("account_id") or self.config.mock.account_id,
            "last_sync_time": self.last_sync_time,
        }

    def get_account(self) -> dict[str, Any]:
        account = deepcopy(self.account)
        account.setdefault("account_id", self.config.mock.account_id)
        account.setdefault("updated_at", self.last_sync_time)
        return account

    def get_positions(self) -> list[dict[str, Any]]:
        positions = [deepcopy(item) for item in self.positions.values()]
        positions.sort(key=lambda item: item["symbol"])
        return positions

    def list_orders(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        records = [order.to_dict() for order in self.storage.list_orders()]
        return [record for record in records if self._match_order_filters(record, filters)]

    def list_trades(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        records = [trade.to_dict() for trade in self.storage.list_trades()]
        return [record for record in records if self._match_trade_filters(record, filters)]

    def validate_orders(self, request: OrderRequest) -> dict[str, Any]:
        issues = request.validate()
        normalized_orders = [self._normalize_order(request, index, order) for index, order in enumerate(request.orders)]
        issues.extend(self._validate_pre_trade_risk(normalized_orders, issues))
        request_level_errors = [item.to_dict() for item in issues if not item.field or not item.field.startswith("orders[")]
        per_order_errors = self._group_order_issues(request, issues)

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for item in normalized_orders:
            order_issues = per_order_errors.get(item["intent_id"], [])
            if order_issues:
                rejected.append(
                    {
                        "intent_id": item["intent_id"],
                        "status": "rejected",
                        "reasons": order_issues,
                    }
                )
            else:
                accepted.append(
                    {
                        "intent_id": item["intent_id"],
                        "status": "accepted",
                        "normalized_order": item,
                    }
                )

        status = self._resolve_status(len(accepted), len(rejected), bool(request_level_errors))
        return {
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "dry_run": request.dry_run,
            "status": status,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
            "normalized_orders": normalized_orders,
            "errors": request_level_errors,
            "validation_time": self._now(),
        }

    def submit_orders(self, request: OrderRequest) -> dict[str, Any]:
        validation = self.validate_orders(request)
        if self._request_exists(request.request_id):
            existing_orders = self.list_orders({"request_id": request.request_id})
            return {
                "request_id": request.request_id,
                "strategy_id": request.strategy_id,
                "trade_date": request.trade_date,
                "account_id": request.account_id,
                "dry_run": request.dry_run,
                "status": "duplicate_request",
                "accepted_count": 0,
                "rejected_count": len(existing_orders),
                "broker_order_ids": [item["broker_order_id"] for item in existing_orders],
                "accepted": [],
                "rejected": [
                    {
                        "intent_id": item["intent_id"],
                        "status": "rejected",
                        "reasons": [{"code": "duplicate_request", "message": "request_id already submitted"}],
                    }
                    for item in existing_orders
                ],
                "normalized_orders": validation["normalized_orders"],
                "errors": [{"code": "duplicate_request", "message": "request_id already submitted"}],
                "submit_time": self._now(),
            }
        if validation["accepted_count"] == 0:
            validation["broker_order_ids"] = []
            validation["submit_time"] = self._now()
            return validation
        if request.dry_run:
            validation["status"] = "dry_run"
            validation["broker_order_ids"] = []
            validation["submit_time"] = self._now()
            return validation
        if not (self.config.mock.submit_enabled and self.config.mock.allow_submit):
            validation["status"] = "rejected"
            validation["errors"] = list(validation["errors"]) + [
                {"code": "submit_disabled", "message": "mock broker submit is disabled"}
            ]
            validation["accepted"] = []
            validation["accepted_count"] = 0
            validation["rejected_count"] = len(validation["normalized_orders"])
            validation["rejected"] = [
                {
                    "intent_id": item["intent_id"],
                    "status": "rejected",
                    "reasons": [{"code": "submit_disabled", "message": "mock broker submit is disabled"}],
                }
                for item in validation["normalized_orders"]
            ]
            validation["submit_time"] = self._now()
            validation["broker_order_ids"] = []
            return validation

        accepted_payloads: list[dict[str, Any]] = []
        rejected_payloads = list(validation["rejected"])
        rejected_intent_ids = {item["intent_id"] for item in rejected_payloads}
        broker_order_ids: list[str] = []
        submit_time = self._now()

        for item in validation["normalized_orders"]:
            if item["intent_id"] in rejected_intent_ids:
                continue
            broker_order_id = self._next_broker_order_id()
            status = "filled" if self.config.mock.auto_fill else "submitted"
            record = OrderRecord(
                broker_order_id=broker_order_id,
                client_order_id=item["client_order_id"],
                request_id=request.request_id,
                strategy_id=request.strategy_id,
                trade_date=request.trade_date,
                account_id=request.account_id,
                intent_id=item["intent_id"],
                symbol=item["symbol"],
                side=item["side"],
                quantity=item["quantity"],
                order_type=item["order_type"],
                limit_price=item["limit_price"],
                time_in_force=item["time_in_force"],
                status=status,
                reason=item["reason"],
                target_weight=item["target_weight"],
                notes=item["notes"],
                dry_run=False,
                submitted_at=submit_time,
                updated_at=submit_time,
                filled_quantity=item["quantity"] if status == "filled" else 0,
            )
            self.storage.record_order(record)
            broker_order_ids.append(broker_order_id)
            accepted_payloads.append(record.to_dict())
            if status == "filled":
                trade_price = self._resolve_reference_price(item["symbol"], item.get("limit_price")) or 0.0
                self._record_trade(record, price=trade_price)

        self._refresh_snapshot(trigger="submit")
        return {
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "dry_run": request.dry_run,
            "status": self._resolve_status(len(accepted_payloads), len(rejected_payloads), False),
            "accepted_count": len(accepted_payloads),
            "rejected_count": len(rejected_payloads),
            "broker_order_ids": broker_order_ids,
            "accepted": accepted_payloads,
            "rejected": rejected_payloads,
            "normalized_orders": validation["normalized_orders"],
            "errors": list(validation["errors"]),
            "submit_time": submit_time,
        }

    def cancel_orders(self, request: CancelRequest) -> dict[str, Any]:
        issues = request.validate()
        if issues:
            return {
                "request_id": request.request_id,
                "account_id": request.account_id,
                "status": "rejected",
                "canceled_count": 0,
                "rejected_count": 0,
                "canceled": [],
                "rejected": [],
                "errors": [issue.to_dict() for issue in issues],
                "cancel_time": self._now(),
            }

        targets = set(request.broker_order_ids + request.client_order_ids)
        canceled: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        orders = self.storage.list_orders()
        by_broker_id = {item.broker_order_id: item for item in orders}
        by_client_id = {item.client_order_id: item for item in orders}
        cancel_time = self._now()

        for target in targets:
            order = by_broker_id.get(target) or by_client_id.get(target)
            if order is None:
                rejected.append(
                    {
                        "target": target,
                        "status": "rejected",
                        "reasons": [{"code": "order_not_found", "message": "order target was not found"}],
                    }
                )
                continue
            if order.status in FINAL_ORDER_STATUSES:
                rejected.append(
                    {
                        "target": target,
                        "status": "rejected",
                        "reasons": [{"code": "not_cancelable", "message": f"order status {order.status} is final"}],
                    }
                )
                continue
            order.status = "canceled"
            order.updated_at = cancel_time
            order.cancel_reason = request.reason
            self.storage.record_order(order)
            canceled.append(order.to_dict())

        if canceled:
            self._refresh_snapshot(trigger="cancel")

        return {
            "request_id": request.request_id,
            "account_id": request.account_id,
            "status": self._resolve_status(len(canceled), len(rejected), False),
            "canceled_count": len(canceled),
            "rejected_count": len(rejected),
            "canceled": canceled,
            "rejected": rejected,
            "errors": [],
            "cancel_time": cancel_time,
        }

    def get_latest_snapshot(self) -> dict[str, Any]:
        snapshot = self.storage.get_latest_snapshot()
        if snapshot is None:
            self._refresh_snapshot(trigger="read")
            snapshot = self.storage.get_latest_snapshot() or {}
        return snapshot

    def _request_exists(self, request_id: str) -> bool:
        if not request_id:
            return False
        return any(order.request_id == request_id for order in self.storage.list_orders())

    def _normalize_order(self, request: OrderRequest, index: int, order: Any) -> dict[str, Any]:
        intent_id = order.intent_id or f"order-{index:04d}"
        return {
            "client_order_id": f"{request.request_id}:{intent_id}",
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "intent_id": intent_id,
            "symbol": order.symbol,
            "side": order.side,
            "quantity": order.quantity,
            "order_type": order.order_type,
            "limit_price": order.limit_price,
            "time_in_force": order.time_in_force,
            "reason": order.reason,
            "target_weight": order.target_weight,
            "notes": order.notes,
        }

    def _group_order_issues(self, request: OrderRequest, issues: list[ValidationIssue]) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for issue in issues:
            if not issue.field or not issue.field.startswith("orders["):
                continue
            index = self._index_from_issue_field(issue.field)
            if index is None or index >= len(request.orders):
                continue
            intent_id = request.orders[index].intent_id or f"order-{index:04d}"
            grouped.setdefault(intent_id, []).append(issue.to_dict())
        return grouped

    def _validate_pre_trade_risk(
        self,
        normalized_orders: list[dict[str, Any]],
        existing_issues: list[ValidationIssue],
    ) -> list[ValidationIssue]:
        invalid_indexes = {
            index
            for issue in existing_issues
            if issue.field and issue.field.startswith("orders[")
            for index in [self._index_from_issue_field(issue.field)]
            if index is not None
        }
        projected_cash = round(float(self.account.get("available_cash", 0.0) or 0.0), 2)
        projected_available_volume = {
            symbol: int(position.get("available_volume", position.get("volume", 0)) or 0)
            for symbol, position in self.positions.items()
        }
        risk_issues: list[ValidationIssue] = []

        for index, order in enumerate(normalized_orders):
            if index in invalid_indexes:
                continue
            symbol = str(order["symbol"])
            side = str(order["side"])
            quantity = int(order["quantity"])
            if side == "SELL":
                available_volume = projected_available_volume.get(symbol, 0)
                if quantity > available_volume:
                    risk_issues.append(
                        ValidationIssue(
                            "insufficient_available_volume",
                            f"SELL quantity {quantity} exceeds available_volume {available_volume} for {symbol}",
                            f"orders[{index}].quantity",
                        )
                    )
                    continue
                projected_available_volume[symbol] = available_volume - quantity
                continue

            reference_price = self._resolve_reference_price(symbol, order.get("limit_price"))
            if reference_price is None:
                risk_issues.append(
                    ValidationIssue(
                        "missing_reference_price",
                        f"cannot estimate cash usage for {symbol}; provide limit_price or preload market_price",
                        f"orders[{index}].limit_price",
                    )
                )
                continue

            required_cash = round(quantity * reference_price, 2)
            if required_cash > projected_cash:
                risk_issues.append(
                    ValidationIssue(
                        "insufficient_available_cash",
                        f"BUY order needs {required_cash:.2f} cash but only {projected_cash:.2f} is available",
                        f"orders[{index}].quantity",
                    )
                )
                continue
            projected_cash = round(projected_cash - required_cash, 2)

        return risk_issues

    def _index_from_issue_field(self, field_name: str) -> int | None:
        try:
            index_text = field_name.split("orders[", 1)[1].split("]", 1)[0]
            return int(index_text)
        except (IndexError, ValueError):
            return None

    def _resolve_reference_price(self, symbol: str, limit_price: Any) -> float | None:
        if limit_price is not None:
            price = float(limit_price)
            if price > 0:
                return price

        position = self.positions.get(symbol) or {}
        for field_name in ("market_price", "cost_price"):
            raw_value = position.get(field_name)
            if raw_value is None:
                continue
            price = float(raw_value)
            if price > 0:
                return price
        return None

    def _record_trade(self, order: OrderRecord, price: float) -> None:
        trade_id = f"mock-trade-{uuid.uuid4().hex[:10]}"
        executed_at = self._now()
        amount = round(order.quantity * price, 2)
        trade = TradeRecord(
            trade_id=trade_id,
            broker_order_id=order.broker_order_id,
            request_id=order.request_id,
            strategy_id=order.strategy_id,
            trade_date=order.trade_date,
            account_id=order.account_id,
            intent_id=order.intent_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            amount=amount,
            executed_at=executed_at,
        )
        self.storage.record_trade(trade)
        self._apply_trade_to_state(order.symbol, order.side, order.quantity, price)

    def _apply_trade_to_state(self, symbol: str, side: str, quantity: int, price: float) -> None:
        position = self.positions.setdefault(
            symbol,
            {
                "symbol": symbol,
                "volume": 0,
                "available_volume": 0,
                "cost_price": price,
                "market_price": price,
                "market_value": 0.0,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "update_time": self._now(),
            },
        )
        if side == "BUY":
            total_cost = position["cost_price"] * position["volume"] + quantity * price
            position["volume"] += quantity
            position["available_volume"] += quantity
            if position["volume"] > 0:
                position["cost_price"] = round(total_cost / position["volume"], 4)
            self.account["available_cash"] = round(self.account.get("available_cash", 0.0) - quantity * price, 2)
        else:
            position["volume"] = max(0, position["volume"] - quantity)
            position["available_volume"] = max(0, position["available_volume"] - quantity)
            self.account["available_cash"] = round(self.account.get("available_cash", 0.0) + quantity * price, 2)
        position["market_price"] = price
        position["market_value"] = round(position["volume"] * price, 2)
        cost_basis = position["cost_price"] * position["volume"]
        position["pnl"] = round(position["market_value"] - cost_basis, 2)
        position["pnl_pct"] = round(position["pnl"] / cost_basis, 6) if cost_basis else 0.0
        position["update_time"] = self._now()
        self._recompute_account_totals()

    def _recompute_account_totals(self) -> None:
        market_value = round(sum(item.get("market_value", 0.0) for item in self.positions.values()), 2)
        self.account["market_value"] = market_value
        self.account["total_assets"] = round(
            self.account.get("available_cash", 0.0) + market_value + self.account.get("frozen_cash", 0.0),
            2,
        )
        self.account.setdefault("daily_pnl", 0.0)
        self.account["updated_at"] = self._now()

    def _restore_from_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.account = deepcopy(snapshot.get("account") or self.account)
        positions = snapshot.get("positions") or []
        self.positions = {item["symbol"]: deepcopy(item) for item in positions}
        self.last_sync_time = str(snapshot.get("captured_at") or "")

    def _refresh_snapshot(self, trigger: str) -> None:
        self._recompute_account_totals()
        captured_at = self._now()
        snapshot = {
            "snapshot_id": f"snapshot-{uuid.uuid4().hex[:10]}",
            "captured_at": captured_at,
            "trigger": trigger,
            "account": self.get_account(),
            "positions": self.get_positions(),
            "orders": self.list_orders({}),
            "trades": self.list_trades({}),
        }
        self.storage.record_snapshot(snapshot)
        self.last_sync_time = captured_at

    def _match_order_filters(self, record: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, value in filters.items():
            if not value:
                continue
            if key == "client_order_id" and record.get("client_order_id") != value:
                return False
            if key == "request_id" and record.get("request_id") != value:
                return False
            if key in {"trade_date", "symbol", "status", "strategy_id"} and str(record.get(key) or "") != value:
                return False
        return True

    def _match_trade_filters(self, record: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, value in filters.items():
            if not value:
                continue
            if key == "order_id" and record.get("broker_order_id") != value:
                return False
            if key in {"trade_date", "symbol", "strategy_id"} and str(record.get(key) or "") != value:
                return False
        return True

    def _resolve_status(self, accepted_count: int, rejected_count: int, request_has_errors: bool) -> str:
        if request_has_errors and accepted_count == 0:
            return "rejected"
        if accepted_count > 0 and rejected_count > 0:
            return "partial"
        if accepted_count > 0:
            return "accepted"
        return "rejected"

    def _next_broker_order_id(self) -> str:
        return f"mock-order-{uuid.uuid4().hex[:10]}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
