"""Low-level data access for the ledger system.

Each function operates on a single table. Transaction management is the
caller's responsibility (see LedgerService in service.py).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ── accounts ────────────────────────────────────────────────────────

def insert_account(
    conn: sqlite3.Connection,
    account_id: str,
    account_type: str,
    initial_cash: float,
    broker: str | None = None,
    base_currency: str = "CNY",
) -> dict[str, Any]:
    now = _now()
    conn.execute(
        """INSERT INTO accounts (account_id, account_type, broker, base_currency,
           initial_cash, created_at, updated_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'active')""",
        (account_id, account_type, broker, base_currency, initial_cash, now, now),
    )
    return dict(conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone())


def get_account(conn: sqlite3.Connection, account_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM accounts WHERE account_id=?", (account_id,)).fetchone()
    return dict(row) if row else None


# ── strategy_runs ───────────────────────────────────────────────────

def insert_strategy_run(
    conn: sqlite3.Connection,
    run_id: str,
    trade_date: str,
    strategy_id: str,
    account_id: str,
    mode: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = _now()
    md = metadata or {}
    conn.execute(
        """INSERT INTO strategy_runs
           (run_id, trade_date, strategy_id, account_id, mode,
            config_hash, model_version, signal_version, data_cutoff, git_commit,
            status, started_at, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'started', ?, ?)""",
        (
            run_id, trade_date, strategy_id, account_id, mode,
            md.get("config_hash"), md.get("model_version"),
            md.get("signal_version"), md.get("data_cutoff"), md.get("git_commit"),
            now, md.get("notes"),
        ),
    )
    return dict(conn.execute("SELECT * FROM strategy_runs WHERE run_id=?", (run_id,)).fetchone())


def finish_strategy_run(conn: sqlite3.Connection, run_id: str, status: str = "completed") -> None:
    conn.execute(
        "UPDATE strategy_runs SET status=?, finished_at=? WHERE run_id=?",
        (status, _now(), run_id),
    )


def get_strategy_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM strategy_runs WHERE run_id=?", (run_id,)).fetchone()
    return dict(row) if row else None


# ── orders ──────────────────────────────────────────────────────────

def insert_orders(conn: sqlite3.Connection, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now()
    rows = []
    for o in orders:
        order_id = o.get("order_id", _new_id("ord_"))
        conn.execute(
            """INSERT INTO orders
               (order_id, run_id, account_id, strategy_id, trade_date,
                symbol, side, order_type, quantity, limit_price,
                target_weight, status, reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id, o["run_id"], o["account_id"], o["strategy_id"], o["trade_date"],
                o["symbol"], o["side"], o.get("order_type", "market"),
                o["quantity"], o.get("limit_price"),
                o.get("target_weight"), o.get("status", "pending"),
                o.get("reason"), now, now,
            ),
        )
        rows.append(dict(conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()))
    return rows


def insert_orders_ignore_conflicts(
    conn: sqlite3.Connection, orders: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert orders, skipping any that already exist (by order_id)."""
    now = _now()
    rows = []
    for o in orders:
        order_id = o.get("order_id", _new_id("ord_"))
        if conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone():
            rows.append(dict(
                conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
            ))
            continue
        conn.execute(
            """INSERT OR IGNORE INTO orders
               (order_id, run_id, account_id, strategy_id, trade_date,
                symbol, side, order_type, quantity, limit_price,
                target_weight, status, reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                order_id, o["run_id"], o["account_id"], o["strategy_id"], o["trade_date"],
                o["symbol"], o["side"], o.get("order_type", "market"),
                o["quantity"], o.get("limit_price"),
                o.get("target_weight"), o.get("status", "pending"),
                o.get("reason"), now, now,
            ),
        )
        rows.append(dict(conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()))
    return rows


