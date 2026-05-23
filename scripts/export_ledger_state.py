#!/usr/bin/env python3
"""Export ledger state to CSV files for inspection and debugging.

Usage:
    python scripts/export_ledger_state.py \\
        --db-path data/trade.db \\
        --account-id shadow_alpha_v1 \\
        --trade-date 2026-05-22 \\
        --output-dir /tmp/ledger_export
"""
from __future__ import annotations

import argparse
from pathlib import Path

from qsys.ledger.export import LedgerExporter
from qsys.ledger.service import LedgerService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export ledger state to CSV files"
    )
    parser.add_argument("--db-path", default=None,
                        help="Path to SQLite ledger DB (default: data/trade.db)")
    parser.add_argument("--account-id", default=None,
                        help="Filter by account_id")
    parser.add_argument("--trade-date", default=None,
                        help="Filter by trade_date (YYYY-MM-DD)")
    parser.add_argument("--output-dir", required=True,
                        help="Output directory for CSV files")
    args = parser.parse_args()

    db_path = args.db_path or str(
        Path(__file__).resolve().parent.parent / "data" / "trade.db"
    )
    output_dir = Path(args.output_dir)

    if not Path(db_path).exists():
        print(f"❌ DB not found: {db_path}")
        raise SystemExit(1)

    service = LedgerService(db_path)
    try:
        exporter = LedgerExporter(service)
        csv_files = exporter.export_all(
            output_dir=output_dir,
            account_id=args.account_id,
            trade_date=args.trade_date,
        )
        print(f"\n✅ Exported {len(csv_files)} CSV files to {output_dir}")
        for path in csv_files:
            print(f"   - {path.name}")
    finally:
        service.close()


if __name__ == "__main__":
    main()
