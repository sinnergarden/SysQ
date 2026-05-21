"""Tests for Phase 1 execution chain: models, converter, risk, ledger, service."""

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from qsys.execution.converter import (
    from_intents_json,
    from_order_intents_csv,
    from_plan_dataframe,
    to_broker_order_requests,
)
from qsys.execution.models import (
    BrokerOrderAck,
    BrokerOrderRequest,
    OS_CANCELLED,
    OS_FILLED,
    OS_PARTIAL,
    OS_PENDING,
    OS_REJECTED,
    OS_SUBMITTED,
    OS_SUBMIT_UNKNOWN,
    FINAL_STATUSES,
    validate_transition,
)
from qsys.execution.service import ExecutionService
from qsys.risk.pre_trade import PreTradeRiskResult, check_pre_trade_risk


# ── Models: state transitions ───────────────────────────────────────────

class TestValidateTransition:
    def test_valid_transitions(self):
        # Pending -> Submitted
        validate_transition(OS_PENDING, OS_SUBMITTED)
        # Submitted -> Partial
        validate_transition(OS_SUBMITTED, OS_PARTIAL)
        # Partial -> Filled
        validate_transition(OS_PARTIAL, OS_FILLED)
        # Submitted -> Cancelled
        validate_transition(OS_SUBMITTED, OS_CANCELLED)

    def test_invalid_transition_raises(self):
        with pytest.raises(ValueError, match="Invalid order status transition"):
            validate_transition(OS_FILLED, OS_PENDING)
        with pytest.raises(ValueError, match="Invalid order status transition"):
            validate_transition(OS_CANCELLED, OS_SUBMITTED)
        with pytest.raises(ValueError, match="Invalid order status transition"):
            validate_transition(OS_REJECTED, OS_PARTIAL)

    def test_final_statuses_empty_transitions(self):
        for s in FINAL_STATUSES:
            with pytest.raises(ValueError, match="Invalid order status transition"):
                validate_transition(s, OS_SUBMITTED)

    def test_partial_self_transition_allowed(self):
        # Partial -> Partial is allowed (same status after another fill)
        validate_transition(OS_PARTIAL, OS_PARTIAL)

    def test_pending_to_submitted_allowed(self):
        validate_transition(OS_PENDING, OS_SUBMITTED)


# ── Converter ───────────────────────────────────────────────────────────

