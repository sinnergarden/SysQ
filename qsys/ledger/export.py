"""Export ledger data to CSV for debugging and external analysis."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from qsys.ledger.service import LedgerService


class LedgerExporter:
    """Export SQLite ledger tables to CSV files."""

    TABLES = [
        "orders", "fills", "cash_ledger", "position_ledger",
        "positions", "portfolio_snapshots", "strategy_runs",
    ]

    def __init__(self, service: LedgerService) -> None:
        self.service = service
        self.conn = service.conn

    def export_all(
        self,
        output_dir: str | Path,
        account_id: str | None = None,
        trade_date: str | None = None,
    ) -> list[Path]:
        """Export all ledger tables to CSV files.

        Parameters
        ----------
        output_dir : Path
            Directory to write CSV files.
        account_id : str, optional
            If set, filter by account_id.
        trade_date : str, optional
            If set, filter by trade_date.

        Returns
        -------
        list[Path]
            Paths of created files.
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []

        for table in self.TABLES:
            rows = self._query(table, account_id=account_id, trade_date=trade_date)
            path = out / f"{table}.csv"
            self._write_csv(path, rows)
            created.append(path)
            print(f"  ✅ {path.name}: {len(rows)} rows")

        return created

    def _query(
        self,
        table: str,
        account_id: str | None = None,
        trade_date: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []

        if account_id and "account_id" in self._columns(table):
            where.append("account_id = ?")
            params.append(account_id)
        if trade_date and "trade_date" in self._columns(table):
            where.append("trade_date = ?")
            params.append(trade_date)

        sql = f"SELECT * FROM {table}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at"

        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def _columns(self, table: str) -> set[str]:
        cursor = self.conn.execute(f"SELECT * FROM {table} LIMIT 0")
        return {desc[0] for desc in cursor.description}

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("")
            return
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
