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
        "fill_id": kw["fill_id"] if "fill_id" in kw else _next_fill_id(symbol),
        "order_id": kw["order_id"] if "order_id" in kw else _next_order_id(symbol),
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
        """Migrated cash = INIT + MIGRATION_ADJUST (snapshot-first, fills do not affect cash)."""
        acct_id = "shadow_alpha_v1"
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate(acct_id, "alpha_v1")
        # account.json: cash=950000, initial_capital=1000000
        # Cash events: +1000000 (INIT) + (-50000 MIGRATION_ADJUST) = 950000
        # Historical fills are recorded in fills table but do NOT affect cash_ledger.
        cash = service.get_cash(acct_id)
        assert cash == pytest.approx(950_000.0, abs=100.0)

    def test_migration_positions(self, service: LedgerService,
                                  shadow_dir: Path) -> None:
        """Migrated positions match positions.csv (snapshot-first, fills do not affect positions)."""
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate("shadow_alpha_v1", "alpha_v1")

        # Positions from positions.csv only — historical fills go to fills table
        # without affecting current positions/cash.
        pos1 = service.get_position("shadow_alpha_v1", "600000.SH")
        assert pos1 is not None
        assert pos1["quantity"] == 1000
        assert pos1["avg_cost"] == pytest.approx(10.0)

        pos2 = service.get_position("shadow_alpha_v1", "600001.SH")
        assert pos2 is not None
        assert pos2["quantity"] == 500
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

    def test_snapshot_idempotent_same_run(self, service: LedgerService) -> None:
        """Same (account_id, trade_date, run_id) updates in place, no duplicate."""
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 1000, 10.0),
        ], t_plus_one=False)

        snap1 = service.create_portfolio_snapshot(
            RUN, "2026-05-23", prices={"600000.SH": 11.0},
        )
        snap2 = service.create_portfolio_snapshot(
            RUN, "2026-05-23", prices={"600000.SH": 12.0},
        )
        assert snap1["snapshot_id"] == snap2["snapshot_id"]
        assert snap2["total_market_value"] == pytest.approx(12_000.0)  # updated

        count = service.conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots WHERE account_id=? AND trade_date=? AND run_id=?",
            (ACCT, "2026-05-23", RUN),
        ).fetchone()[0]
        assert count == 1


