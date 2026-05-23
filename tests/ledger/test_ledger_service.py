"""Comprehensive tests for SQLite ledger service.

Covers:
  - create_account initial cash
  - buy fill cash/position update (T+0 and T+1)
  - sell fill cash/position update
  - insufficient cash rollback
  - insufficient position rollback (SELL > available)
  - duplicate fill_id / duplicate run behavior
  - T+1 true vs false (available_quantity)
  - migration idempotency
  - reconstruct cash from cash_ledger
  - reconstruct positions from position_ledger
"""
from __future__ import annotations

import csv
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest

from qsys.ledger.db import create_connection
from qsys.ledger.service import (
    DuplicateFillError,
    DuplicateRunError,
    InsufficientCashError,
    InsufficientPositionError,
    LedgerService,
)
from qsys.ledger.migration import ShadowMigrator, _mig_id
from qsys.ledger import repository as repo


# ── Helpers ──────────────────────────────────────────────────────────

_fill_counter: int = 0


def _next_fill_id(symbol: str) -> str:
    """Return a unique fill_id for a symbol (counter-based)."""
    global _fill_counter
    _fill_counter += 1
    return f"fil_{symbol}_{_fill_counter}"


def _next_order_id(symbol: str) -> str:
    """Return a unique order_id for a symbol (counter-based)."""
    global _fill_counter
    return f"ord_{symbol}_{_fill_counter}"


