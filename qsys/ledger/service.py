"""LedgerService — the single public API for ledger operations.

All account state queries go through this service. Callers must never
read JSON/CSV files as source of truth.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qsys.ledger.db import create_connection
from qsys.ledger import repository as repo


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


def _new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


class DuplicateRunError(ValueError):
    """Raised when a run_id already exists and would be overwritten."""


class DuplicateFillError(ValueError):
    """Raised when a fill_id already exists."""


class InsufficientCashError(ValueError):
    """Raised when there is not enough cash to execute a fill."""


class InsufficientPositionError(ValueError):
    """Raised when trying to sell more than held."""


class LedgerService:
    """Ledger service — the single entry point for all ledger operations.

    Usage:
        service = LedgerService(db_path="data/trade.db")
        service.create_account("shadow_alpha_v1", "shadow", 1_000_000)
        service.start_run("2026-05-23.alpha_v1.shadow", ...)
        service.apply_fills(run_id, fills)
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = create_connection(self.db_path)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ── Account ─────────────────────────────────────────────────────

    def create_account(
        self,
        account_id: str,
        account_type: str,
        initial_cash: float,
        broker: str | None = None,
        base_currency: str = "CNY",
    ) -> dict[str, Any]:
        """Create a new account and record the initial cash event."""
        conn = self.conn
        existing = repo.get_account(conn, account_id)
        if existing:
            return existing

        with conn:  # transactional context manager
            acct = repo.insert_account(
                conn, account_id, account_type, initial_cash,
                broker=broker, base_currency=base_currency,
            )
            repo.insert_cash_event(
                conn,
                event_id=_new_id("cash_"),
                account_id=account_id,
                run_id=None,
                trade_date=datetime.now().strftime("%Y-%m-%d"),
                event_type="INIT",
                amount=initial_cash,
                note="Initial deposit",
            )
        return acct

    # ── Strategy Run ────────────────────────────────────────────────

    def start_run(
        self,
        run_id: str,
        trade_date: str,
        strategy_id: str,
        account_id: str,
        mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start a new strategy run. Raises DuplicateRunError if run_id exists."""
        conn = self.conn
        existing = repo.get_strategy_run(conn, run_id)
        if existing:
            raise DuplicateRunError(f"Run {run_id} already exists (status={existing['status']})")
        with conn:
            return repo.insert_strategy_run(
                conn, run_id, trade_date, strategy_id, account_id, mode,
                metadata=metadata,
            )

    def finish_run(self, run_id: str, status: str = "completed") -> None:
        repo.finish_strategy_run(self.conn, run_id, status=status)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return repo.get_strategy_run(self.conn, run_id)

    # ── Orders ──────────────────────────────────────────────────────

    def record_orders(self, run_id: str, orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Record orders for a run. Idempotent within a transaction."""
        run = self._require_run(run_id)
        for o in orders:
            o.setdefault("run_id", run_id)
            o.setdefault("account_id", run["account_id"])
            o.setdefault("strategy_id", run["strategy_id"])
            o.setdefault("trade_date", run["trade_date"])
        with self.conn:
            return repo.insert_orders(self.conn, orders)

    def get_orders(self, run_id: str) -> list[dict[str, Any]]:
        return repo.get_orders_by_run(self.conn, run_id)

    # ── Fills — the core transaction boundary ───────────────────────

    def apply_fills(
        self,
        run_id: str,
        fills: list[dict[str, Any]],
        open_prices: dict[str, float] | None = None,
        t_plus_one: bool = True,
    ) -> list[dict[str, Any]]:
        """Apply fills atomically.

        This is ONE transaction:
          1. Validate fills (no duplicates, sufficient cash/positions)
          2. Insert fills
          3. Update order status → filled
          4. Insert cash_ledger events
          5. Insert position_ledger events
          6. Upsert positions (current snapshot)
          7. On any failure → full ROLLBACK

        Parameters
        ----------
        run_id : str
            The strategy run these fills belong to.
        fills : list[dict]
            Each fill dict must have: order_id, symbol, side, quantity, price,
            commission, stamp_tax, slippage.
        open_prices : dict[str, float], optional
            Symbol → open price map for avg_cost calculation.
        t_plus_one : bool, default True
            If True, BUY fills do NOT increase available_quantity until
            next trading day (A-share T+1 settlement rule).
        """
        run = self._require_run(run_id)
        acct_id = run["account_id"]
        strat_id = run["strategy_id"]
        trade_date = run["trade_date"]

        conn = self.conn
        with conn:
            # 1. Validate: no duplicate fill_ids
            for f in fills:
                fid = f.get("fill_id", "")
                if fid:
                    existing = conn.execute(
                        "SELECT 1 FROM fills WHERE fill_id=?", (fid,)
                    ).fetchone()
                    if existing:
                        raise DuplicateFillError(f"Fill {fid} already exists")

            # 2. Compute cash impact and validate sufficiency
            current_cash = repo.get_cash_balance(conn, acct_id)
            net_cash_delta = 0.0
            for f in fills:
                side = f["side"].upper()
                qty = int(f["quantity"])
                price = float(f["price"])
                gross = qty * price
                comm = float(f.get("commission", 0.0))
                stamp = float(f.get("stamp_tax", 0.0))
                slip = float(f.get("slippage", 0.0))

                if side == "BUY":
                    net = gross + comm + slip
                    f["gross_amount"] = gross
                    f["net_amount"] = net
                    f["source"] = f.get("source", "simulation")
                    net_cash_delta -= net
                elif side == "SELL":
                    net = gross - comm - stamp - slip
                    f["gross_amount"] = gross
                    f["net_amount"] = net
                    f["source"] = f.get("source", "simulation")
                    net_cash_delta += net

                # Validate positions for SELL
                if side == "SELL":
                    pos = repo.get_position(conn, acct_id, f["symbol"])
                    avail = pos["available_quantity"] if pos else 0
                    if qty > avail:
                        raise InsufficientPositionError(
                            f"Cannot SELL {qty} of {f['symbol']}: "
                            f"only {avail} available (held={pos['quantity'] if pos else 0})"
                        )

            if current_cash + net_cash_delta < -0.01:
                raise InsufficientCashError(
                    f"Cash {current_cash:.2f} insufficient for net {net_cash_delta:.2f}"
                )

            # 3. Set run/account/strategy context on fills
            for f in fills:
                f.setdefault("fill_id", _new_id("fil_"))
                f.setdefault("run_id", run_id)
                f.setdefault("account_id", acct_id)
                f.setdefault("strategy_id", strat_id)
                f.setdefault("trade_date", trade_date)

            # 4. Insert fills
            inserted = repo.insert_fills(conn, fills)

            # 5. Update orders, cash ledger, position ledger, positions
            order_statuses: dict[str, int] = {}
            for f in fills:
                oid = f["order_id"]
                order_statuses[oid] = order_statuses.get(oid, 0) + 1

                side = f["side"].upper()
                qty = int(f["quantity"])
                price = float(f["price"])

                # ── cash_ledger ──
                cash_amount = -f["net_amount"] if side == "BUY" else f["net_amount"]
                repo.insert_cash_event(
                    conn,
                    event_id=_new_id("cash_"),
                    account_id=acct_id,
                    run_id=run_id,
                    trade_date=trade_date,
                    event_type=f"FILL_{side}",
                    amount=cash_amount,
                    related_order_id=oid,
                    related_fill_id=f["fill_id"],
                    note=f"{side} {qty} {f['symbol']} @ {price:.4f}",
                )

                # ── position_ledger + positions ──
                qty_delta = qty if side == "BUY" else -qty
                repo.insert_position_event(
                    conn,
                    event_id=_new_id("pos_"),
                    account_id=acct_id,
                    run_id=run_id,
                    trade_date=trade_date,
                    symbol=f["symbol"],
                    event_type=f"FILL_{side}",
                    quantity_delta=qty_delta,
                    price=price,
                    amount=f["net_amount"],
                    related_order_id=oid,
                    related_fill_id=f["fill_id"],
                    note=f"{side} {qty} {f['symbol']} @ {price:.4f}",
                )

                # ── upsert current position ──
                current_pos = repo.get_position(conn, acct_id, f["symbol"])
                old_qty = current_pos["quantity"] if current_pos else 0
                old_cost = current_pos["avg_cost"] if current_pos else 0.0
                old_avail = current_pos["available_quantity"] if current_pos else 0

                if side == "BUY":
                    new_qty = old_qty + qty
                    new_avail = old_avail  # T+1: not available today
                    new_cost = (
                        (old_cost * old_qty + price * qty) / new_qty
                        if new_qty > 0 else 0.0
                    )
                else:  # SELL
                    new_qty = old_qty - qty
                    new_avail = old_avail - qty
                    new_cost = old_cost  # avg_cost unchanged on SELL

                mv = new_qty * price if new_qty > 0 else None
                upnl = (price - new_cost) * new_qty if (new_qty > 0 and new_cost > 0) else None

                repo.upsert_position(
                    conn,
                    account_id=acct_id,
                    symbol=f["symbol"],
                    quantity=max(new_qty, 0),
                    available_quantity=max(new_avail, 0),
                    avg_cost=new_cost if new_qty > 0 else 0.0,
                    last_price=price,
                    market_value=round(mv, 4) if mv is not None else None,
                    unrealized_pnl=round(upnl, 4) if upnl is not None else None,
                )

            # 6. Mark orders as filled
            for oid in order_statuses:
                repo.update_order_status(conn, oid, "filled")

        return inserted

    # ── T+1 Settlement ──────────────────────────────────────────────

    def roll_available_positions(self, account_id: str, trade_date: str) -> None:
        """Make previous day's BUY positions available for SELL.

        A-share T+1 rule: stocks bought on day T can only be sold on day T+1.
        This should be called at the start of each trading day.
        """
        conn = self.conn
        with conn:
            rows = conn.execute(
                """SELECT symbol, quantity FROM positions
                   WHERE account_id=? AND quantity > 0""",
                (account_id,),
            ).fetchall()
            for r in rows:
                conn.execute(
                    """UPDATE positions SET available_quantity = quantity
                       WHERE account_id=? AND symbol=?""",
                    (account_id, r["symbol"]),
                )

    # ── Portfolio Snapshot ──────────────────────────────────────────

    def create_portfolio_snapshot(
        self,
        run_id: str,
        trade_date: str,
        prices: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Create a portfolio snapshot at current prices."""
        run = self._require_run(run_id)
        acct_id = run["account_id"]
        conn = self.conn
        with conn:
            cash = repo.get_cash_balance(conn, acct_id)
            positions = repo.get_positions(conn, acct_id)
            total_mv = 0.0
            pos_count = 0
            for p in positions:
                sym = p["symbol"]
                qty = p["quantity"]
                if qty <= 0:
                    continue
                price = prices.get(sym, p["last_price"]) if prices else p["last_price"]
                if price is None or price <= 0:
                    price = 0.0
                mv = qty * price
                total_mv += mv
                pos_count += 1
                # Update last_price and market_value
                conn.execute(
                    "UPDATE positions SET last_price=?, market_value=? WHERE account_id=? AND symbol=?",
                    (price, round(mv, 4), acct_id, sym),
                )

            total_asset = cash + total_mv

            # Compute daily PnL from previous snapshot
            prev = repo.get_snapshot_by_account_date(conn, acct_id, _prev_date(trade_date))
            daily_pnl = total_asset - prev["total_asset"] if prev else None
            daily_return = (daily_pnl / prev["total_asset"]) if prev and prev["total_asset"] else None

            snapshot = repo.insert_snapshot(conn, {
                "snapshot_id": _new_id("snp_"),
                "account_id": acct_id,
                "run_id": run_id,
                "trade_date": trade_date,
                "cash": round(cash, 4),
                "total_market_value": round(total_mv, 4),
                "total_asset": round(total_asset, 4),
                "daily_pnl": round(daily_pnl, 4) if daily_pnl is not None else None,
                "daily_return": round(daily_return, 6) if daily_return is not None else None,
                "position_count": pos_count,
            })
        return snapshot

    def get_portfolio_snapshot(self, account_id: str, trade_date: str) -> dict[str, Any] | None:
        return repo.get_snapshot_by_account_date(self.conn, account_id, trade_date)

    # ── Cash & Positions queries ────────────────────────────────────

    def get_cash(self, account_id: str) -> float:
        """Get current cash for an account. Only by account_id."""
        return repo.get_cash_balance(self.conn, account_id)

    def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        """Get current non-zero positions for an account. Only by account_id."""
        return repo.get_positions(self.conn, account_id)

    def get_position(self, account_id: str, symbol: str) -> dict[str, Any] | None:
        return repo.get_position(self.conn, account_id, symbol)

    # ── Account info ────────────────────────────────────────────────

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        return repo.get_account(self.conn, account_id)

    def get_initial_cash(self, account_id: str) -> float:
        acct = repo.get_account(self.conn, account_id)
        return acct["initial_cash"] if acct else 0.0

    # ── Run queries ────────────────────────────────────────────────

    def get_fills(self, run_id: str) -> list[dict[str, Any]]:
        return repo.get_fills_by_run(self.conn, run_id)

    def get_cash_events(self, run_id: str) -> list[dict[str, Any]]:
        return repo.get_cash_events_by_run(self.conn, run_id)

    def get_position_events(self, run_id: str) -> list[dict[str, Any]]:
        return repo.get_position_events_by_run(self.conn, run_id)

    # ── Internal ────────────────────────────────────────────────────

    def _require_run(self, run_id: str) -> dict[str, Any]:
        run = repo.get_strategy_run(self.conn, run_id)
        if run is None:
            raise ValueError(f"Run {run_id} not found. Call start_run() first.")
        return run


def _prev_date(trade_date: str) -> str:
    """Simple previous date — not aware of trading calendar."""
    from datetime import datetime, timedelta
    dt = datetime.strptime(trade_date, "%Y-%m-%d")
    prev = dt - timedelta(days=1)
    return prev.strftime("%Y-%m-%d")