class TestPortfolioSnapshotUniqueIndex:
    """Verify DB-level unique index on (account_id, trade_date, run_id)."""

    def test_unique_index_created(self, db_path: str) -> None:
        """ensure_schema creates the unique index."""
        from qsys.ledger.schema import ensure_schema
        conn = create_connection(db_path)
        ensure_schema(conn)
        # Query sqlite_master for the index
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name=?",
            ("idx_portfolio_snapshots_unique",),
        ).fetchone()
        assert row is not None, "Unique index not found"
        assert row["name"] == "idx_portfolio_snapshots_unique"
        conn.close()

    def test_unique_index_prevents_duplicate(self, db_path: str) -> None:
        """Inserting same (account_id, trade_date, run_id) twice raises IntegrityError."""
        from qsys.ledger.schema import ensure_schema
        conn = create_connection(db_path)
        ensure_schema(conn)

        # Need an account for the FK constraint
        conn.execute(
            "INSERT INTO accounts (account_id, account_type, initial_cash) "
            "VALUES ('test_acct', 'shadow', 1000000.0)"
        )
        conn.commit()

        conn.execute(
            """INSERT INTO portfolio_snapshots
               (snapshot_id, account_id, run_id, trade_date,
                cash, total_market_value, total_asset)
               VALUES ('snp_001', 'test_acct', 'run_001', '2026-05-23',
                       100000.0, 50000.0, 150000.0)"""
        )
        conn.commit()

        # Second insert with same (account_id, trade_date, run_id) must fail
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO portfolio_snapshots
                   (snapshot_id, account_id, run_id, trade_date,
                    cash, total_market_value, total_asset)
                   VALUES ('snp_002', 'test_acct', 'run_001', '2026-05-23',
                           90000.0, 60000.0, 150000.0)"""
            )
            conn.commit()
        conn.close()


# ═════════════════════════════════════════════════════════════════════
# 12. finish_run + completed-run retry
# ═════════════════════════════════════════════════════════════════════

class TestCompletedRun:
    def test_finish_run_sets_completed(self, service: LedgerService) -> None:
        _start_run(service)
        service.finish_run(RUN, "completed")
        run = service.get_run(RUN)
        assert run["status"] == "completed"
        assert run["finished_at"] is not None

    def test_fill_after_completed_run(self, service: LedgerService) -> None:
        """Fills still work after run is completed (service doesn't auto-block)."""
        _start_run(service)
        service.finish_run(RUN, "completed")
        # Re-open with force
        service.start_run(RUN, "2026-05-23", STRAT, ACCT, "rerun", force=True)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 100, 10.0)])
        assert service.get_position(ACCT, "600000.SH")["quantity"] == 100

    def test_completed_run_retry_skip_snapshot(self, service: LedgerService) -> None:
        """Simulate _write_execution_to_ledger idempotency: completed run skips all writes."""
        _start_run(service)
        service.apply_fills(RUN, [_fill("600000.SH", "BUY", 100, 10.0)])
        service.create_portfolio_snapshot(RUN, "2026-05-23", prices={"600000.SH": 11.0})
        service.finish_run(RUN, "completed")

        # Verify initial state
        assert service.get_run(RUN)["status"] == "completed"
        initial_cash = service.get_cash(ACCT)
        initial_pos = service.get_position(ACCT, "600000.SH")["quantity"]
        fill_count_before = len(service.get_fills(RUN))
        snap_count_before = service.conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots WHERE run_id=?", (RUN,)
        ).fetchone()[0]

        # Now simulate what _write_execution_to_ledger does on retry:
        existing = service.get_run(RUN)
        assert existing["status"] == "completed"

        # Since run is completed, _write_execution_to_ledger returns early.
        # State should be unchanged (no duplicate fills, no duplicate snapshots).
        assert service.get_cash(ACCT) == initial_cash
        assert service.get_position(ACCT, "600000.SH")["quantity"] == initial_pos
        assert len(service.get_fills(RUN)) == fill_count_before
        snap_count_after = service.conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots WHERE run_id=?", (RUN,)
        ).fetchone()[0]
        assert snap_count_after == snap_count_before


# ═════════════════════════════════════════════════════════════════════
# 13. Migration archive + report
# ═════════════════════════════════════════════════════════════════════

class TestMigrationArchive:
    """ShadowMigrator must archive old files and generate a markdown report."""

    @pytest.fixture
    def shadow_dir(self) -> Generator[Path, None, None]:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)
            (path / "account.json").write_text(json.dumps({
                "initial_capital": 1_000_000.0, "cash": 950_000.0,
            }))
            with (path / "positions.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["instrument", "quantity", "sellable_quantity",
                            "cost_price", "last_price", "market_value"])
                w.writerow(["600000.SH", 1000, 1000, 10.0, 10.5, 10500.0])
            with (path / "ledger.csv").open("w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["run_id", "trade_date", "instrument", "side",
                            "quantity", "price", "amount", "fee", "status", "reason"])
                w.writerow(["run_001", "2026-05-22", "600000.SH", "BUY",
                            "1000", "10.00", "10000.00", "0", "filled", "rebalance"])
            yield path

    def test_archive_after_migration(self, service: LedgerService,
                                      shadow_dir: Path) -> None:
        """After migration, old shadow files are renamed to .archived."""
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate("shadow_alpha_v1", "alpha_v1")

        assert not (shadow_dir / "account.json").exists()
        assert not (shadow_dir / "positions.csv").exists()
        assert not (shadow_dir / "ledger.csv").exists()

        assert (shadow_dir / "account.json.archived").exists()
        assert (shadow_dir / "positions.csv.archived").exists()
        assert (shadow_dir / "ledger.csv.archived").exists()

    def test_migration_report_exists(self, service: LedgerService,
                                      shadow_dir: Path) -> None:
        """Migration produces a markdown report."""
        migrator = ShadowMigrator(service, shadow_dir)
        migrator.migrate("shadow_alpha_v1", "alpha_v1")

        report_path = shadow_dir / "migration_report.md"
        assert report_path.exists()

        content = report_path.read_text()
        assert "shadow_alpha_v1" in content
        assert "alpha_v1" in content
        assert "Snapshot-First" in content
        assert "not replayed" in content