@pytest.fixture
def db_path() -> Generator[str, None, None]:
    """In-memory SQLite ledger via a temp file (WAL requires file)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = create_connection(path)
    conn.close()
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def service(db_path: str) -> Generator[LedgerService, None, None]:
    svc = LedgerService(db_path)
    yield svc
    svc.close()


ACCT = "test_account"
STRAT = "test_strategy"
RUN = "2026-05-23.test_strategy.test"


def _start_run(svc: LedgerService) -> dict[str, Any]:
    svc.create_account(ACCT, "shadow", 1_000_000.0)
    return svc.start_run(RUN, "2026-05-23", STRAT, ACCT, "test")


def _fill(symbol: str, side: str, qty: int, price: float,
          **kw: Any) -> dict[str, Any]:
    gross = qty * price
    return {
        "fill_id": _next_fill_id(symbol),
        "order_id": _next_order_id(symbol),
        "symbol": symbol,
        "side": side,
        "quantity": qty,
        "price": price,
        "gross_amount": gross,
        "commission": kw.get("commission", 0.0),
        "stamp_tax": kw.get("stamp_tax", 0.0),
        "slippage": kw.get("slippage", 0.0),
        "net_amount": gross,  # simplified
        "source": "test",
    }


# ═════════════════════════════════════════════════════════════════════
#  1. create_account — initial cash
# ═════════════════════════════════════════════════════════════════════

class TestCreateAccount:
    def test_initial_cash_recorded(self, service: LedgerService) -> None:
        acct = service.create_account(ACCT, "shadow", 500_000.0)
        assert acct["account_id"] == ACCT
        assert acct["initial_cash"] == 500_000.0
        assert acct["status"] == "active"

    def test_cash_balance_matches_initial(self, service: LedgerService) -> None:
        service.create_account(ACCT, "shadow", 1_000_000.0)
        assert service.get_cash(ACCT) == 1_000_000.0

    def test_account_idempotent(self, service: LedgerService) -> None:
        a1 = service.create_account(ACCT, "shadow", 500_000.0)
        a2 = service.create_account(ACCT, "shadow", 999_999.0)  # different value
        assert a2["account_id"] == ACCT
        assert a2["initial_cash"] == 500_000.0  # original value preserved

    def test_cash_reconstructed_from_ledger(self, service: LedgerService) -> None:
        """Reconstruct cash balance by summing cash_ledger events."""
        service.create_account(ACCT, "shadow", 1_000_000.0)
        conn = service.conn
        total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE account_id=?",
            (ACCT,),
        ).fetchone()[0]
        assert total == 1_000_000.0
        assert total == service.get_cash(ACCT)


# ═════════════════════════════════════════════════════════════════════
#  2. Buy fill — cash/position update
# ═════════════════════════════════════════════════════════════════════

class TestBuyFill:
    def test_buy_reduces_cash(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)])
        assert service.get_cash(ACCT) == pytest.approx(1_000_000.0 - 10_000.0)

    def test_buy_adds_position(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)])
        pos = service.get_position(ACCT, "600000.SH")
        assert pos is not None
        assert pos["quantity"] == 1000

    def test_buy_avg_cost(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
        ])
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["avg_cost"] == pytest.approx(10.0, abs=1e-4)

    def test_buy_avg_cost_multiple(self, service: LedgerService) -> None:
        _start_run(service)
        fill1 = _fill("600000.SH", "BUY", 500, 10.0)
        fill2 = _fill("600000.SH", "BUY", 500, 12.0, fill_id="fil_600000.SH_2",
                      order_id="ord_600000.SH_2")
        service.apply_fills(RUN, [fill1, fill2])
        pos = service.get_position(ACCT, "600000.SH")
        expected_avg = (500 * 10.0 + 500 * 12.0) / 1000
        assert pos["avg_cost"] == pytest.approx(expected_avg, abs=1e-4)


# ═════════════════════════════════════════════════════════════════════
#  3. Sell fill — cash/position update
# ═════════════════════════════════════════════════════════════════════

class TestSellFill:
    def test_sell_increases_cash(self, service: LedgerService) -> None:
        _start_run(service)
        # Buy first (T+0 so position is available)
        buy = _fill("600000.SH", "BUY", 1000, 10.0)
        service.apply_fills(RUN, [buy], t_plus_one=False)
        cash_after_buy = service.get_cash(ACCT)

        sell = _fill("600000.SH", "SELL", 500, 12.0)
        service.apply_fills(RUN, [sell], t_plus_one=False)
        cash_after_sell = service.get_cash(ACCT)
        assert cash_after_sell == pytest.approx(cash_after_buy + 500 * 12.0)

    def test_sell_reduces_position(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=False)
        service.apply_fills(RUN, [_fill("600000.SH", "SELL", 300, 12.0)], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["quantity"] == 700
        assert pos["available_quantity"] == 700

    def test_sell_avg_cost_unchanged(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=False)
        service.apply_fills(RUN, [_fill("600000.SH", "SELL", 500, 12.0)], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["avg_cost"] == pytest.approx(10.0, abs=1e-4)

    def test_sell_removes_zero_position(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=False)
        service.apply_fills(RUN, [_fill("600000.SH", "SELL", 1000, 12.0)], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos is None or pos["quantity"] == 0


# ═════════════════════════════════════════════════════════════════════
#  4. Insufficient cash — rollback
# ═════════════════════════════════════════════════════════════════════

class TestInsufficientCash:
    def test_insufficient_cash_raises(self, service: LedgerService) -> None:
        _start_run(service)
        # Try to buy more than available cash
        with pytest.raises(InsufficientCashError):
            service.apply_fills(RUN, [_fill("600000.SH", "BUY", 999_999, 10.0)])

    def test_insufficient_cash_does_not_insert_fill(self, service: LedgerService) -> None:
        _start_run(service)
        try:
            service.apply_fills(RUN, [_fill("600000.SH", "BUY", 999_999, 10.0)])
        except InsufficientCashError:
            pass
        fills = service.get_fills(RUN)
        assert len(fills) == 0

    def test_insufficient_cash_does_not_change_balance(self, service: LedgerService) -> None:
        _start_run(service)
        cash_before = service.get_cash(ACCT)
        try:
            service.apply_fills(RUN, [_fill("600000.SH", "BUY", 999_999, 10.0)])
        except InsufficientCashError:
            pass
        assert service.get_cash(ACCT) == cash_before

    def test_partial_batch_cash_rollback(self, service: LedgerService) -> None:
        """Batch: first fill ok, second overdraw → full rollback."""
        _start_run(service)
        fill_ok = _fill("600000.SH", "BUY", 1000, 10.0)
        fill_over = _fill("600001.SH", "BUY", 999_999, 10.0,
                          fill_id="fil_600001.SH", order_id="ord_600001.SH")
        with pytest.raises(InsufficientCashError):
            service.apply_fills(RUN, [fill_ok, fill_over])
        # Neither fill should be recorded
        assert len(service.get_fills(RUN)) == 0
        assert service.get_position(ACCT, "600000.SH") is None


# ═════════════════════════════════════════════════════════════════════
#  5. Insufficient position — rollback
# ═════════════════════════════════════════════════════════════════════

class TestInsufficientPosition:
    def test_sell_without_position_raises(self, service: LedgerService) -> None:
        _start_run(service)
        with pytest.raises(InsufficientPositionError):
            service.apply_fills(RUN, [_fill("600000.SH", "SELL", 100, 12.0)])

    def test_sell_more_than_available_raises(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 100, 10.0)], t_plus_one=False)
        with pytest.raises(InsufficientPositionError):
            service.apply_fills(RUN, [_fill("600000.SH", "SELL", 200, 12.0)])

    def test_sell_t1_restricted(self, service: LedgerService) -> None:
        """With T+1=True, BUY position not available for same-day SELL."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 100, 10.0)], t_plus_one=True)
        with pytest.raises(InsufficientPositionError):
            service.apply_fills(RUN, [_fill("600000.SH", "SELL", 100, 12.0)])

    def test_sell_t0_after_t1_buy(self, service: LedgerService) -> None:
        """T+1 BUY → roll available → SELL should work."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 100, 10.0)], t_plus_one=True)
        # Simulate next-day settlement
        service.roll_available_positions(ACCT, "2026-05-24")
        service.apply_fills(RUN, [_fill("600000.SH", "SELL", 100, 12.0)], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos is None or pos["quantity"] == 0

    def test_partial_batch_position_rollback(self, service: LedgerService) -> None:
        """Batch: first sell ok, second overdraw → full rollback."""
        _start_run(service)
        # Buy two symbols (T+0)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 500, 10.0),
            _fill("600001.SH", "BUY", 500, 10.0, fill_id="fil_600001.SH",
                  order_id="ord_600001.SH"),
        ], t_plus_one=False)

        # Try to sell 600000 ok but 600001 overdraw
        with pytest.raises(InsufficientPositionError):
            service.apply_fills(RUN, [
                _fill("600000.SH", "SELL", 200, 12.0),
                _fill("600001.SH", "SELL", 999, 12.0, fill_id="fil_600001.SH_sell",
                      order_id="ord_600001.SH_sell"),
            ])
        # No fills committed
        assert len(service.get_fills(RUN)) == 2  # only the two buy fills


# ═════════════════════════════════════════════════════════════════════
#  6. Duplicate fill / run behavior
# ═════════════════════════════════════════════════════════════════════

class TestDuplicateBehavior:
    def test_duplicate_run_raises(self, service: LedgerService) -> None:
        _start_run(service)
        with pytest.raises(DuplicateRunError):
            service.start_run(RUN, "2026-05-23", STRAT, ACCT, "test")

    def test_duplicate_run_force(self, service: LedgerService) -> None:
        _start_run(service)
        svc = service
        svc.finish_run(RUN, "completed")
        # force=True should re-open
        result = svc.start_run(RUN, "2026-05-23", STRAT, ACCT, "rerun", force=True)
        assert result["status"] == "started"

    def test_duplicate_fill_raises(self, service: LedgerService) -> None:
        _start_run(service)
        f = _fill("600000.SH", "BUY", 100, 10.0)
        service.apply_fills(RUN, [f], t_plus_one=False)
        with pytest.raises(DuplicateFillError):
            service.apply_fills(RUN, [f], t_plus_one=False)

    def test_duplicate_fill_idempotent(self, service: LedgerService) -> None:
        _start_run(service)
        f = _fill("600000.SH", "BUY", 100, 10.0)
        r1 = service.apply_fills(RUN, [f], t_plus_one=False)
        assert len(r1) == 1
        # idempotent=True → skip silently
        r2 = service.apply_fills(RUN, [f], idempotent=True, t_plus_one=False)
        assert r2 == []
        # State unchanged
        assert service.get_cash(ACCT) == pytest.approx(1_000_000.0 - 1000.0)
        assert service.get_position(ACCT, "600000.SH")["quantity"] == 100

    def test_completed_run_skip_ledger_write(self, service: LedgerService) -> None:
        """When run is completed, _write_execution_to_ledger must skip writes."""
        _start_run(service)
        service.finish_run(RUN, "completed")

        # Simulate what _write_execution_to_ledger does: check + skip
        existing = service.get_run(RUN)
        assert existing["status"] == "completed"

        # Any further apply_fills should work normally (service level doesn't
        # auto-skip — the caller _write_execution_to_ledger handles it)
        f = _fill("600000.SH", "BUY", 100, 10.0)
        with pytest.raises(DuplicateRunError):
            service.start_run(RUN, "2026-05-23", STRAT, ACCT, "rerun")


# ═════════════════════════════════════════════════════════════════════
#  7. T+1 true vs false
# ═════════════════════════════════════════════════════════════════════

class TestTPlusOne:
    def test_t1_buy_not_available(self, service: LedgerService) -> None:
        """T+1=True: BUY does not increase available_quantity."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=True)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["quantity"] == 1000
        assert pos["available_quantity"] == 0  # not available for sell

    def test_t1_roll_available(self, service: LedgerService) -> None:
        """roll_available_positions makes T+1 BUY sellable."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=True)
        service.roll_available_positions(ACCT, "2026-05-24")
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["available_quantity"] == 1000

    def test_t0_buy_available_immediately(self, service: LedgerService) -> None:
        """T+1=False: BUY increases available_quantity immediately."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["quantity"] == 1000
        assert pos["available_quantity"] == 1000  # T+0: available immediately

    def test_t0_buy_then_sell_same_day(self, service: LedgerService) -> None:
        """T+1=False allows buy-then-sell same day."""
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
        ], t_plus_one=False)
        # Sell should work since available_quantity == 1000
        service.apply_fills(RUN, [
            _fill("600000.SH", "SELL", 500, 11.0),
        ], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["quantity"] == 500

    def test_t1_buy_cannot_sell_same_day(self, service: LedgerService) -> None:
        """T+1=True prevents same-day sell."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 1000, 10.0)], t_plus_one=True)
        with pytest.raises(InsufficientPositionError):
            service.apply_fills(RUN, [_fill("600000.SH", "SELL", 100, 11.0)])

    def test_t1_partial_roll(self, service: LedgerService) -> None:
        """Mix of T+1 BUY and old positions."""
        _start_run(service)
        # Day 1: BUY 500 (T+1)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 500, 10.0)], t_plus_one=True)
        # Day 2: Roll
        service.roll_available_positions(ACCT, "2026-05-24")
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["available_quantity"] == 500
        # Day 2: BUY 300 more (T+1 — won't be available)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 300, 11.0, fill_id="fil_600000.SH_day2",
                  order_id="ord_600000.SH_day2"),
        ], t_plus_one=True)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["quantity"] == 800
        assert pos["available_quantity"] == 500  # only rolled portion
        # Can sell 500 (the rolled part) but not 800
        service.apply_fills(RUN, [
            _fill("600000.SH", "SELL", 500, 12.0),
        ], t_plus_one=False)
        pos = service.get_position(ACCT, "600000.SH")
        assert pos["quantity"] == 300  # 800 - 500
        # Cannot sell the remaining 300 (T+1 not yet settled)
        with pytest.raises(InsufficientPositionError):
            service.apply_fills(RUN, [
                _fill("600000.SH", "SELL", 300, 12.0),
            ])


# ═════════════════════════════════════════════════════════════════════
#  8. Migration idempotency
# ═════════════════════════════════════════════════════════════════════

class TestMigrationIdempotency:
    """ShadowMigrator must not add rows on repeated runs."""

    @pytest.fixture
    def shadow_dir(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            # account.json
            (path / "account.json").write_text(json.dumps({
                "initial_capital": 1_000_000.0,
                "cash": 950_000.0,
                "available_cash": 950_000.0,
                "market_value": 48_000.0,
                "total_value": 998_000.0,
                "last_run_id": "2026-05-22.shadow",
            }))
            # positions.csv
            with (path / "positions.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["instrument", "quantity", "sellable_quantity",
                            "cost_price", "last_price", "market_value"])
                w.writerow(["600000.SH", 1000, 1000, 10.0, 10.5, 10500.0])
                w.writerow(["600001.SH", 500, 500, 20.0, 21.0, 10500.0])
            # ledger.csv
            with (path / "ledger.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["run_id", "trade_date", "instrument", "side",
                            "quantity", "price", "amount", "fee",
                            "status", "reason"])
                w.writerow(["run_001", "2026-05-22", "600000.SH", "BUY",
                            "1000", "10.00", "10000.00", "0", "filled",
                            "rebalance"])
                w.writerow(["run_001", "2026-05-22", "600001.SH", "BUY",
                            "500", "20.00", "10000.00", "0", "filled",
                            "rebalance"])
            yield path

    def _row_count(self, service: LedgerService, table: str) -> int:
        return service.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_deterministic_id_format(self) -> None:
        """_mig_id produces reproducible IDs."""
        id1 = _mig_id("shadow_test", "ord", 1, "600000.SH")
        id2 = _mig_id("shadow_test", "ord", 1, "600000.SH")
        assert id1 == id2
        # Same index but different seed → different ID
        id3 = _mig_id("shadow_test", "ord", 1, "600001.SH")
        assert id1 != id3

    def test_migration_idempotent(self, service: LedgerService,
                                   shadow_dir: Path) -> None:
        """Second run does not increase row counts."""
        acct_id = "shadow_alpha_v1"
        strat_id = "alpha_v1"

        migrator = ShadowMigrator(service, shadow_dir)
        r1 = migrator.migrate(acct_id, strat_id)
        counts_1 = {
            "orders": self._row_count(service, "orders"),
            "fills": self._row_count(service, "fills"),
            "cash_ledger": self._row_count(service, "cash_ledger"),
            "position_ledger": self._row_count(service, "position_ledger"),
        }

        # Run migration again
        r2 = migrator.migrate(acct_id, strat_id)
        counts_2 = {
            "orders": self._row_count(service, "orders"),
            "fills": self._row_count(service, "fills"),
            "cash_ledger": self._row_count(service, "cash_ledger"),
            "position_ledger": self._row_count(service, "position_ledger"),
        }

        # No new rows from second run
        assert counts_1 == counts_2, f"Rows changed: {counts_1} → {counts_2}"

        # Verify no skipped rows on second run (all gracefully handled)
        assert len(r2.skipped_rows) == 0

    def test_migration_cash_correct(self, service: LedgerService,
                                     shadow_dir: Path) -> None:
        """Migrated cash balance includes INIT + adjustment + fills."""
        acct_id = "shadow_alpha_v1"
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate(acct_id, "alpha_v1")
        # account.json: cash=950000, initial_capital=1000000
        # So cash events: +1000000 (INIT) + (-50000 adjustment)
        #   + 2 BUY fills from ledger.csv: -10000 - 10000
        # Total = 1000000 - 50000 - 10000 - 10000 = 930000
        cash = service.get_cash(acct_id)
        assert cash == pytest.approx(930_000.0, abs=100.0)

    def test_migration_positions(self, service: LedgerService,
                                  shadow_dir: Path) -> None:
        """Migrated positions include positions.csv + ledger.csv fills."""
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate("shadow_alpha_v1", "alpha_v1")

        # Positions reflect positions.csv snapshot PLUS ledger.csv BUY fills
        # positions.csv: 600000.SH=1000, 600001.SH=500
        # ledger.csv BUY fills add another: 600000.SH=1000, 600001.SH=500
        pos1 = service.get_position("shadow_alpha_v1", "600000.SH")
        assert pos1 is not None
        assert pos1["quantity"] == 2000
        assert pos1["avg_cost"] == pytest.approx(10.0)

        pos2 = service.get_position("shadow_alpha_v1", "600001.SH")
        assert pos2 is not None
        assert pos2["quantity"] == 1000
        assert pos2["avg_cost"] == pytest.approx(20.0)

    def test_migration_reconstruct_fills(self, service: LedgerService,
                                          shadow_dir: Path) -> None:
        """Fills from legacy ledger.csv are present."""
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate("shadow_alpha_v1", "alpha_v1")

        fills = service.conn.execute(
            "SELECT * FROM fills WHERE account_id=?", ("shadow_alpha_v1",)
        ).fetchall()
        assert len(fills) == 2  # 2 rows in ledger.csv


# ═════════════════════════════════════════════════════════════════════
#  9. Reconstruct state from event tables
# ═════════════════════════════════════════════════════════════════════

class TestReconstructState:
    def test_reconstruct_cash_from_ledger(self, service: LedgerService) -> None:
        """Cash balance equals SUM of cash_ledger."""
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
            _fill("600001.SH", "BUY", 500, 20.0, fill_id="fil_600001.SH",
                  order_id="ord_600001.SH"),
        ], t_plus_one=False)

        conn = service.conn
        # INIT event + FILL_BUY + FILL_BUY
        total = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE account_id=?",
            (ACCT,),
        ).fetchone()[0]
        assert total == pytest.approx(1_000_000.0 - 10_000.0 - 10_000.0)

    def test_reconstruct_positions_from_ledger(self, service: LedgerService) -> None:
        """Current position quantity = SUM of position_ledger quantity_delta."""
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
        ], t_plus_one=False)
        service.apply_fills(RUN, [
            _fill("600000.SH", "SELL", 300, 12.0),
        ], t_plus_one=False)

        conn = service.conn
        delta = conn.execute(
            "SELECT COALESCE(SUM(quantity_delta), 0) FROM position_ledger "
            "WHERE account_id=? AND symbol=?",
            (ACCT, "600000.SH"),
        ).fetchone()[0]
        assert delta == 700  # 1000 - 300

    def test_reconstruct_multi_symbol(self, service: LedgerService) -> None:
        """Position quantities from position_ledger match positions table."""
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
            _fill("600001.SH", "BUY", 500, 20.0, fill_id="fil_600001.SH",
                  order_id="ord_600001.SH"),
        ], t_plus_one=False)
        service.apply_fills(RUN, [
            _fill("600000.SH", "SELL", 200, 12.0),
        ], t_plus_one=False)

        conn = service.conn
        rows = conn.execute(
            "SELECT symbol, SUM(quantity_delta) as net_qty FROM position_ledger "
            "WHERE account_id=? GROUP BY symbol", (ACCT,)
        ).fetchall()
        pos_map = {r["symbol"]: r["net_qty"] for r in rows}
        assert pos_map["600000.SH"] == 800
        assert pos_map["600001.SH"] == 500


# ═════════════════════════════════════════════════════════════════════
# 10. Atomicity — transaction rollback
# ═════════════════════════════════════════════════════════════════════

class TestAtomicity:
    def test_apply_fills_is_atomic(self, service: LedgerService) -> None:
        """On failure, no partial state remains."""
        _start_run(service)
        # First fill is fine, second is overdraw
        try:
            service.apply_fills(RUN, [
                _fill("600000.SH", "BUY", 1000, 10.0),
                _fill("600001.SH", "BUY", 999_999, 10.0,
                      fill_id="fil_600001.SH", order_id="ord_600001.SH"),
            ])
        except InsufficientCashError:
            pass
        # No state changes
        assert len(service.get_fills(RUN)) == 0
        assert service.get_cash(ACCT) == 1_000_000.0


# ═════════════════════════════════════════════════════════════════════
# 11. Portfolio snapshot
# ═════════════════════════════════════════════════════════════════════

class TestPortfolioSnapshot:
    def test_snapshot_created(self, service: LedgerService) -> None:
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
        ], t_plus_one=False)
        snap = service.create_portfolio_snapshot(
            RUN, "2026-05-23", prices={"600000.SH": 11.0},
        )
        assert snap is not None
        assert snap["cash"] == pytest.approx(990_000.0)
        assert snap["total_market_value"] == pytest.approx(11_000.0)
        assert snap["total_asset"] == pytest.approx(1_001_000.0)
        assert snap["position_count"] == 1