def get_orders_by_run(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM orders WHERE run_id=?", (run_id,)).fetchall()]


def update_order_status(conn: sqlite3.Connection, order_id: str, status: str) -> None:
    conn.execute(
        "UPDATE orders SET status=?, updated_at=? WHERE order_id=?",
        (status, _now(), order_id),
    )


# ── fills ───────────────────────────────────────────────────────────

def insert_fills(conn: sqlite3.Connection, fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now()
    rows = []
    for f in fills:
        fill_id = f.get("fill_id", _new_id("fil_"))
        conn.execute(
            """INSERT INTO fills
               (fill_id, order_id, run_id, account_id, strategy_id, trade_date,
                symbol, side, quantity, price, gross_amount,
                commission, stamp_tax, slippage, net_amount,
                fill_time, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fill_id, f["order_id"], f["run_id"], f["account_id"],
                f["strategy_id"], f["trade_date"],
                f["symbol"], f["side"], f["quantity"], f["price"],
                f["gross_amount"], f.get("commission", 0.0),
                f.get("stamp_tax", 0.0), f.get("slippage", 0.0),
                f["net_amount"], now, f.get("source", "simulation"),
            ),
        )
        rows.append(dict(conn.execute("SELECT * FROM fills WHERE fill_id=?", (fill_id,)).fetchone()))
    return rows


def insert_fills_ignore_conflicts(
    conn: sqlite3.Connection, fills: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Insert fills, skipping any that already exist (by fill_id), idempotent."""
    now = _now()
    rows = []
    for f in fills:
        fill_id = f.get("fill_id", _new_id("fil_"))
        if conn.execute("SELECT 1 FROM fills WHERE fill_id=?", (fill_id,)).fetchone():
            rows.append(dict(
                conn.execute("SELECT * FROM fills WHERE fill_id=?", (fill_id,)).fetchone()
            ))
            continue
        conn.execute(
            """INSERT INTO fills
               (fill_id, order_id, run_id, account_id, strategy_id, trade_date,
                symbol, side, quantity, price, gross_amount,
                commission, stamp_tax, slippage, net_amount,
                fill_time, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                fill_id, f["order_id"], f["run_id"], f["account_id"],
                f["strategy_id"], f["trade_date"],
                f["symbol"], f["side"], f["quantity"], f["price"],
                f["gross_amount"], f.get("commission", 0.0),
                f.get("stamp_tax", 0.0), f.get("slippage", 0.0),
                f["net_amount"], now, f.get("source", "simulation"),
            ),
        )
        rows.append(dict(conn.execute("SELECT * FROM fills WHERE fill_id=?", (fill_id,)).fetchone()))
    return rows


def get_fills_by_run(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM fills WHERE run_id=?", (run_id,)).fetchall()]


# ── cash_ledger ─────────────────────────────────────────────────────

def insert_cash_event(
    conn: sqlite3.Connection,
    event_id: str,
    account_id: str,
    run_id: str | None,
    trade_date: str,
    event_type: str,
    amount: float,
    related_order_id: str | None = None,
    related_fill_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    now = _now()
    conn.execute(
        """INSERT INTO cash_ledger
           (cash_event_id, account_id, run_id, trade_date, event_type,
            amount, related_order_id, related_fill_id, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, account_id, run_id, trade_date, event_type,
         amount, related_order_id, related_fill_id, note, now),
    )
    return dict(
        conn.execute("SELECT * FROM cash_ledger WHERE cash_event_id=?", (event_id,)).fetchone()
    )


def get_cash_events_by_run(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM cash_ledger WHERE run_id=?", (run_id,)
    ).fetchall()]


# ── position_ledger ─────────────────────────────────────────────────

def insert_position_event(
    conn: sqlite3.Connection,
    event_id: str,
    account_id: str,
    run_id: str | None,
    trade_date: str,
    symbol: str,
    event_type: str,
    quantity_delta: int,
    price: float | None = None,
    amount: float | None = None,
    related_order_id: str | None = None,
    related_fill_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    now = _now()
    conn.execute(
        """INSERT INTO position_ledger
           (position_event_id, account_id, run_id, trade_date, symbol,
            event_type, quantity_delta, price, amount,
            related_order_id, related_fill_id, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, account_id, run_id, trade_date, symbol,
         event_type, quantity_delta, price, amount,
         related_order_id, related_fill_id, note, now),
    )
    return dict(
        conn.execute("SELECT * FROM position_ledger WHERE position_event_id=?",
                      (event_id,)).fetchone()
    )


def get_position_events_by_run(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM position_ledger WHERE run_id=?", (run_id,)
    ).fetchall()]


# ── positions (current snapshot) ────────────────────────────────────

def upsert_position(
    conn: sqlite3.Connection,
    account_id: str,
    symbol: str,
    quantity: int,
    available_quantity: int,
    avg_cost: float,
    last_price: float | None = None,
    market_value: float | None = None,
    unrealized_pnl: float | None = None,
) -> dict[str, Any]:
    now = _now()
    conn.execute(
        """INSERT INTO positions
           (account_id, symbol, quantity, available_quantity, avg_cost,
            last_price, market_value, unrealized_pnl, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(account_id, symbol) DO UPDATE SET
               quantity          = excluded.quantity,
               available_quantity = excluded.available_quantity,
               avg_cost          = excluded.avg_cost,
               last_price        = excluded.last_price,
               market_value      = excluded.market_value,
               unrealized_pnl    = excluded.unrealized_pnl,
               updated_at        = excluded.updated_at""",
        (account_id, symbol, quantity, available_quantity, avg_cost,
         last_price, market_value, unrealized_pnl, now),
    )
    return dict(
        conn.execute("SELECT * FROM positions WHERE account_id=? AND symbol=?",
                      (account_id, symbol)).fetchone()
    )


def get_positions(conn: sqlite3.Connection, account_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM positions WHERE account_id=? AND quantity != 0 ORDER BY symbol",
        (account_id,),
    ).fetchall()]


def get_position(conn: sqlite3.Connection, account_id: str, symbol: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM positions WHERE account_id=? AND symbol=?",
        (account_id, symbol),
    ).fetchone()
    return dict(row) if row else None


def delete_positions(conn: sqlite3.Connection, account_id: str) -> None:
    conn.execute("DELETE FROM positions WHERE account_id=?", (account_id,))


# ── portfolio_snapshots ─────────────────────────────────────────────

def upsert_snapshot(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Insert or update a portfolio snapshot by (account_id, trade_date, run_id).

    Idempotent: same (account_id, trade_date, run_id) updates in place.
    """
    now = _now()
    sid = snapshot.get("snapshot_id", _new_id("snp_"))

    existing = conn.execute(
        "SELECT snapshot_id FROM portfolio_snapshots "
        "WHERE account_id=? AND trade_date=? AND run_id=?",
        (snapshot["account_id"], snapshot["trade_date"], snapshot.get("run_id")),
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE portfolio_snapshots SET
               cash=?, total_market_value=?, total_asset=?,
               daily_pnl=?, daily_return=?, turnover=?, position_count=?,
               created_at=COALESCE(created_at, ?)
               WHERE snapshot_id=?""",
            (
                snapshot["cash"], snapshot["total_market_value"],
                snapshot["total_asset"],
                snapshot.get("daily_pnl"), snapshot.get("daily_return"),
                snapshot.get("turnover"), snapshot.get("position_count"),
                now,
                existing["snapshot_id"],
            ),
        )
        sid = existing["snapshot_id"]
    else:
        conn.execute(
            """INSERT INTO portfolio_snapshots
               (snapshot_id, account_id, run_id, trade_date,
                cash, total_market_value, total_asset,
                daily_pnl, daily_return, turnover, position_count,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sid, snapshot["account_id"], snapshot.get("run_id"),
                snapshot["trade_date"],
                snapshot["cash"], snapshot["total_market_value"],
                snapshot["total_asset"],
                snapshot.get("daily_pnl"), snapshot.get("daily_return"),
                snapshot.get("turnover"), snapshot.get("position_count"),
                now,
            ),
        )

    return dict(
        conn.execute("SELECT * FROM portfolio_snapshots WHERE snapshot_id=?", (sid,)).fetchone()
    )


def get_snapshot_by_account_date(
    conn: sqlite3.Connection, account_id: str, trade_date: str,
) -> dict[str, Any] | None:
    rows = conn.execute(
        """SELECT * FROM portfolio_snapshots
           WHERE account_id=? AND trade_date=?
           ORDER BY created_at DESC LIMIT 1""",
        (account_id, trade_date),
    ).fetchall()
    return dict(rows[0]) if rows else None


# ── aggregated queries ──────────────────────────────────────────────

def get_cash_balance(conn: sqlite3.Connection, account_id: str) -> float:
    """Compute current cash from cash_ledger SUM."""
    row = conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM cash_ledger WHERE account_id=?",
        (account_id,),
    ).fetchone()
    return float(row[0])


# ── new query helpers ────────────────────────────────────────────


def get_latest_snapshot(
    conn: sqlite3.Connection, account_id: str,
) -> dict[str, Any] | None:
    """Get the most recent portfolio snapshot for an account."""
    row = conn.execute(
        "SELECT * FROM portfolio_snapshots WHERE account_id=? "
        "ORDER BY trade_date DESC, created_at DESC LIMIT 1",
        (account_id,),
    ).fetchone()
    return dict(row) if row else None


def get_latest_trade_date(
    conn: sqlite3.Connection, account_id: str,
) -> str | None:
    """Get the latest trade_date across fills and snapshots."""
    row = conn.execute(
        "SELECT MAX(trade_date) FROM ("
        "  SELECT trade_date FROM fills WHERE account_id=? "
        "  UNION ALL "
        "  SELECT trade_date FROM portfolio_snapshots WHERE account_id=?"
        ")",
        (account_id, account_id),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def get_account_summary(
    conn: sqlite3.Connection, account_id: str,
) -> dict[str, Any] | None:
    """Return a summary dict for an account: cash, positions, last trade."""
    acct = get_account(conn, account_id)
    if not acct:
        return None

    cash = get_cash_balance(conn, account_id)
    positions = get_positions(conn, account_id)
    total_mv = sum(float(p.get("market_value", 0) or 0) for p in positions)
    pos_count = len(positions)
    last_trade = get_latest_trade_date(conn, account_id)

    return {
        "account_id": account_id,
        "cash": cash,
        "market_value": total_mv,
        "total_value": cash + total_mv,
        "position_count": pos_count,
        "last_trade_date": last_trade,
    }


def list_accounts(
    conn: sqlite3.Connection, account_type: str | None = None,
) -> list[dict[str, Any]]:
    """List all accounts, optionally filtered by account_type."""
    if account_type:
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM accounts WHERE account_type=? ORDER BY account_id",
                (account_type,),
            ).fetchall()
        ]
    return [
        dict(r) for r in conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
    ]


def get_ledger_summary(
    conn: sqlite3.Connection, account_id: str | None = None,
) -> dict[str, Any]:
    """Return aggregate ledger stats (order/fill counts, sums)."""
    account_filter = "WHERE account_id=?" if account_id else ""
    params = (account_id,) if account_id else ()

    order_count = conn.execute(
        f"SELECT COUNT(*) FROM orders {account_filter}", params,
    ).fetchone()[0]
    fill_count = conn.execute(
        f"SELECT COUNT(*) FROM fills {account_filter}", params,
    ).fetchone()[0]
    fill_volume = conn.execute(
        f"SELECT COALESCE(SUM(gross_amount), 0) FROM fills {account_filter}", params,
    ).fetchone()[0]
    cash_events = conn.execute(
        f"SELECT COUNT(*) FROM cash_ledger {account_filter}", params,
    ).fetchone()[0]

    return {
        "account_id": account_id,
        "order_count": order_count,
        "fill_count": fill_count,
        "fill_volume": float(fill_volume),
        "cash_event_count": cash_events,
    }
