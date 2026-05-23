#!/usr/bin/env python3
"""Migrate shadow/ CSV/JSON data to SQLite ledger.

Usage:
    python scripts/migrate_shadow_account_to_sqlite.py \\
        --account-id shadow_alpha_v1 \\
        --strategy-id alpha_v1_candidate_v3 \\
        [--shadow-dir shadow] \\
        [--db-path data/trade.db]

Idempotent: safe to run multiple times.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ledger.service import LedgerService
from qsys.ledger.migration import ShadowMigrator
from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate shadow CSV/JSON to SQLite")
    parser.add_argument("--account-id", default=ALPHA_V1_CANDIDATE.shadow_account_id, help="Account ID for ledger")
    parser.add_argument("--strategy-id", default=ALPHA_V1_CANDIDATE.strategy_id, help="Strategy ID")
    parser.add_argument("--shadow-dir", default=str(PROJECT_ROOT / "shadow"), help="Shadow data directory")
    parser.add_argument("--db-path", default=str(PROJECT_ROOT / "data" / "trade.db"), help="SQLite ledger path")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    service = LedgerService(str(db_path))
    migrator = ShadowMigrator(service, args.shadow_dir)
    report = migrator.migrate(args.account_id, args.strategy_id)
    report.print_summary()

    if report.skipped_rows:
        print(f"⚠ {len(report.skipped_rows)} rows skipped — check warnings above")
        sys.exit(1)

    print(f"✅ Migration complete. Ledger at: {db_path}")
    service.close()


if __name__ == "__main__":
    main()
