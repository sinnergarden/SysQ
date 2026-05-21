from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from qsys.execution.converter import from_order_intents_csv, from_plan_dataframe
from qsys.execution.models import (
    BrokerOrderRequest,
    FINAL_STATUSES,
    OS_FILLED,
    OS_PARTIAL,
    OS_PENDING,
    OS_REJECTED,
    OS_SUBMITTED,
    OS_SUBMIT_UNKNOWN,
)
from qsys.risk.pre_trade import PreTradeRiskResult, check_pre_trade_risk
from qsys.utils.logger import log

if TYPE_CHECKING:
    from qsys.broker.miniqmt import MiniQMTAdapter
    from qsys.trader.database import TradeLedger


class ExecutionService:
    """Orchestrate the order lifecycle: submit -> poll -> fill -> reconcile.

    Minimal public API for Phase 1::

        service = ExecutionService(adapter, ledger, strategy_id="alpha_v1")
        result = service.prepare_and_submit(
            requests=broker_order_requests,
            trade_date="2026-04-25",
            run_id="shadow_2026-04-25_090807",
        )
        service.poll_updates(run_id="shadow_2026-04-25_090807")
    """

    def __init__(
        self,
        adapter: MiniQMTAdapter,
        ledger: TradeLedger,
        *,
        strategy_id: str = "",
        account_name: str = "real",
        pre_trade_risk_kw: dict[str, Any] | None = None,
    ) -> None:
        self.adapter = adapter
        self.ledger = ledger
        self.strategy_id = strategy_id
        self.account_name = account_name
        self.pre_trade_risk_kw = pre_trade_risk_kw or {}

    def _idempotency_key(self, intent_id: str, run_id: str, trade_date: str) -> str:
        """Build composite idempotency_key from execution context."""
        return f"{self.strategy_id}:{self.account_name}:{trade_date}:{trade_date}:{run_id}:{intent_id}"

    # ── Submit ───────────────────────────────────────────────────────────

    def prepare_and_submit(
        self,
        *,
        requests: list[BrokerOrderRequest],
        trade_date: str,
        run_id: str,
        dry_run: bool = False,
        risk_check: bool = True,
        available_cash: float = 0.0,
        blacklist: set[str] | None = None,
        reference_prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Full submit flow: record intents -> risk check -> submit -> record acks.

        Steps:
        1. Record every intent in the ledger as pending
        2. Run pre-trade risk checks (unless *risk_check* is False)
        3. Check idempotency — skip intents already submitted
        4. Submit to broker via adapter
        5. Record acks in ledger (broker_order_id + status)

        Returns a summary dict with counts and errors.
        """
        log.info(
            "ExecutionService: preparing %d requests for %s run=%s",
            len(requests),
            trade_date,
            run_id,
        )

        # Step 1: Record all intents as pending
        idem_keys: dict[str, str] = {}
        for req in requests:
            idem_key = self.ledger.record_pending_intent(
                intent_id=req.intent_id,
                run_id=run_id,
                trading_date=trade_date,
                strategy_id=self.strategy_id,
                account_name=self.account_name,
                signal_date=trade_date,
                execution_date=trade_date,
                request_payload={
                    "symbol": req.symbol,
                    "side": req.side,
                    "quantity": req.quantity,
                    "limit_price": req.limit_price,
                    "order_type": req.order_type,
                },
            )
            idem_keys[req.intent_id] = idem_key

        # Step 2: Pre-trade risk
        if risk_check:
            risk_result = check_pre_trade_risk(
                requests,
                available_cash=available_cash,
                blacklist=blacklist or set(),
                reference_prices=reference_prices or {},
                **self.pre_trade_risk_kw,
            )
            for req, reason in risk_result.failed:
                self.ledger.update_intent_status(
                    idempotency_key=idem_keys[req.intent_id],
                    status=OS_REJECTED,
                    error=reason,
                )
                log.warning("Pre-trade risk FAILED: %s %s reason=%s", req.symbol, req.side, reason)
            requests = risk_result.passed

        if not requests:
            return {
                "status": "skipped",
                "submitted_count": 0,
                "rejected_count": 0,
                "errors": ["all_requests_failed_risk_check"],
                "acks": [],
            }

        # Step 3: Idempotency — skip intents that are already submitted
        to_submit: list[BrokerOrderRequest] = []
        for req in requests:
            existing = self.ledger.get_intent(idem_keys[req.intent_id])
            if existing and existing["status"] != OS_PENDING:
                log.info(
                    "Skipping already-submitted intent %s (status=%s)",
                    req.intent_id,
                    existing["status"],
                )
                continue
            to_submit.append(req)

        if not to_submit:
            return {
                "status": "idempotent_skip",
                "submitted_count": 0,
                "rejected_count": 0,
                "errors": [],
                "acks": [],
            }

        # Step 4: Submit to broker
        try:
            acks = self.adapter.submit_broker_requests(
                to_submit,
                strategy_id=self.strategy_id,
                trade_date=trade_date,
                dry_run=dry_run,
            )
        except Exception as exc:
            log.error("Broker submit failed for run=%s: %s", run_id, exc)
            # Mark as submit_unknown — broker may or may not have received them
            for req in to_submit:
                self.ledger.update_intent_status(
                    idempotency_key=idem_keys[req.intent_id],
                    status=OS_SUBMIT_UNKNOWN,
                    error=f"submit_failed: {exc}",
                )
            raise

        # Step 5: Record acks
        submitted = 0
        rejected = 0
        for ack in acks:
            if ack.status in ("accepted", "pending"):
                target_status = OS_SUBMITTED
                submitted += 1
            else:
                target_status = OS_REJECTED
                rejected += 1

            self.ledger.update_intent_status(
                idempotency_key=idem_keys.get(ack.intent_id, ""),
                status=target_status,
                broker_order_id=ack.broker_order_id,
                ack_payload=ack.extra,
                error=ack.message if ack.status == "rejected" else "",
            )

        log.info(
            "Submit complete: %d submitted, %d rejected (run=%s)",
            submitted,
            rejected,
            run_id,
        )
        return {
            "status": "submitted",
            "submitted_count": submitted,
            "rejected_count": rejected,
            "errors": [],
            "acks": [{"intent_id": a.intent_id, "broker_order_id": a.broker_order_id, "status": a.status} for a in acks],
        }

    # ── Polling ──────────────────────────────────────────────────────────

    def poll_updates(
        self,
        *,
        run_id: str,
        filters: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Poll broker for current order statuses and update the ledger.

        Returns a summary of status transitions.
        """
        try:
            reports = self.adapter.fetch_orders(filters=filters)
        except Exception as exc:
            log.error("Poll failed: %s", exc)
            return {"status": "poll_failed", "error": str(exc), "transitions": [], "fill_count": 0, "errors": []}

        transitions: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        fill_count = 0

        for report in reports:
            if not report.intent_id and not report.broker_order_id:
                continue

            # Find intent — prefer broker_order_id (unique) over intent_id (non-unique)
            intent = None
            if report.broker_order_id:
                intent = self.ledger.get_intents_by_broker_order_id(report.broker_order_id)
            if intent is None and report.intent_id:
                intent = self.ledger.get_intent_by_intent_id(report.intent_id, run_id=run_id)

            if intent is None:
                # Report without a matching local intent — skip
                continue

            current_status = intent["status"]
            if current_status in FINAL_STATUSES:
                continue  # Terminal state — no further updates

            if report.status != current_status:
                try:
                    self.ledger.update_intent_status(
                        idempotency_key=intent["idempotency_key"],
                        status=report.status,
                        broker_order_id=report.broker_order_id or intent.get("broker_order_id", ""),
                    )
                    transitions.append(
                        {
                            "intent_id": intent["intent_id"],
                            "broker_order_id": report.broker_order_id,
                            "from": current_status,
                            "to": report.status,
                        }
                    )
                except ValueError as exc:
                    log.error(
                        "Invalid status transition for %s: %s -> %s (%s)",
                        intent.get("intent_id", "?"),
                        current_status,
                        report.status,
                        exc,
                    )
                    errors.append(
                        {
                            "intent_id": intent.get("intent_id", ""),
                            "broker_order_id": report.broker_order_id or intent.get("broker_order_id", ""),
                            "from": current_status,
                            "to": report.status,
                            "error": str(exc),
                        }
                    )

            if report.status in (OS_PARTIAL, OS_FILLED) and report.broker_order_id:
                fill_count += self._record_fills_for_order(
                    run_id=run_id,
                    broker_order_id=report.broker_order_id,
                    filters=filters,
                )

        return {
            "status": "poll_inconsistent" if errors else "polled",
            "transitions": transitions,
            "fill_count": fill_count,
            "errors": errors,
        }

    def _record_fills_for_order(
        self,
        *,
        run_id: str,
        broker_order_id: str,
        filters: dict[str, str] | None = None,
    ) -> int:
        """Fetch trades for a broker_order_id and record them in the ledger."""
        order_filters = dict(filters or {})
        order_filters["order_id"] = broker_order_id
        try:
            fills = self.adapter.fetch_trades(filters=order_filters)
        except Exception:
            return 0

        count = 0
        for fill in fills:
            fill_id = fill.broker_trade_id or f"{fill.broker_order_id}:{fill.quantity}:{fill.price}"
            try:
                self.ledger.insert_fill(
                    fill_id=fill_id,
                    order_id=fill.broker_order_id,
                    run_id=run_id,
                    trading_date=self._resolve_trade_date(filters),
                    symbol=fill.symbol,
                    side=fill.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    fee=fill.fee,
                    filled_at=fill.filled_at,
                )
                count += 1
            except Exception:
                log.warning("Failed to record fill %s", fill_id)
        return count

    @staticmethod
    def _resolve_trade_date(filters: dict[str, str] | None) -> str:
        if filters and filters.get("trade_date"):
            return filters["trade_date"]
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ── Convenience: load from CSV ───────────────────────────────────────

    @classmethod
    def from_order_intents_csv(
        cls,
        csv_path: str | Path,
        *,
        adapter: MiniQMTAdapter,
        ledger: TradeLedger,
        trade_date: str,
        run_id: str,
        strategy_id: str = "",
    ) -> list[BrokerOrderRequest]:
        """Shortcut: read order intents CSV and return BrokerOrderRequest list."""
        return from_order_intents_csv(
            csv_path,
            trade_date=trade_date,
            run_id=run_id,
        )

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile_run(
        self,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        """Compare broker positions with the local ledger for a completed run.

        Returns a diff summary. Uses the existing
        ``qsys.live.reconciliation`` module's approach.
        """
        try:
            broker_positions = self.adapter.fetch_positions()
            broker_account = self.adapter.fetch_account_snapshot()
        except Exception as exc:
            return {"status": "reconcile_failed", "error": str(exc), "position_gaps": [], "cash_gap": None}

        # Build local view from ledger
        intents = self.ledger.get_run_intents(run_id=run_id)
        status_counts = self.ledger.count_run_intents_by_status(run_id=run_id)

        return {
            "status": "reconciled",
            "broker_cash": broker_account.cash,
            "broker_position_count": len(broker_positions),
            "local_intent_count": len(intents),
            "local_status_counts": status_counts,
            "note": "Detailed position-by-position diff is available via qsys.live.reconciliation",
        }
