#!/usr/bin/env python3
"""Recover orders stuck in ``submit_unknown`` status by polling the broker.

Usage::

    python scripts/deprecated/recover_unknown_orders.py \\
        --broker-url http://localhost:8080 \\
        --run-id shadow_2026-04-25_090807 \\
        --trade-date 2026-04-25

Workflow:
1. Reads the TradeLedger for all intents with ``status=submit_unknown``
2. Polls the broker for each (by ``broker_order_id`` if available, else by ``intent_id``)
3. If the order is found at the broker — updates the ledger to the resolved status
4. If the order is **not** found — prints a manual-investigation prompt
5. **Never** auto-resubmits an unknown order
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from qsys.broker.miniqmt import MiniQMTAdapter
from qsys.execution.models import OS_SUBMIT_UNKNOWN
from qsys.trader.database import TradeLedger
from qsys.utils.logger import log


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover submit_unknown orders")
    parser.add_argument("--broker-url", default="http://localhost:8080", help="MiniQMT server URL")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--trade-date", default=None, help="Trading date")
    parser.add_argument("--strategy-id", default="alpha_v1", help="Strategy identifier")
    parser.add_argument("--account-name", default="real", help="Account name")
    parser.add_argument("--ledger-path", default=None, help="TradeLedger SQLite path")
    args = parser.parse_args()

    adapter = MiniQMTAdapter(
        account_name=args.account_name,
        base_url=args.broker_url,
    )
    ledger_path = args.ledger_path or f"data/trade_{args.trade_date or 'latest'}.db"
    ledger = TradeLedger(db_path=ledger_path)

    # Find submit_unknown intents
    intents = ledger.get_run_intents(run_id=args.run_id)
    unknown = [i for i in intents if i["status"] == OS_SUBMIT_UNKNOWN]

    if not unknown:
        log.info("No submit_unknown intents found for run=%s", args.run_id)
        artifact = {
            "run_id": args.run_id,
            "trade_date": args.trade_date or "",
            "strategy_id": args.strategy_id,
            "account_name": args.account_name,
            "status": "clean",
            "total_unknown": 0,
            "recovered": 0,
            "unresolved": 0,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        _write_artifact(args.run_id, artifact)
        print(f"\n=== Recover Result ===")
        print(f"  No submit_unknown intents found. All clean.")
        print()
        return

    log.info("Found %d submit_unknown intents for run=%s", len(unknown), args.run_id)

    filters: dict[str, str] = {}
    if args.trade_date:
        filters["trade_date"] = args.trade_date

    recovered = 0
    unresolved: list[dict[str, str]] = []

    for intent in unknown:
        intent_id = intent["intent_id"]
        broker_order_id = intent.get("broker_order_id", "")

        # Poll broker — prefer broker_order_id when available
        try:
            order_filters = dict(filters)
            if broker_order_id:
                order_filters["order_id"] = broker_order_id
            reports = adapter.fetch_orders(filters=order_filters)
        except Exception as exc:
            log.error("Poll failed for intent %s: %s", intent_id, exc)
            unresolved.append(
                {"intent_id": intent_id, "broker_order_id": broker_order_id, "reason": f"poll_error: {exc}"}
            )
            continue

        # Find matching report
        found = None
        for r in reports:
            if broker_order_id and r.broker_order_id == broker_order_id:
                found = r
                break
            if not found and r.intent_id == intent_id:
                found = r

        if found is not None:
            ledger.update_intent_status(
                idempotency_key=intent["idempotency_key"],
                status=found.status,
                broker_order_id=found.broker_order_id or broker_order_id,
                error="",
            )
            log.info(
                "Recovered intent %s: %s → %s (broker_order_id=%s)",
                intent_id,
                OS_SUBMIT_UNKNOWN,
                found.status,
                found.broker_order_id,
            )
            recovered += 1
        else:
            log.warning(
                "Unresolved intent %s: not found at broker. "
                "Manual investigation required. DO NOT auto-resubmit.",
                intent_id,
            )
            unresolved.append(
                {"intent_id": intent_id, "broker_order_id": broker_order_id, "reason": "not_found_at_broker"}
            )

    artifact = {
        "run_id": args.run_id,
        "trade_date": args.trade_date or "",
        "strategy_id": args.strategy_id,
        "account_name": args.account_name,
        "status": "unresolved" if unresolved else "recovered",
        "total_unknown": len(unknown),
        "recovered": recovered,
        "unresolved": len(unresolved),
        "unresolved_details": unresolved if unresolved else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_artifact(args.run_id, artifact)

    print(f"\n=== Recover Result ===")
    print(f"  Total unknown: {len(unknown)}")
    print(f"  Recovered:     {recovered}")
    print(f"  Unresolved:    {len(unresolved)}")
    if unresolved:
        print(f"\n  ⚠  Unresolved intents (not found at broker — manual check required):")
        for u in unresolved:
            print(f"     {u['intent_id']:40s}  broker_order_id={u['broker_order_id']}")
        print(f"\n  Do NOT auto-resubmit — orders may have been filled or rejected.")
    print()


def _write_artifact(run_id: str, artifact: dict) -> None:
    artifact_dir = Path("data/artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / f"recover_{run_id}.json"
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Wrote recovery artifact: %s", path)


if __name__ == "__main__":
    main()
