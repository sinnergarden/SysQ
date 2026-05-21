#!/usr/bin/env python3
"""Poll the MiniQMT broker for order status updates and record fills.

Usage:
    python scripts/live/poll_broker_orders.py \\
        --run-id shadow_2026-04-25_090807 \\
        --broker-url http://localhost:8080 \\
        [--trade-date 2026-04-25]

This is the third script in the Phase 1 live chain. It:
1. Fetches current order statuses from the broker
2. Updates the TradeLedger with any status transitions
3. Records any new fills/trades
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from qsys.broker.miniqmt import MiniQMTAdapter
from qsys.execution.service import ExecutionService
from qsys.trader.database import TradeLedger
from qsys.utils.logger import log


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll broker for order updates")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--broker-url", default="http://localhost:8080", help="MiniQMT server URL")
    parser.add_argument("--trade-date", default=None, help="Trading date filter")
    parser.add_argument("--strategy-id", default="alpha_v1", help="Strategy identifier")
    parser.add_argument("--ledger-path", default=None, help="TradeLedger SQLite path")
    args = parser.parse_args()

    adapter = MiniQMTAdapter(base_url=args.broker_url)
    ledger_path = args.ledger_path or f"data/trade_{args.trade_date or 'latest'}.db"
    ledger = TradeLedger(db_path=ledger_path)
    service = ExecutionService(adapter, ledger, strategy_id=args.strategy_id)

    filters = {}
    if args.trade_date:
        filters["trade_date"] = args.trade_date

    log.info("Polling broker for order updates run=%s filters=%s", args.run_id, filters)
    result = service.poll_updates(run_id=args.run_id, filters=filters)

    print(f"\n=== Poll Result ===")
    print(f"  Status:      {result['status']}")
    print(f"  Transitions: {len(result.get('transitions', []))}")
    print(f"  Fills found: {result.get('fill_count', 0)}")

    for t in result.get("transitions", []):
        print(f"  {t['intent_id']:40s} {t.get('from', '?'):>12s} → {t.get('to', '?'):<12s}  broker={t.get('broker_order_id', '')}")

    print(f"\nStatus counts:")
    counts = ledger.count_run_intents_by_status(run_id=args.run_id)
    for status, count in counts.items():
        print(f"  {status}: {count}")

    if result.get("error"):
        print(f"\nError: {result['error']}")
        sys.exit(1)

    print()

    # Write artifact
    artifact = {
        "run_id": args.run_id,
        "trade_date": args.trade_date or "",
        "strategy_id": args.strategy_id,
        "account_name": getattr(adapter, "account_name", ""),
        "status": result["status"],
        "transitions_count": len(result.get("transitions", [])),
        "fill_count": result.get("fill_count", 0),
        "errors": result.get("errors", []),
        "transitions": result.get("transitions", []),
        "status_counts": ledger.count_run_intents_by_status(run_id=args.run_id),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    artifact_dir = Path("data/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / f"poll_{args.run_id}.json"
    artifact_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote poll artifact: %s", artifact_path)


if __name__ == "__main__":
    main()
