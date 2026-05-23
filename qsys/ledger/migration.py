"""Migrate legacy shadow JSON/CSV files into SQLite ledger."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from qsys.ledger.service import LedgerService


def _mig_id(account_id: str, kind: str, row_index: int, seed: str = "") -> str:
    """Deterministic ID for migration: mig_{kind}_{md5 prefix}."""
    raw = f"{account_id}.{kind}.{row_index}.{seed}"
    return f"mig_{kind}_{hashlib.md5(raw.encode()).hexdigest()[:16]}"


class MigrationReport:
    def __init__(self) -> None:
        self.account_id: str = ""
        self.strategy_id: str = ""
        self.total_cash: float = 0.0
        self.position_count: int = 0
        self.ledger_rows_parsed: int = 0
        self.orders_migrated: int = 0
        self.fills_migrated: int = 0
        self.skipped_rows: list[dict[str, Any]] = []
        self.warnings: list[str] = []

    def _write_markdown(self, path: Path) -> None:
        """Write a detailed markdown report to path."""
        lines = [
            f"# Migration Report — {self.account_id}",
            "",
            f"- **Account ID:** {self.account_id}",
            f"- **Strategy ID:** {self.strategy_id}",
            f"- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "## Mode: Snapshot-First",
            "",
            "This migration imports legacy shadow files using a **snapshot-first** approach:",
            "",
            "- **Cash** is seeded from `account.json` (initial_capital + adjustment)",
            "- **Positions** are seeded from `positions.csv` — these become the current position snapshots",
            "- **Orders + Fills** from `ledger.csv` are imported into historical tables (`orders`, `fills`) **only**",
            "- Legacy fills are **not replayed** into `cash_ledger` or `position_ledger`",
            "- Cash and position event tables reflect only the seeded state (INIT, MIGRATION_ADJUST, MIGRATION_INIT)",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| Initial Cash | ¥{self.total_cash:,.2f} |",
            f"| Position Count | {self.position_count} |",
            f"| Ledger Rows Parsed | {self.ledger_rows_parsed} |",
            f"| Orders Migrated | {self.orders_migrated} |",
            f"| Fills Migrated | {self.fills_migrated} |",
        ]
        if self.skipped_rows:
            lines.extend([
                "",
                "## Skipped Rows",
                "",
            ])
            for s in self.skipped_rows:
                lines.append(f"- Row {s.get('row', '?')}: {s.get('reason', 'unknown')}")
        if self.warnings:
            lines.extend([
                "",
                "## Warnings",
                "",
            ])
            for w in self.warnings:
                lines.append(f"- {w}")
        lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")

    def print_summary(self) -> None:
        print(f"\n{'=' * 50}")
        print(f"Migration Report — {self.account_id}")
        print(f"{'=' * 50}")
        print(f"  Account ID:     {self.account_id}")
        print(f"  Strategy ID:    {self.strategy_id}")
        print(f"  Initial Cash:   ¥{self.total_cash:,.2f}")
        print(f"  Positions:      {self.position_count}")
        print(f"  Ledger rows:    {self.ledger_rows_parsed}")
        print(f"  Orders:         {self.orders_migrated}")
        print(f"  Fills:          {self.fills_migrated}")
        if self.skipped_rows:
            print(f"\n  ⚠ Skipped: {len(self.skipped_rows)} rows (see below)")
            for s in self.skipped_rows[:5]:
                print(f"     - {s}")
            if len(self.skipped_rows) > 5:
                print(f"     ... and {len(self.skipped_rows) - 5} more")
        if self.warnings:
            print(f"\n  ⚠ Warnings:")
            for w in self.warnings:
                print(f"     - {w}")
        print(f"{'=' * 50}\n")


class ShadowMigrator:
    """Migrate legacy shadow/ files to SQLite ledger.

    Reads shadow/account.json, shadow/positions.csv, shadow/ledger.csv
    and writes into the SQLite ledger via LedgerService.
    """

    def __init__(self, service: LedgerService, shadow_dir: str | Path) -> None:
        self.service = service
        self.shadow_dir = Path(shadow_dir)

    def migrate(
        self,
        account_id: str,
        strategy_id: str,
    ) -> MigrationReport:
        report = MigrationReport()
        report.account_id = account_id
        report.strategy_id = strategy_id

        # ── 1. Migrate account.json ──
        account_path = self.shadow_dir / "account.json"
        if not account_path.exists():
            report.warnings.append(f"account.json not found at {account_path} — using defaults")
            initial_cash = 1_000_000.0
        else:
            with account_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            initial_cash = float(data.get("initial_capital", data.get("cash", 1_000_000.0)))
            report.total_cash = float(data.get("cash", initial_cash))

        self.service.create_account(account_id, "shadow", initial_cash)
        print(f"  ✅ Account {account_id} (initial ¥{initial_cash:,.2f})")

        # If account.json had residual cash beyond initial deposit,
        # record it as adjustment (skip if already recorded — idempotent)
        if report.total_cash != initial_cash and report.total_cash > 0:
            adjustment = report.total_cash - initial_cash
            conn = self.service.conn
            from qsys.ledger import repository as repo
            event_id = _mig_id(account_id, "cash_adj", 0, "MIGRATION_ADJUST")
            if conn.execute("SELECT 1 FROM cash_ledger WHERE cash_event_id=?", (event_id,)).fetchone():
                report.warnings.append(f"Cash adjustment ¥{adjustment:+.2f} already recorded — skipping")
            else:
                with conn:
                    repo.insert_cash_event(
                        conn,
                        event_id=event_id,
                        account_id=account_id,
                        run_id=None,
                        trade_date=datetime.now().strftime("%Y-%m-%d"),
                        event_type="MIGRATION_ADJUST",
                        amount=adjustment,
                        note="Cash adjustment from legacy account.json",
                    )
                report.warnings.append(
                    f"Cash adjusted by ¥{adjustment:+.2f} (legacy cash ≠ initial_capital)"
                )

        # ── 2. Migrate positions.csv ──
        positions_path = self.shadow_dir / "positions.csv"
        if positions_path.exists():
            conn = self.service.conn
            from qsys.ledger import repository as repo
            with open(positions_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = row.get("instrument") or row.get("symbol", "")
                    qty = int(float(row.get("quantity", 0)))
                    cost = float(row.get("cost_price", row.get("avg_cost", 0)))
                    avail = int(float(row.get("sellable_quantity", row.get("available_quantity", qty))))

                    if qty == 0:
                        continue

                    with conn:
                        repo.upsert_position(
                            conn,
                            account_id=account_id,
                            symbol=symbol,
                            quantity=qty,
                            available_quantity=avail,
                            avg_cost=cost,
                            last_price=float(row.get("last_price", cost)),
                            market_value=float(row.get("market_value", qty * cost)),
                        )
                        pos_event_id = _mig_id(account_id, "pos_init", 0, symbol)
                        if not conn.execute("SELECT 1 FROM position_ledger WHERE position_event_id=?", (pos_event_id,)).fetchone():
                            repo.insert_position_event(
                                conn,
                                event_id=pos_event_id,
                                account_id=account_id,
                                run_id=None,
                                trade_date=datetime.now().strftime("%Y-%m-%d"),
                                symbol=symbol,
                                event_type="MIGRATION_INIT",
                                quantity_delta=qty,
                                price=cost,
                                amount=qty * cost,
                                note="Initial position from legacy migration",
                            )
                    report.position_count += 1
            print(f"  ✅ Positions: {report.position_count} symbols")

        # ── 3. Migrate ledger.csv ──
        ledger_path = self.shadow_dir / "ledger.csv"
        if ledger_path.exists():
            orders_batch: list[dict[str, Any]] = []
            fills_batch: list[dict[str, Any]] = []
            run_id_to_legacy_run: dict[str, dict] = {}

            with open(ledger_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    report.ledger_rows_parsed += 1
                    try:
                        run_id_legacy = row.get("run_id", "")
                        trade_date = row.get("trade_date", "")
                        symbol = row.get("instrument", "")
                        side_raw = row.get("side", "").upper()
                        qty = int(float(row.get("quantity", 0)))
                        price = float(row.get("price", 0))
                        amount = float(row.get("amount", 0))
                        fee = float(row.get("fee", 0))
                        status = row.get("status", "")
                        reason = row.get("reason", "")

                        if not symbol or qty == 0:
                            report.skipped_rows.append(
                                {"row": report.ledger_rows_parsed, "reason": "empty symbol or quantity"}
                            )
                            continue

                        side = "BUY" if side_raw == "BUY" else "SELL"
                        gross = qty * price
                        net = gross + fee if side == "BUY" else gross - fee

                        # Normalize legacy run_id to ledger run_id
                        leg_run_id = run_id_legacy or f"{trade_date}.legacy.shadow" if trade_date else f"{trade_date}.legacy"

                        # Create a strategy run entry if not yet created
                        if leg_run_id not in run_id_to_legacy_run:
                            ledger_run_id = f"{trade_date}.{strategy_id}.shadow"
                            if not self.service.get_run(ledger_run_id):
                                self.service.start_run(
                                    ledger_run_id, trade_date, strategy_id,
                                    account_id, "legacy_migration",
                                )
                            run_id_to_legacy_run[leg_run_id] = {
                                "ledger_run_id": ledger_run_id,
                                "trade_date": trade_date,
                            }
                        else:
                            ledger_run_id = run_id_to_legacy_run[leg_run_id]["ledger_run_id"]

                        order_id = _mig_id(account_id, "ord", report.ledger_rows_parsed, symbol)
                        fill_id = _mig_id(account_id, "fil", report.ledger_rows_parsed, symbol)

                        order = {
                            "order_id": order_id,
                            "run_id": ledger_run_id,
                            "account_id": account_id,
                            "strategy_id": strategy_id,
                            "trade_date": trade_date,
                            "symbol": symbol,
                            "side": side,
                            "order_type": "market",
                            "quantity": qty,
                            "limit_price": price,
                            "status": "filled" if status in ("filled", "成交") else status,
                            "reason": reason or "legacy_migration",
                        }
                        orders_batch.append(order)

                        fill = {
                            "fill_id": fill_id,
                            "order_id": order_id,
                            "run_id": ledger_run_id,
                            "account_id": account_id,
                            "strategy_id": strategy_id,
                            "trade_date": trade_date,
                            "symbol": symbol,
                            "side": side,
                            "quantity": qty,
                            "price": price,
                            "gross_amount": gross,
                            "commission": fee,
                            "stamp_tax": 0.0,
                            "slippage": 0.0,
                            "net_amount": net,
                            "source": "legacy_migration",
                        }
                        fills_batch.append(fill)

                    except Exception as e:
                        report.skipped_rows.append(
                            {"row": report.ledger_rows_parsed, "reason": str(e)}
                        )

            # Apply in batches by run (idempotent, snapshot-first)
            # Orders/fills go to historical tables only — no cash/position changes.
            # Positions come from positions.csv, cash from account.json.
            conn = self.service.conn
            with conn:
                from qsys.ledger import repository as repo
                if orders_batch:
                    repo.insert_orders_ignore_conflicts(conn, orders_batch)
                    report.orders_migrated = len(orders_batch)
                if fills_batch:
                    repo.insert_fills_ignore_conflicts(conn, fills_batch)
                    report.fills_migrated = len(fills_batch)

            if fills_batch or orders_batch:
                print(f"  ✅ Orders: {report.orders_migrated}, Fills: {report.fills_migrated}")

        # ── 4. Archive old shadow files ──
        archived_suffix = ".archived"
        for fname in ("account.json", "positions.csv", "ledger.csv"):
            src = self.shadow_dir / fname
            if src.exists():
                dst = src.with_suffix(src.suffix + archived_suffix)
                # Remove existing archive if present
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
                print(f"  📦 Archived: {src.name} → {src.name}{archived_suffix}")

        # ── 5. Generate migration report ──
        report_path = self.shadow_dir / "migration_report.md"
        report._write_markdown(report_path)
        print(f"  📄 Report: {report_path}")

        return report
