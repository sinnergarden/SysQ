"""
SQLite schema for the SysQ ledger system.

All shadow/paper account state is stored in SQLite tables.
CSV/JSON export is provided for debugging but never read as source of truth.
"""

from __future__ import annotations

import sqlite3


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id      TEXT PRIMARY KEY,
    account_type    TEXT NOT NULL DEFAULT 'shadow',
    broker          TEXT,
    base_currency   TEXT NOT NULL DEFAULT 'CNY',
    initial_cash    REAL NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS strategy_runs (
    run_id          TEXT PRIMARY KEY,
    trade_date      TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    mode            TEXT NOT NULL,
    config_hash     TEXT,
    model_version   TEXT,
    signal_version  TEXT,
    data_cutoff     TEXT,
    git_commit      TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT,
    notes           TEXT,
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    order_type      TEXT NOT NULL DEFAULT 'market',
    quantity        INTEGER NOT NULL,
    limit_price     REAL,
    target_weight   REAL,
    status          TEXT NOT NULL DEFAULT 'pending',
    reason          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES strategy_runs(run_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id         TEXT PRIMARY KEY,
    order_id        TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    account_id      TEXT NOT NULL,
    strategy_id     TEXT NOT NULL,
    trade_date      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    price           REAL NOT NULL,
    gross_amount    REAL NOT NULL,
    commission      REAL NOT NULL DEFAULT 0,
    stamp_tax       REAL NOT NULL DEFAULT 0,
    slippage        REAL NOT NULL DEFAULT 0,
    net_amount      REAL NOT NULL,
    fill_time       TEXT NOT NULL DEFAULT (datetime('now')),
    source          TEXT NOT NULL DEFAULT 'simulation',
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (run_id) REFERENCES strategy_runs(run_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS cash_ledger (
    cash_event_id       TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    run_id              TEXT,
    trade_date          TEXT NOT NULL,
    event_type          TEXT NOT NULL,
    amount              REAL NOT NULL,
    currency            TEXT DEFAULT 'CNY',
    related_order_id    TEXT,
    related_fill_id     TEXT,
    note                TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS position_ledger (
    position_event_id       TEXT PRIMARY KEY,
    account_id              TEXT NOT NULL,
    run_id                  TEXT,
    trade_date              TEXT NOT NULL,
    symbol                  TEXT NOT NULL,
    event_type              TEXT NOT NULL,
    quantity_delta          INTEGER NOT NULL,
    price                   REAL,
    amount                  REAL,
    related_order_id        TEXT,
    related_fill_id         TEXT,
    note                    TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS positions (
    account_id          TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    quantity            INTEGER NOT NULL DEFAULT 0,
    available_quantity  INTEGER NOT NULL DEFAULT 0,
    avg_cost            REAL NOT NULL DEFAULT 0,
    last_price          REAL,
    market_value        REAL,
    unrealized_pnl      REAL,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account_id, symbol),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    snapshot_id         TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    run_id              TEXT,
    trade_date          TEXT NOT NULL,
    cash                REAL NOT NULL,
    total_market_value  REAL NOT NULL,
    total_asset         REAL NOT NULL,
    daily_pnl           REAL,
    daily_return        REAL,
    turnover            REAL,
    position_count      INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't exist. Idempotent."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def get_schema_version(conn: sqlite3.Connection) -> str | None:
    cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
    row = cursor.fetchone()
    return row[0] if row else None


def record_migration(conn: sqlite3.Connection, version: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)",
        (version,),
    )
    conn.commit()