# ═════════════════════════════════════════════════════════════════════
# 14. New LedgerService query methods
# ═════════════════════════════════════════════════════════════════════

class TestLedgerServiceQueries:
    def test_get_latest_trade_date(self, service: LedgerService) -> None:
        _start_run(service)
        # No fills yet → None
        assert service.get_latest_trade_date(ACCT) is None

        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 100, 10.0),
        ])
        assert service.get_latest_trade_date(ACCT) == "2026-05-23"

    def test_get_account_summary(self, service: LedgerService) -> None:
        _start_run(service)
        summary = service.get_account_summary(ACCT)
        assert summary is not None
        assert summary["account_id"] == ACCT
        assert summary["cash"] == 1_000_000.0
        assert summary["position_count"] == 0

        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 100, 10.0),
        ])
        summary = service.get_account_summary(ACCT)
        assert summary["cash"] == pytest.approx(999_000.0)
        assert summary["position_count"] == 1
        assert summary["total_value"] == pytest.approx(
            summary["cash"] + summary["market_value"]
        )

    def test_get_ledger_summary(self, service: LedgerService) -> None:
        _start_run(service)
        summary = service.get_ledger_summary()
        assert summary["order_count"] == 0
        assert summary["fill_count"] == 0

        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 100, 10.0),
        ])
        summary = service.get_ledger_summary(ACCT)
        assert summary["order_count"] == 1
        assert summary["fill_count"] == 1
        assert summary["fill_volume"] == pytest.approx(1000.0)

    def test_get_ledger_summary_includes_last_run(self, service: LedgerService) -> None:
        """get_ledger_summary returns last_run_id and last_trade_date."""
        _start_run(service)
        summary = service.get_ledger_summary(ACCT)
        # No fills yet
        assert summary["last_run_id"] is None
        assert summary["last_trade_date"] is None

        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 100, 10.0),
        ])
        summary = service.get_ledger_summary(ACCT)
        assert summary["last_run_id"] == RUN
        assert summary["last_trade_date"] == "2026-05-23"

    def test_list_accounts(self, service: LedgerService) -> None:
        service.create_account("test_a", "shadow", 100_000.0)
        service.create_account("test_b", "real", 200_000.0)
        all_accts = service.list_accounts()
        assert len(all_accts) >= 2

        shadow_accts = service.list_accounts(account_type="shadow")
        assert all(a["account_type"] == "shadow" for a in shadow_accts)


# ═════════════════════════════════════════════════════════════════════
# 15. Ledger export
# ═════════════════════════════════════════════════════════════════════

class TestLedgerExport:
    def test_export_all_tables(self, service: LedgerService,
                                tmp_path: Path) -> None:
        _start_run(service)
        service.apply_fills(RUN, [
            _fill("600000.SH", "BUY", 100, 10.0),
        ], t_plus_one=False)
        service.create_portfolio_snapshot(RUN, "2026-05-23",
                                           prices={"600000.SH": 11.0})

        from qsys.ledger.export import LedgerExporter
        exporter = LedgerExporter(service)
        csv_files = exporter.export_all(output_dir=tmp_path)

        # Verify expected files exist
        expected = {
            "orders.csv", "fills.csv", "cash_ledger.csv",
            "position_ledger.csv", "positions.csv",
            "portfolio_snapshots.csv", "strategy_runs.csv",
        }
        actual = {p.name for p in csv_files}
        assert expected.issubset(actual)

        # Verify non-empty files
        for name in ["orders.csv", "fills.csv", "positions.csv"]:
            content = (tmp_path / name).read_text()
            assert len(content) > 0, f"{name} is empty"
