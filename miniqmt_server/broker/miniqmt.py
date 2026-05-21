from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any
import uuid

from miniqmt_server.broker.base import BrokerAdapter
from miniqmt_server.broker.mapper import (
    _fallback_account,
    map_account,
    map_order,
    map_position,
    map_trade,
)
from miniqmt_server.config import ServerConfig
from miniqmt_server.models import (
    CancelRequest,
    FINAL_ORDER_STATUSES,
    OrderIntent,
    OrderRecord,
    OrderRequest,
    SubmitReceipt,
    TradeRecord,
    ValidationIssue,
)
from miniqmt_server.storage import JsonlStorage


LOGGER = logging.getLogger("miniqmt_server.broker.miniqmt")


class MiniQMTBrokerAdapter(BrokerAdapter):
    """Bridge to a real MiniQMT (xtquant) installation on Windows.

    Guards:
    - xtquant import guard: if xtquant is not installed (e.g. on Linux/dev),
      all operations fail closed with a clear error.
    - ``submit_enabled`` guard: when ``config.miniqmt.submit_enabled`` is
      ``False``, :meth:`submit_orders` rejects all orders without calling the
      broker.
    - ``dry_run`` guard: when the incoming ``OrderRequest.dry_run`` is
      ``True``, orders are validated but **not** submitted to xtquant.

    No order, position, or account data is fabricated when xtquant is
    unavailable — the adapter degrades honestly.
    """

    def __init__(self, config: ServerConfig, storage: JsonlStorage) -> None:
        self.config = config
        self.storage = storage
        self.cfg = config.miniqmt

        self._xtquant_available = False
        self._xttrader = None
        self._acc = None
        self._last_sync_time = ""

        self._try_connect_xtquant()

    # ── Connection management ──────────────────────────────────────────

    def _try_connect_xtquant(self) -> None:
        """Attempt to import xtquant and log in."""
        try:
            from xtquant import xttrader  # type: ignore[import-untyped]  # noqa: F811
            from xtquant.xttype import StockAccount  # type: ignore[import-untyped]
        except ImportError:
            LOGGER.info("xtquant not available — MiniQMT adapter will operate in degraded mode")
            self._xtquant_available = False
            return
        except Exception as exc:
            LOGGER.warning("xtquant import error: %s", exc)
            self._xtquant_available = False
            return

        try:
            self._acc = StockAccount(self.cfg.account_id)
            session_id = self.cfg.session_id or 0
            self._xttrader = xttrader.XtTrader(session_id=session_id)
            self._xttrader.connect()
            self._xtquant_available = True
            LOGGER.info("MiniQMT connected: account=%s session_id=%s", self.cfg.account_id, session_id)
        except Exception as exc:
            LOGGER.warning("MiniQMT connection failed: %s", exc)
            self._xtquant_available = False

    # ── Health ─────────────────────────────────────────────────────────

    def get_health(self) -> dict[str, Any]:
        trading_date = datetime.now().strftime("%Y-%m-%d")
        if not self._xtquant_available:
            return {
                "status": "degraded",
                "broker_mode": "miniqmt",
                "miniqmt_connected": False,
                "account_query_ready": False,
                "submit_enabled": self.cfg.submit_enabled,
                "server_version": self.config.version,
                "trade_date": trading_date,
                "account_id": self.cfg.account_id,
                "last_sync_time": self._last_sync_time,
                "error": {
                    "code": "xtquant_unavailable",
                    "message": "xtquant is not available on this system",
                },
            }
        return {
            "status": "ok",
            "broker_mode": "miniqmt",
            "miniqmt_connected": True,
            "account_query_ready": True,
            "submit_enabled": self.cfg.submit_enabled,
            "server_version": self.config.version,
            "trade_date": trading_date,
            "account_id": self.cfg.account_id,
            "last_sync_time": self._last_sync_time,
        }

    # ── Account ────────────────────────────────────────────────────────

    def get_account(self) -> dict[str, Any]:
        if not self._xtquant_available:
            return _fallback_account(self.cfg.account_id)
        try:
            raw = self._xttrader.query_account(self._acc)
            return map_account(raw)
        except Exception as exc:
            LOGGER.error("MiniQMT account query failed: %s", exc)
            return _fallback_account(self.cfg.account_id)

    # ── Positions ──────────────────────────────────────────────────────

    def get_positions(self) -> list[dict[str, Any]]:
        if not self._xtquant_available:
            return []
        try:
            raw_list = self._xttrader.query_stock_positions(self._acc)
            return [map_position(pos) for pos in raw_list]
        except Exception as exc:
            LOGGER.error("MiniQMT position query failed: %s", exc)
            return []

    # ── Orders ─────────────────────────────────────────────────────────

    def _query_xt_orders(self) -> list[dict[str, Any]]:
        if not self._xtquant_available:
            return []
        try:
            raw_list = self._xttrader.query_orders(self._acc)
            return [map_order(o) for o in raw_list]
        except Exception as exc:
            LOGGER.error("MiniQMT order query failed: %s", exc)
            return []

    def list_orders(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        # Local records from storage
        stored = [record.to_dict() for record in self.storage.list_orders()]
        # Latest status from xtquant if available
        xt_orders = self._query_xt_orders()
        # Merge: xtquant results override stored records for matching broker_order_ids
        by_id: dict[str, dict[str, Any]] = {}
        for o in stored:
            by_id[o.get("broker_order_id", o.get("intent_id", ""))] = o
        for o in xt_orders:
            key = o.get("broker_order_id", o.get("intent_id", ""))
            if key:
                by_id[key] = o
        merged = list(by_id.values())
        return [record for record in merged if self._match_filters(record, filters)]

    # ── Trades ─────────────────────────────────────────────────────────

    def _query_xt_trades(self) -> list[dict[str, Any]]:
        if not self._xtquant_available:
            return []
        try:
            raw_list = self._xttrader.query_trades(self._acc)
            return [map_trade(t) for t in raw_list]
        except Exception as exc:
            LOGGER.error("MiniQMT trade query failed: %s", exc)
            return []

    def list_trades(self, filters: dict[str, str]) -> list[dict[str, Any]]:
        stored = [trade.to_dict() for trade in self.storage.list_trades()]
        xt_trades = self._query_xt_trades()
        merged = stored + xt_trades
        return [record for record in merged if self._match_trade_filters(record, filters)]

    # ── Validate ───────────────────────────────────────────────────────

    def validate_orders(self, request: OrderRequest) -> dict[str, Any]:
        issues = request.validate()
        if self._xtquant_available:
            try:
                risk_issues = self._validate_pre_trade_risk(request)
                issues.extend(risk_issues)
            except Exception as exc:
                LOGGER.error("MiniQMT pre-trade risk check failed: %s", exc)
                issues.append(
                    ValidationIssue(
                        "risk_check_error",
                        f"broker risk check failed: {exc}",
                    )
                )

        normalized_orders = [self._normalize_order(request, index, order) for index, order in enumerate(request.orders)]
        request_level_errors = [item.to_dict() for item in issues if not item.field or not item.field.startswith("orders[")]
        per_order_errors = self._group_order_issues(request, issues)

        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for item in normalized_orders:
            order_issues = per_order_errors.get(item["intent_id"], [])
            if order_issues:
                rejected.append({"intent_id": item["intent_id"], "status": "rejected", "reasons": order_issues})
            else:
                accepted.append({"intent_id": item["intent_id"], "status": "accepted", "normalized_order": item})

        return {
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "dry_run": request.dry_run,
            "status": self._resolve_status(len(accepted), len(rejected), bool(request_level_errors)),
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "accepted": accepted,
            "rejected": rejected,
            "normalized_orders": normalized_orders,
            "errors": request_level_errors,
            "validation_time": self._now(),
        }

    # ── Submit ─────────────────────────────────────────────────────────

    def submit_orders(self, request: OrderRequest) -> dict[str, Any]:
        validation = self.validate_orders(request)
        request_fingerprint = self._fingerprint_submit_request(request, validation["normalized_orders"])

        # Idempotency check
        existing_receipt = self.storage.get_submission(request.request_id)
        if existing_receipt is not None:
            if existing_receipt.request_fingerprint == request_fingerprint:
                return self._build_replayed_submit_response(existing_receipt)
            return self._build_request_conflict_response(request, validation, existing_receipt)

        # No accepted orders
        if validation["accepted_count"] == 0:
            return {**validation, "broker_order_ids": [], "submit_time": self._now()}

        # Dry-run guard
        if request.dry_run:
            return {
                **validation,
                "status": "dry_run",
                "broker_order_ids": [],
                "submit_time": self._now(),
            }

        # submit_enabled guard
        if not self.cfg.submit_enabled:
            return self._build_submit_disabled_response(validation)

        # xtquant availability guard
        if not self._xtquant_available:
            return self._build_xtquant_unavailable_response(validation)

        # Real submit via xtquant
        accepted_intent_ids = {item["intent_id"] for item in validation["accepted"]}
        accepted_payloads: list[dict[str, Any]] = []
        rejected_payloads = list(validation["rejected"])
        broker_order_ids: list[str] = []
        submit_time = self._now()
        errors: list[dict[str, Any]] = list(validation["errors"])

        for item in validation["normalized_orders"]:
            intent_id = item["intent_id"]
            if intent_id not in accepted_intent_ids:
                continue
            try:
                xt_order_id = self._xttrader.order_stock(
                    self._acc,
                    stock_code=item["symbol"],
                    order_type=int(item.get("order_type") == "BUY"),  # xtquant: 0=SELL, 1=BUY
                    order_type_ext=0,  # LIMIT
                    price=item.get("limit_price") or 0.0,
                    amount=item["quantity"],
                    strategy_name=item.get("strategy_id", ""),
                )
                broker_order_id = str(xt_order_id)
            except Exception as exc:
                LOGGER.error("MiniQMT submit failed for intent %s: %s", intent_id, exc)
                rejected_payloads.append({
                    "intent_id": intent_id,
                    "status": "rejected",
                    "reasons": [{"code": "xtquant_submit_error", "message": str(exc)}],
                })
                continue

            record = OrderRecord(
                broker_order_id=broker_order_id,
                client_order_id=item.get("client_order_id", ""),
                request_id=request.request_id,
                strategy_id=request.strategy_id,
                trade_date=request.trade_date,
                account_id=request.account_id,
                intent_id=intent_id,
                symbol=item["symbol"],
                side=item["side"],
                quantity=item["quantity"],
                order_type=item["order_type"],
                limit_price=item.get("limit_price"),
                time_in_force=item.get("time_in_force", "DAY"),
                status="submitted",
                reason=item.get("reason", ""),
                target_weight=item.get("target_weight"),
                notes=item.get("notes", ""),
                dry_run=False,
                submitted_at=submit_time,
                updated_at=submit_time,
                filled_quantity=0,
            )
            self.storage.record_order(record)
            broker_order_ids.append(broker_order_id)
            accepted_payloads.append(record.to_dict())

        response: dict[str, Any] = {
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "dry_run": request.dry_run,
            "status": self._resolve_status(len(accepted_payloads), len(rejected_payloads), bool(errors)),
            "accepted_count": len(accepted_payloads),
            "rejected_count": len(rejected_payloads),
            "broker_order_ids": broker_order_ids,
            "accepted": accepted_payloads,
            "rejected": rejected_payloads,
            "normalized_orders": validation["normalized_orders"],
            "errors": errors,
            "submit_time": submit_time,
        }
        response["idempotency_status"] = "new"
        self.storage.record_submission(
            SubmitReceipt(
                request_id=request.request_id,
                request_fingerprint=request_fingerprint,
                strategy_id=request.strategy_id,
                trade_date=request.trade_date,
                account_id=request.account_id,
                dry_run=request.dry_run,
                normalized_orders=deepcopy(validation["normalized_orders"]),
                response=deepcopy(response),
                recorded_at=submit_time,
            )
        )
        return response

    # ── Cancel ─────────────────────────────────────────────────────────

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

        if not self._xtquant_available:
            return {
                "request_id": request.request_id,
                "account_id": request.account_id,
                "status": "rejected",
                "canceled_count": 0,
                "rejected_count": len(request.broker_order_ids) + len(request.client_order_ids),
                "canceled": [],
                "rejected": [
                    {
                        "target": oid,
                        "status": "rejected",
                        "reasons": [{"code": "xtquant_unavailable", "message": "xtquant is not available"}],
                    }
                    for oid in request.broker_order_ids + request.client_order_ids
                ],
                "errors": [{"code": "xtquant_unavailable", "message": "xtquant is not available"}],
                "cancel_time": self._now(),
            }

        targets = set(request.broker_order_ids + request.client_order_ids)
        canceled: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        orders = self.storage.list_orders()
        by_broker_id = {o.broker_order_id: o for o in orders}
        cancel_time = self._now()

        for target in targets:
            order = by_broker_id.get(target)
            if order is None:
                # Try cancelling via xtquant anyway
                try:
                    self._xttrader.cancel_order(self._acc, target)
                    rejected.append({
                        "target": target,
                        "status": "rejected",
                        "reasons": [{"code": "order_not_found_locally", "message": "order not in local storage"}],
                    })
                except Exception as exc:
                    rejected.append({
                        "target": target,
                        "status": "rejected",
                        "reasons": [{"code": "order_not_found", "message": f"order not found: {exc}"}],
                    })
                continue
            if order.status in FINAL_ORDER_STATUSES:
                rejected.append({
                    "target": target,
                    "status": "rejected",
                    "reasons": [{"code": "not_cancelable", "message": f"order status {order.status} is final"}],
                })
                continue
            try:
                self._xttrader.cancel_order(self._acc, order.broker_order_id)
            except Exception as exc:
                LOGGER.error("MiniQMT cancel failed for order %s: %s", order.broker_order_id, exc)
                rejected.append({
                    "target": target,
                    "status": "rejected",
                    "reasons": [{"code": "cancel_error", "message": str(exc)}],
                })
                continue
            order.status = "canceled"
            order.updated_at = cancel_time
            order.cancel_reason = request.reason
            self.storage.record_order(order)
            canceled.append(order.to_dict())

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

    # ── Snapshot ───────────────────────────────────────────────────────

    def get_latest_snapshot(self) -> dict[str, Any]:
        stored = self.storage.get_latest_snapshot()
        if self._xtquant_available:
            try:
                account = self.get_account()
                positions = self.get_positions()
                captured_at = self._now()
                snapshot = {
                    "snapshot_id": f"snapshot-{uuid.uuid4().hex[:10]}",
                    "captured_at": captured_at,
                    "trigger": "read",
                    "account": account,
                    "positions": positions,
                    "orders": self.list_orders({}),
                    "trades": self.list_trades({}),
                }
                self.storage.record_snapshot(snapshot)
                self._last_sync_time = captured_at
                return snapshot
            except Exception as exc:
                LOGGER.error("MiniQMT snapshot query failed: %s", exc)
        return stored or {}

    # ── Internal helpers ───────────────────────────────────────────────

    def _normalize_order(self, request: OrderRequest, index: int, order: OrderIntent) -> dict[str, Any]:
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

    def _validate_pre_trade_risk(self, request: OrderRequest) -> list[ValidationIssue]:
        """Run pre-trade risk checks using current account/position state from xtquant."""
        issues: list[ValidationIssue] = []
        try:
            account_raw = self._xttrader.query_account(self._acc)
        except Exception as exc:
            issues.append(ValidationIssue("risk_account_error", f"cannot fetch account state: {exc}"))
            return issues

        available_cash = float(account_raw.get("available_cash", 0.0) or 0.0)
        projected_cash = available_cash

        try:
            positions_raw = self._xttrader.query_stock_positions(self._acc) or []
        except Exception:
            positions_raw = []

        available_volume: dict[str, int] = {}
        for pos in positions_raw:
            symbol = str(pos.get("stock_code", pos.get("symbol", "")))
            vol = int(pos.get("can_use_volume", pos.get("available_volume", 0)) or 0)
            if symbol:
                available_volume[symbol] = vol

        for index, order in enumerate(request.orders):
            if order.side == "SELL":
                avail = available_volume.get(order.symbol, 0)
                if order.quantity > avail:
                    issues.append(
                        ValidationIssue(
                            "insufficient_available_volume",
                            f"SELL {order.symbol} quantity {order.quantity} exceeds available {avail}",
                            f"orders[{index}].quantity",
                        )
                    )
                continue

            if order.side == "BUY":
                ref_price = order.limit_price
                if ref_price is None or ref_price <= 0:
                    issues.append(
                        ValidationIssue(
                            "missing_limit_price",
                            f"cannot estimate cash for BUY {order.symbol} without limit_price",
                            f"orders[{index}].limit_price",
                        )
                    )
                    continue
                required = round(order.quantity * ref_price, 2)
                if required > projected_cash:
                    issues.append(
                        ValidationIssue(
                            "insufficient_available_cash",
                            f"BUY {order.symbol} needs {required:.2f} but only {projected_cash:.2f} available",
                            f"orders[{index}].quantity",
                        )
                    )
                    continue
                projected_cash -= required

        return issues

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

    def _index_from_issue_field(self, field_name: str) -> int | None:
        try:
            return int(field_name.split("orders[", 1)[1].split("]", 1)[0])
        except (IndexError, ValueError):
            return None

    def _match_filters(self, record: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, value in filters.items():
            if not value:
                continue
            if key == "order_id" and str(record.get("broker_order_id", "")) != value:
                return False
            if key in {"trade_date", "symbol", "status", "strategy_id", "request_id", "intent_id"}:
                if str(record.get(key, "")) != value:
                    return False
        return True

    def _match_trade_filters(self, record: dict[str, Any], filters: dict[str, str]) -> bool:
        for key, value in filters.items():
            if not value:
                continue
            if key == "order_id" and record.get("broker_order_id") != value:
                return False
            if key in {"trade_date", "symbol", "strategy_id"} and str(record.get(key, "")) != value:
                return False
        return True

    def _resolve_status(self, accepted_count: int, rejected_count: int, has_errors: bool) -> str:
        if has_errors and accepted_count == 0:
            return "rejected"
        if accepted_count > 0 and rejected_count > 0:
            return "partial"
        if accepted_count > 0:
            return "accepted"
        return "rejected"

    def _fingerprint_submit_request(self, request: OrderRequest, normalized_orders: list[dict[str, Any]]) -> str:
        canonical = {
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "dry_run": request.dry_run,
            "normalized_orders": normalized_orders,
        }
        encoded = json.dumps(canonical, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _build_replayed_submit_response(self, receipt: SubmitReceipt) -> dict[str, Any]:
        response = deepcopy(receipt.response)
        response["idempotency_status"] = "replayed"
        response["original_submit_time"] = response.get("submit_time") or receipt.recorded_at
        response["replay_time"] = self._now()
        return response

    def _build_request_conflict_response(
        self,
        request: OrderRequest,
        validation: dict[str, Any],
        receipt: SubmitReceipt,
    ) -> dict[str, Any]:
        reasons = [{"code": "request_id_conflict", "message": "request_id was already used for a different submit payload"}]
        return {
            "request_id": request.request_id,
            "strategy_id": request.strategy_id,
            "trade_date": request.trade_date,
            "account_id": request.account_id,
            "dry_run": request.dry_run,
            "status": "rejected",
            "accepted_count": 0,
            "rejected_count": len(validation["normalized_orders"]),
            "broker_order_ids": [],
            "accepted": [],
            "rejected": [
                {"intent_id": item["intent_id"], "status": "rejected", "reasons": reasons}
                for item in validation["normalized_orders"]
            ],
            "normalized_orders": validation["normalized_orders"],
            "errors": reasons,
            "submit_time": self._now(),
            "idempotency_status": "conflict",
            "original_submit_time": receipt.recorded_at,
        }

    def _build_submit_disabled_response(self, validation: dict[str, Any]) -> dict[str, Any]:
        return {
            **validation,
            "status": "rejected",
            "errors": list(validation.get("errors", []))
            + [{"code": "submit_disabled", "message": "broker submit is disabled via config"}],
            "accepted": [],
            "accepted_count": 0,
            "rejected_count": len(validation.get("normalized_orders", [])),
            "rejected": [
                {
                    "intent_id": item["intent_id"],
                    "status": "rejected",
                    "reasons": [{"code": "submit_disabled", "message": "broker submit is disabled via config"}],
                }
                for item in validation.get("normalized_orders", [])
            ],
            "submit_time": self._now(),
            "broker_order_ids": [],
        }

    def _build_xtquant_unavailable_response(self, validation: dict[str, Any]) -> dict[str, Any]:
        return {
            **validation,
            "status": "rejected",
            "errors": list(validation.get("errors", []))
            + [{"code": "xtquant_unavailable", "message": "MiniQMT (xtquant) is not connected"}],
            "accepted": [],
            "accepted_count": 0,
            "rejected_count": len(validation.get("normalized_orders", [])),
            "rejected": [
                {
                    "intent_id": item["intent_id"],
                    "status": "rejected",
                    "reasons": [{"code": "xtquant_unavailable", "message": "MiniQMT (xtquant) is not connected"}],
                }
                for item in validation.get("normalized_orders", [])
            ],
            "submit_time": self._now(),
            "broker_order_ids": [],
        }

    def _now(self) -> str:
        return (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
