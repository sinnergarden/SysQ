from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TradeLedger:
    """Minimal SQLite ledger for daily production runs."""

    def __init__(self, db_path: str | Path = "data/trade.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    run_id TEXT PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    recipe_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    error TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    sellable_quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    avg_cost REAL NOT NULL,
                    market_value REAL NOT NULL,
                    captured_at TEXT NOT NULL,
                    note TEXT DEFAULT ''
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    note TEXT DEFAULT ''
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS fills (
                    fill_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    trading_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL,
                    tax REAL NOT NULL,
                    filled_at TEXT NOT NULL,
                    note TEXT DEFAULT ''
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_metrics (
                    run_id TEXT PRIMARY KEY,
                    trading_date TEXT NOT NULL,
                    daily_return REAL NOT NULL,
                    turnover REAL NOT NULL,
                    fill_rate REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    details_json TEXT DEFAULT '{}'
                )
                """
            )
            connection.commit()

    def start_pipeline_run(
        self,
        *,
        run_id: str,
        trading_date: str,
        recipe_version: str,
        status: str = "running",
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT OR IGNORE INTO pipeline_runs (
                    run_id,
                    trading_date,
                    recipe_version,
                    status,
                    started_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, trading_date, recipe_version, status, now, now),
            )
            cursor.execute(
                """
                UPDATE pipeline_runs
                SET trading_date = ?, recipe_version = ?, status = ?, updated_at = ?, ended_at = NULL, error = NULL
                WHERE run_id = ?
                """,
                (trading_date, recipe_version, status, now, run_id),
            )
            connection.commit()

    def finish_pipeline_run(self, *, run_id: str, status: str, error: str | None = None) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE pipeline_runs
                SET status = ?, ended_at = ?, updated_at = ?, error = ?
                WHERE run_id = ?
                """,
                (status, now, now, error, run_id),
            )
            connection.commit()

    def replace_position_snapshot(
        self,
        *,
        run_id: str,
        trading_date: str,
        snapshot_type: str,
        positions: Iterable[dict[str, Any]],
        account_name: str = "real",
    ) -> None:
        captured_at = utc_now()
        normalized_rows: list[tuple[Any, ...]] = []
        for item in positions:
            quantity = int(item.get("quantity", item.get("total_amount", item.get("amount", 0))) or 0)
            sellable_quantity = int(item.get("sellable_quantity", item.get("sellable_amount", quantity)) or 0)
            price = float(item.get("price", item.get("last_price", 0.0)) or 0.0)
            avg_cost = float(item.get("avg_cost", item.get("cost_basis", price)) or 0.0)
            market_value = float(item.get("market_value", quantity * price) or 0.0)
            normalized_rows.append(
                (
                    run_id,
                    trading_date,
                    snapshot_type,
                    str(item.get("account_name") or account_name),
                    str(item.get("symbol") or ""),
                    quantity,
                    sellable_quantity,
                    price,
                    avg_cost,
                    market_value,
                    captured_at,
                    str(item.get("note") or ""),
                )
            )

        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM position_snapshots
                WHERE run_id = ? AND trading_date = ? AND snapshot_type = ? AND account_name = ?
                """,
                (run_id, trading_date, snapshot_type, account_name),
            )
            if normalized_rows:
                connection.executemany(
                    """
                    INSERT INTO position_snapshots (
                        run_id,
                        trading_date,
                        snapshot_type,
                        account_name,
                        symbol,
                        quantity,
                        sellable_quantity,
                        price,
                        avg_cost,
                        market_value,
                        captured_at,
                        note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    normalized_rows,
                )
            connection.commit()

    def upsert_order(
        self,
        *,
        order_id: str,
        run_id: str,
        trading_date: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        status: str,
        account_name: str = "real",
        note: str = "",
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO orders (
                    order_id,
                    run_id,
                    trading_date,
                    account_name,
                    symbol,
                    side,
                    quantity,
                    price,
                    status,
                    created_at,
                    updated_at,
                    note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    run_id = excluded.run_id,
                    trading_date = excluded.trading_date,
                    account_name = excluded.account_name,
                    symbol = excluded.symbol,
                    side = excluded.side,
                    quantity = excluded.quantity,
                    price = excluded.price,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    note = excluded.note
                """,
                (
                    order_id,
                    run_id,
                    trading_date,
                    account_name,
                    symbol,
                    side,
                    int(quantity),
                    float(price),
                    status,
                    now,
                    now,
                    note,
                ),
            )
            connection.commit()

    def insert_fill(
        self,
        *,
        fill_id: str,
        order_id: str,
        run_id: str,
        trading_date: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        fee: float = 0.0,
        tax: float = 0.0,
        filled_at: str | None = None,
        note: str = "",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO fills (
                    fill_id,
                    order_id,
                    run_id,
                    trading_date,
                    symbol,
                    side,
                    quantity,
                    price,
                    fee,
                    tax,
                    filled_at,
                    note
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill_id,
                    order_id,
                    run_id,
                    trading_date,
                    symbol,
                    side,
                    int(quantity),
                    float(price),
                    float(fee),
                    float(tax),
                    filled_at or utc_now(),
                    note,
                ),
            )
            connection.commit()

    def upsert_daily_metrics(
        self,
        *,
        run_id: str,
        trading_date: str,
        daily_return: float,
        turnover: float,
        fill_rate: float,
        details: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO daily_metrics (
                    run_id,
                    trading_date,
                    daily_return,
                    turnover,
                    fill_rate,
                    created_at,
                    updated_at,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    trading_date = excluded.trading_date,
                    daily_return = excluded.daily_return,
                    turnover = excluded.turnover,
                    fill_rate = excluded.fill_rate,
                    updated_at = excluded.updated_at,
                    details_json = excluded.details_json
                """,
                (
                    run_id,
                    trading_date,
                    float(daily_return),
                    float(turnover),
                    float(fill_rate),
                    now,
                    now,
                    json.dumps(details or {}, ensure_ascii=False),
                ),
            )
            connection.commit()

    def count_orders(self, *, run_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count_value FROM orders WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return int(row["count_value"] if row else 0)

    def count_fills(self, *, run_id: str, trading_date: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count_value FROM fills WHERE run_id = ?"
        params: list[Any] = [run_id]
        if trading_date is not None:
            query += " AND trading_date = ?"
            params.append(trading_date)
        with self.connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return int(row["count_value"] if row else 0)
