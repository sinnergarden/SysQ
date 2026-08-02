"""F05 regression: TradeLedger must not silently co-exist on the LedgerService
SOT (data/trade.db) with a conflicting orders/fills schema."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from qsys.trader.database import TradeLedger

_LEDGER_ORDERS = """
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, account_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL, trade_date TEXT NOT NULL, symbol TEXT NOT NULL,
    side TEXT NOT NULL, order_type TEXT NOT NULL DEFAULT 'market',
    quantity INTEGER NOT NULL, limit_price REAL, target_weight REAL,
    status TEXT NOT NULL DEFAULT 'pending', reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def test_trade_ledger_default_is_not_ledger_sot() -> None:
    """F05: the default execution DB must not be data/trade.db (LedgerService SOT)."""
    import inspect

    default = inspect.signature(TradeLedger.__init__).parameters["db_path"].default
    assert str(default) != "data/trade.db"
    assert "execution" in str(default)  # dedicated execution DB


def test_schema_collision_raises() -> None:
    """F05: opening a DB whose orders table has LedgerService's schema raises."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        con = sqlite3.connect(tmp)
        con.execute(_LEDGER_ORDERS)
        con.commit()
        con.close()
        with pytest.raises(RuntimeError, match="schema collision"):
            TradeLedger(tmp)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def test_fresh_execution_db_ok() -> None:
    """F05: a fresh isolated execution DB initializes normally."""
    tmp = tempfile.mktemp(suffix=".db")
    try:
        ledger = TradeLedger(tmp)
        assert ledger.db_path.exists()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
