#!/usr/bin/env python3
"""Reconcile broker positions against local ledger after market close.

Usage:
    python scripts/deprecated/reconcile_broker_state.py \\
        --run-id shadow_2026-04-25_090807 \\
        --broker-url http://localhost:8080 \\
        [--trade-date 2026-04-25]

This is the fourth script in the Phase 1 live chain. It:
1. Fetches current account/positions from the broker
2. Compares against local TradeLedger state
3. Outputs a diff report
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from qsys.broker.miniqmt import MiniQMTAdapter
from qsys.execution.service import ExecutionService
from qsys.trader.database import TradeLedger
from qsys.utils.logger import log


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile broker vs local ledger")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--broker-url", default="http://localhost:8080", help="MiniQMT server URL")
    parser.add_argument("--trade-date", default=None, help="Trading date")
    parser.add_argument("--strategy-id", default="alpha_v1", help="Strategy identifier")
    parser.add_argument("--ledger-path", default=None, help="TradeLedger SQLite path")
    args = parser.parse_args()

    adapter = MiniQMTAdapter(base_url=args.broker_url)
    ledger_path = args.ledger_path or f"data/trade_{args.trade_date or 'latest'}.db"
    ledger = TradeLedger(db_path=ledger_path)
    service = ExecutionService(adapter, ledger, strategy_id=args.strategy_id)

    log.info("Reconciling broker state run=%s", args.run_id)
    result = service.reconcile_run(run_id=args.run_id)

    print(f"\n=== Broker Reconciliation ===")
    print(f"  Status: {result.get('status', 'unknown')}")

    if result.get("status") == "reconcile_failed":
        print(f"  Error: {result.get('error')}")
        return

    print(f"\n  Broker account:")
    print(f"    Cash:            {result.get('broker_cash', '?'):>12.2f}")
    print(f"    Position count:  {result.get('broker_position_count', '?'):>12d}")

    print(f"\n  Local ledger (run={args.run_id}):")
    print(f"    Intent count:    {result.get('local_intent_count', '?'):>12d}")
    print(f"    Status counts:   {result.get('local_status_counts', {})}")

    print(f"\n  Note: {result.get('note', '')}")
    print()

    # Write artifact
    artifact = {
        "run_id": args.run_id,
        "trade_date": args.trade_date or "",
        "strategy_id": args.strategy_id,
        "account_name": getattr(adapter, "account_name", ""),
        "status": result.get("status", "unknown"),
        "broker_cash": result.get("broker_cash"),
        "broker_position_count": result.get("broker_position_count"),
        "local_intent_count": result.get("local_intent_count"),
        "local_status_counts": result.get("local_status_counts"),
        "error": result.get("error"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact_dir = Path("data/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"reconcile_{args.run_id}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote reconcile artifact: %s", artifact_path)


if __name__ == "__main__":
    main()