class TestConverter:
    def test_to_broker_order_requests(self):
        rows = [
            {"symbol": "600000.SH", "side": "buy", "amount": 100, "price": 10.5, "intent_id": "id1", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
            {"symbol": "600001.SH", "side": "sell", "amount": 200, "price": 20.0, "intent_id": "id2", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
        ]
        result = to_broker_order_requests(intent_rows=rows, trade_date="2026-04-25", run_id="test_run")
        assert len(result) == 2
        assert result[0].symbol == "600000.SH"
        assert result[0].side == "buy"
        assert result[0].quantity == 100
        assert result[0].limit_price == 10.5
        assert result[0].price_source == "close"
        assert result[0].price_snapshot_time == "2026-04-25T15:00:00"
        assert result[1].side == "sell"
        assert result[1].quantity == 200
        assert result[1].limit_price == 20.0

    def test_to_broker_order_requests_filters_invalid(self):
        rows = [
            {"symbol": "", "side": "buy", "amount": 100},  # empty symbol
            {"symbol": "600000.SH", "side": "invalid", "amount": 100},  # bad side
            {"symbol": "600000.SH", "side": "buy", "amount": 0},  # zero qty
        ]
        result = to_broker_order_requests(intent_rows=rows, trade_date="2026-04-25", run_id="test_run")
        assert len(result) == 0

    def test_to_broker_order_requests_requires_price_fields(self):
        # Missing price_source from row and from default
        rows = [
            {"symbol": "600000.SH", "side": "buy", "amount": 100, "price": 10.5, "intent_id": "id1", "price_snapshot_time": "2026-04-25T15:00:00"},
        ]
        result = to_broker_order_requests(intent_rows=rows, trade_date="2026-04-25", run_id="test_run")
        assert len(result) == 0

        # Missing price_snapshot_time from row and from default
        rows = [
            {"symbol": "600000.SH", "side": "buy", "amount": 100, "price": 10.5, "intent_id": "id2", "price_source": "close"},
        ]
        result = to_broker_order_requests(intent_rows=rows, trade_date="2026-04-25", run_id="test_run")
        assert len(result) == 0

        # Both present — should pass
        rows = [
            {"symbol": "600000.SH", "side": "buy", "amount": 100, "price": 10.5, "intent_id": "id3", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
        ]
        result = to_broker_order_requests(intent_rows=rows, trade_date="2026-04-25", run_id="test_run")
        assert len(result) == 1

    def test_from_order_intents_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "order_intents.csv"
            pd.DataFrame(
                [
                    {"instrument": "600000.SH", "side": "buy", "requested_qty": 100, "price": 10.5, "target_weight": 0.05, "reason": "rebalance", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
                    {"instrument": "600001.SH", "side": "sell", "requested_qty": 200, "price": 20.0, "target_weight": 0.0, "reason": "rebalance", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
                ]
            ).to_csv(csv_path, index=False)

            result = from_order_intents_csv(csv_path, trade_date="2026-04-25", run_id="test_run")
            assert len(result) == 2
            assert result[0].symbol == "600000.SH"
            assert result[0].side == "buy"
            assert result[1].symbol == "600001.SH"
            assert result[1].side == "sell"

    def test_from_order_intents_csv_empty_or_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "nonexistent.csv"
            result = from_order_intents_csv(missing, trade_date="2026-04-25", run_id="test_run")
            assert result == []

            empty_path = Path(tmpdir) / "empty.csv"
            empty_path.write_text("", encoding="utf-8")
            result = from_order_intents_csv(empty_path, trade_date="2026-04-25", run_id="test_run")
            assert result == []

    def test_from_plan_dataframe(self):
        df = pd.DataFrame(
            [
                {"symbol": "600000.SH", "side": "buy", "amount": 100, "price": 10.5, "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
                {"symbol": "600001.SH", "side": "sell", "amount": 200, "price": 12.0, "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
            ]
        )
        result = from_plan_dataframe(df, trade_date="2026-04-25", run_id="test_run")
        assert len(result) == 2
        assert result[0].limit_price == 10.5
        assert result[0].quantity == 100

    def test_from_plan_dataframe_empty(self):
        result = from_plan_dataframe(pd.DataFrame(), trade_date="2026-04-25", run_id="test_run")
        assert result == []

    def test_from_intents_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path = Path(tmpdir) / "intents.json"
            payload = {
                "intents": [
                    {"symbol": "600000.SH", "side": "buy", "amount": 100, "price": 10.5, "intent_id": "i1", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
                    {"symbol": "600001.SH", "side": "sell", "amount": 200, "price": 20.0, "intent_id": "i2", "price_source": "close", "price_snapshot_time": "2026-04-25T15:00:00"},
                ]
            }
            json_path.write_text(json.dumps(payload), encoding="utf-8")

            result = from_intents_json(json_path, trade_date="2026-04-25", run_id="test_run")
            assert len(result) == 2
            assert result[0].symbol == "600000.SH"


# ── Pre-trade risk ──────────────────────────────────────────────────────

class FakeSnapshot:
    """Minimal ExecutionSnapshot stand-in for pre-trade risk tests."""

    def __init__(self):
        self._suspended: set[str] = set()
        self._limit_up: dict[str, float] = {}
        self._limit_down: dict[str, float] = {}
        self._positions: dict[str, object] = {}

    def add_position(self, symbol: str, total: int, sellable: int) -> None:
        self._positions[symbol] = type("Pos", (), {"total_amount": total, "sellable_amount": sellable})()

    def is_suspended(self, symbol: str) -> bool:
        return symbol in self._suspended

    def is_limit_up(self, symbol: str, price: float) -> bool:
        return symbol in self._limit_up and price >= self._limit_up[symbol]

    def is_limit_down(self, symbol: str, price: float) -> bool:
        return symbol in self._limit_down and price <= self._limit_down[symbol]

    def get_position(self, symbol: str):
        return self._positions.get(symbol)

class TestPreTradeRisk:
    def make_request(self, symbol, side, qty, limit_price=None, price_source="close", price_snapshot_time="2026-04-25T15:00:00"):
        return BrokerOrderRequest(
            intent_id=f"{side}:{symbol}",
            symbol=symbol,
            side=side,
            order_type="limit",
            quantity=qty,
            limit_price=limit_price,
            price_source=price_source,
            price_snapshot_time=price_snapshot_time,
        )

    def test_all_passed(self):
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=2000.0)
        assert result.all_passed
        assert len(result.passed) == 1
        assert len(result.failed) == 0

    def test_blacklist_rejected(self):
        reqs = [self.make_request("ST001.SH", "buy", 100, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=2000.0, blacklist={"ST001.SH"})
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "blacklisted" in result.failed[0][1]

    def test_insufficient_cash(self):
        reqs = [self.make_request("600000.SH", "buy", 1000, 100.0)]  # needs 100k
        result = check_pre_trade_risk(reqs, available_cash=1000.0)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "insufficient cash" in result.failed[0][1]

    def test_cash_deducted_across_orders(self):
        reqs = [
            self.make_request("600000.SH", "buy", 100, 10.0),   # 1000
            self.make_request("600001.SH", "buy", 100, 10.0),   # 1000
        ]
        result = check_pre_trade_risk(reqs, available_cash=1500.0)
        assert not result.all_passed
        assert len(result.passed) == 1
        assert len(result.failed) == 1

    def test_price_deviation(self):
        reqs = [self.make_request("600000.SH", "buy", 100, 15.0)]
        result = check_pre_trade_risk(
            reqs, available_cash=2000.0,
            reference_prices={"600000.SH": 10.0},  # 50% deviation
            max_price_deviation_pct=20.0,
        )
        assert not result.all_passed
        assert len(result.failed) == 1

    def test_sell_orders_skip_cash_check(self):
        reqs = [self.make_request("600000.SH", "sell", 100, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=0.0)
        assert result.all_passed

    def test_summary_property(self):
        reqs = [
            self.make_request("600000.SH", "buy", 100, 10.0),
            self.make_request("BLACK.SH", "buy", 100, 10.0),
        ]
        result = check_pre_trade_risk(reqs, available_cash=2000.0, blacklist={"BLACK.SH"})
        s = result.summary
        assert s["passed_count"] == 1
        assert s["failed_count"] == 1
        assert s["failed_orders"][0]["symbol"] == "BLACK.SH"

    # ── ExecutionSnapshot-based risk checks ──────────────────────────

    def test_lot_size_check(self):
        """Quantity must be a multiple of 100."""
        reqs = [self.make_request("600000.SH", "buy", 50, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=5000.0)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "multiple of 100" in result.failed[0][1]

    def test_lot_size_check_passes_for_valid(self):
        """Quantity of 100 (1 lot) should pass lot size check."""
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=5000.0)
        assert result.all_passed

    def test_suspended_rejected(self):
        snapshot = FakeSnapshot()
        snapshot._suspended.add("600000.SH")
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "suspended" in result.failed[0][1]

    def test_limit_up_blocks_buy(self):
        snapshot = FakeSnapshot()
        snapshot._limit_up["600000.SH"] = 11.0  # limit_up price
        reqs = [self.make_request("600000.SH", "buy", 100, 11.0)]
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "limit_up" in result.failed[0][1]

    def test_limit_up_below_threshold_passes(self):
        snapshot = FakeSnapshot()
        snapshot._limit_up["600000.SH"] = 11.0  # limit_up price
        reqs = [self.make_request("600000.SH", "buy", 100, 10.5)]  # below limit_up
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert result.all_passed

    def test_limit_down_blocks_sell(self):
        snapshot = FakeSnapshot()
        snapshot._limit_down["600000.SH"] = 9.0  # limit_down price
        reqs = [self.make_request("600000.SH", "sell", 100, 9.0)]
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "limit_down" in result.failed[0][1]

    def test_limit_down_above_threshold_passes(self):
        snapshot = FakeSnapshot()
        snapshot._limit_down["600000.SH"] = 9.0  # limit_down price
        snapshot.add_position("600000.SH", total=500, sellable=500)
        reqs = [self.make_request("600000.SH", "sell", 100, 9.5)]  # above limit_down
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert result.all_passed

    def test_sell_exceeds_sellable(self):
        snapshot = FakeSnapshot()
        snapshot.add_position("600000.SH", total=500, sellable=200)
        reqs = [self.make_request("600000.SH", "sell", 300, 10.0)]  # 300 > 200 sellable
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "sellable" in result.failed[0][1]

    def test_sell_within_sellable_passes(self):
        snapshot = FakeSnapshot()
        snapshot.add_position("600000.SH", total=500, sellable=200)
        reqs = [self.make_request("600000.SH", "sell", 200, 10.0)]  # 200 == sellable
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert result.all_passed

    def test_sell_without_position_fails(self):
        """Sell order for symbol not in snapshot positions should fail closed."""
        snapshot = FakeSnapshot()  # no positions added
        reqs = [self.make_request("600000.SH", "sell", 100, 10.0)]
        result = check_pre_trade_risk(reqs, available_cash=5000.0, snapshot=snapshot)
        assert not result.all_passed
        assert len(result.failed) == 1
        assert "no position found" in result.failed[0][1]

    def test_snapshot_checks_skip_when_snapshot_none(self):
        """Without snapshot, limit_up/down/suspended/sellable checks are skipped."""
        reqs = [self.make_request("SUSPENDED.SH", "buy", 100, 10.0)]
        # No snapshot passed — should pass (no snapshot checks)
        result = check_pre_trade_risk(reqs, available_cash=5000.0)
        assert result.all_passed


# ── TradeLedger state machine ───────────────────────────────────────────

class TestTradeLedgerExecutionRequests:
    @pytest.fixture
    def ledger(self):
        from qsys.trader.database import TradeLedger
        with tempfile.TemporaryDirectory() as tmpdir:
            yield TradeLedger(db_path=Path(tmpdir) / "test.db")

    def test_record_and_retrieve_pending(self, ledger):
        idem_key = ledger.record_pending_intent(
            intent_id="test_intent_1",
            run_id="run_001",
            trading_date="2026-04-25",
        )
        row = ledger.get_intent(idem_key)
        assert row is not None
        assert row["status"] == OS_PENDING

    def test_record_pending_is_idempotent(self, ledger):
        ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        # Should not raise — INSERT OR IGNORE

    def test_valid_state_transition(self, ledger):
        idem_key = ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        ledger.update_intent_status(idempotency_key=idem_key, status=OS_SUBMITTED, broker_order_id="bo1")
        row = ledger.get_intent(idem_key)
        assert row["status"] == OS_SUBMITTED
        assert row["broker_order_id"] == "bo1"

    def test_invalid_transition_raises(self, ledger):
        idem_key = ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        ledger.update_intent_status(idempotency_key=idem_key, status=OS_SUBMITTED, broker_order_id="bo1")
        # Can't go from filled back to pending
        ledger.update_intent_status(idempotency_key=idem_key, status=OS_FILLED, broker_order_id="bo1")
        with pytest.raises(ValueError, match="Invalid order status transition"):
            ledger.update_intent_status(idempotency_key=idem_key, status=OS_PENDING)

    def test_full_lifecycle(self, ledger):
        iid = "lifecycle_test"
        idem_key = ledger.record_pending_intent(intent_id=iid, run_id="r1", trading_date="2026-04-25")
        assert ledger.get_intent(idem_key)["status"] == OS_PENDING

        ledger.update_intent_status(idempotency_key=idem_key, status=OS_SUBMITTED, broker_order_id="bo1")
        assert ledger.get_intent(idem_key)["status"] == OS_SUBMITTED

        ledger.update_intent_status(idempotency_key=idem_key, status=OS_PARTIAL, broker_order_id="bo1")
        assert ledger.get_intent(idem_key)["status"] == OS_PARTIAL

        ledger.update_intent_status(idempotency_key=idem_key, status=OS_FILLED, broker_order_id="bo1")
        assert ledger.get_intent(idem_key)["status"] == OS_FILLED

    def test_get_run_intents(self, ledger):
        ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        ledger.record_pending_intent(intent_id="i2", run_id="r1", trading_date="2026-04-25")
        ledger.record_pending_intent(intent_id="i3", run_id="r2", trading_date="2026-04-25")
        run1 = ledger.get_run_intents(run_id="r1")
        assert len(run1) == 2
        run2 = ledger.get_run_intents(run_id="r2")
        assert len(run2) == 1

    def test_count_run_intents_by_status(self, ledger):
        idem_key1 = ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        ledger.record_pending_intent(intent_id="i2", run_id="r1", trading_date="2026-04-25")
        ledger.update_intent_status(idempotency_key=idem_key1, status=OS_SUBMITTED, broker_order_id="bo1")
        counts = ledger.count_run_intents_by_status(run_id="r1")
        assert counts.get(OS_SUBMITTED) == 1
        assert counts.get(OS_PENDING) == 1

    def test_has_submitted_intents(self, ledger):
        idem_key = ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        assert not ledger.has_submitted_intents(run_id="r1")
        ledger.update_intent_status(idempotency_key=idem_key, status=OS_SUBMITTED, broker_order_id="bo1")
        assert ledger.has_submitted_intents(run_id="r1")

    def test_get_nonexistent_intent(self, ledger):
        assert ledger.get_intent("no_such_key") is None

    def test_get_intents_by_broker_order_id(self, ledger):
        idem_key = ledger.record_pending_intent(intent_id="i1", run_id="r1", trading_date="2026-04-25")
        ledger.update_intent_status(idempotency_key=idem_key, status=OS_SUBMITTED, broker_order_id="bo1")
        row = ledger.get_intents_by_broker_order_id("bo1")
        assert row is not None
        assert row["intent_id"] == "i1"


# ── ExecutionService (unit tests with mocks) ────────────────────────────

class TestExecutionService:
    @pytest.fixture
    def adapter(self):
        mock = MagicMock(spec=["submit_broker_requests", "fetch_orders", "fetch_trades", "fetch_positions", "fetch_account_snapshot"])
        # Default: submit returns one ack per request
        def fake_submit(requests, **kwargs):
            return [
                BrokerOrderAck(
                    intent_id=r.intent_id,
                    broker_order_id=f"mock-{r.intent_id}",
                    status="accepted",
                )
                for r in requests
            ]
        mock.submit_broker_requests.side_effect = fake_submit
        mock.fetch_orders.return_value = []
        mock.fetch_trades.return_value = []
        return mock

    @pytest.fixture
    def ledger(self):
        from qsys.trader.database import TradeLedger
        with tempfile.TemporaryDirectory() as tmpdir:
            yield TradeLedger(db_path=Path(tmpdir) / "test.db")

    @pytest.fixture
    def service(self, adapter, ledger):
        return ExecutionService(adapter, ledger, strategy_id="alpha_v1")

    def make_request(self, symbol, side, qty, limit_price=None, price_source="close", price_snapshot_time="2026-04-25T15:00:00"):
        return BrokerOrderRequest(
            intent_id=f"{side}:{symbol}:{qty}",
            symbol=symbol,
            side=side,
            order_type="limit",
            quantity=qty,
            limit_price=limit_price,
            price_source=price_source,
            price_snapshot_time=price_snapshot_time,
        )

    def test_prepare_and_submit_success(self, service, ledger):
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        result = service.prepare_and_submit(
            requests=reqs,
            trade_date="2026-04-25",
            run_id="test_run_001",
            risk_check=False,
        )
        assert result["status"] == "submitted"
        assert result["submitted_count"] == 1

        # Check ledger
        row = ledger.get_intent_by_intent_id("buy:600000.SH:100")
        assert row is not None
        assert row["status"] == OS_SUBMITTED

    def test_idempotency_skip_on_rerun(self, service, ledger):
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        # First run — submits
        service.prepare_and_submit(requests=reqs, trade_date="2026-04-25", run_id="run_001", risk_check=False)
        # Second run with same intents — should skip
        result = service.prepare_and_submit(requests=reqs, trade_date="2026-04-25", run_id="run_001", risk_check=False)
        assert result["status"] == "idempotent_skip"

        # Adapter should have been called only once
        assert service.adapter.submit_broker_requests.call_count == 1

    def test_risk_check_blocks_failed_orders(self, service, ledger):
        reqs = [
            self.make_request("600000.SH", "buy", 100, 10.0),   # passes
            self.make_request("BLACK.SH", "buy", 100, 10.0),    # blacklisted
        ]
        result = service.prepare_and_submit(
            requests=reqs,
            trade_date="2026-04-25",
            run_id="run_002",
            risk_check=True,
            available_cash=2000.0,
            blacklist={"BLACK.SH"},
        )
        assert result["submitted_count"] == 1
        assert service.adapter.submit_broker_requests.call_count == 1

        # Blacklisted should be rejected in ledger
        rejected = ledger.get_intent_by_intent_id("buy:BLACK.SH:100")
        assert rejected is not None
        assert rejected["status"] == OS_REJECTED

    def test_broker_fail_closed(self, service, ledger):
        service.adapter.submit_broker_requests.side_effect = RuntimeError("broker unreachable")
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        with pytest.raises(RuntimeError, match="broker unreachable"):
            service.prepare_and_submit(requests=reqs, trade_date="2026-04-25", run_id="run_003", risk_check=False)

        # Intents should be marked submit_unknown (not rejected) due to ambiguous submit failure
        row = ledger.get_intent_by_intent_id("buy:600000.SH:100")
        assert row is not None
        assert row["status"] == OS_SUBMIT_UNKNOWN

    def test_empty_requests(self, service):
        result = service.prepare_and_submit(requests=[], trade_date="2026-04-25", run_id="run_empty", risk_check=False)
        assert result["status"] == "skipped"

    def test_poll_updates(self, service, ledger):
        # First submit order
        reqs = [self.make_request("600000.SH", "buy", 100, 10.0)]
        service.prepare_and_submit(requests=reqs, trade_date="2026-04-25", run_id="run_poll", risk_check=False)

        # Mock broker returns filled status
        service.adapter.fetch_orders.return_value = [
            type("Report", (), {
                "intent_id": "buy:600000.SH:100",
                "broker_order_id": "mock-buy:600000.SH:100",
                "symbol": "600000.SH",
                "side": "buy",
                "status": OS_FILLED,
                "filled_quantity": 100,
                "filled_price": 10.0,
                "remaining_quantity": 0,
                "message": "",
            })()
        ]

        result = service.poll_updates(run_id="run_poll")
        assert result["status"] == "polled"
        assert len(result["transitions"]) == 1
        assert result["transitions"][0]["from"] == OS_SUBMITTED
        assert result["transitions"][0]["to"] == OS_FILLED

    def test_poll_updates_no_changes(self, service, ledger):
        result = service.poll_updates(run_id="nonexistent_run")
        assert result["status"] == "poll_failed" or result["fill_count"] == 0

    def test_reconcile_run(self, service, adapter):
        adapter.fetch_positions.return_value = []
        adapter.fetch_account_snapshot.return_value = type("Acct", (), {"cash": 10000.0})()
        result = service.reconcile_run(run_id="test_run")
        assert result["status"] == "reconciled"

    def test_reconcile_run_broker_fail(self, service, adapter):
        adapter.fetch_positions.side_effect = RuntimeError("broker down")
        result = service.reconcile_run(run_id="test_run")
        assert result["status"] == "reconcile_failed"
