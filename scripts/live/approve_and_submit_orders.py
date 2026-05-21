#!/usr/bin/env python3
"""Review, approve, and submit orders to the MiniQMT broker.

Usage:
    python scripts/live/approve_and_submit_orders.py \\
        --order-intents-path /path/to/order_intents.csv \\
        --trade-date 2026-04-25 \\
        --run-id shadow_2026-04-25_090807 \\
        --broker-url http://localhost:8080 \\
        [--dry-run] [--auto-approve] [--no-risk]

This is the second script in the Phase 1 live chain. It:
1. Loads order intents from the shadow pipeline
2. Records intents in TradeLedger
3. Runs pre-trade risk checks
4. Pauses for manual approval (unless --auto-approve)
5. Submits to the MiniQMT broker
6. Records broker acks in the ledger
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qsys.broker.miniqmt import MiniQMTAdapter
from qsys.execution.converter import from_order_intents_csv
from qsys.execution.service import ExecutionService
from qsys.trader.database import TradeLedger
from qsys.utils.logger import log


def prompt_approval(requests, risk_result) -> bool:
    """Show the plan and ask the user to approve."""
    print("\n" + "=" * 60)
    print("  ORDER APPROVAL — Review before submitting")
    print("=" * 60)

    if risk_result is not None:
        print(f"\nPre-trade Risk:")
        print(f"  Passed: {risk_result.summary['passed_count']}")
        print(f"  Failed: {risk_result.summary['failed_count']}")
        if risk_result.summary["failed_orders"]:
            print("  Failed orders:")
            for f in risk_result.summary["failed_orders"]:
                print(f"    - {f['symbol']} ({f['intent_id']}): {f['reason']}")

    print(f"\nOrders to submit ({len(requests)}):")
    for req in requests:
        print(f"  {req.side.upper():4s} {req.symbol:12s} qty={req.quantity:>6d}  price={req.price or 'MARKET':>10s}  [{req.intent_id}]")
    print()

    while True:
        response = input("Submit these orders? [y/N]: ").strip().lower()
        if response == "y":
            return True
        if response in ("n", ""):
            return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve and submit orders to MiniQMT broker")
    parser.add_argument("--order-intents-path", required=True, help="Path to order_intents.csv from shadow pipeline")
    parser.add_argument("--trade-date", required=True, help="Trading date YYYY-MM-DD")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--broker-url", default="http://localhost:8080", help="MiniQMT server URL")
    parser.add_argument("--strategy-id", default="alpha_v1", help="Strategy identifier")
    parser.add_argument("--ledger-path", default=None, help="TradeLedger SQLite path")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry-run mode (no real orders)")
    parser.add_argument("--auto-approve", action="store_true", help="Skip manual approval prompt")
    parser.add_argument("--no-risk", action="store_true", help="Skip pre-trade risk checks")
    parser.add_argument("--available-cash", type=float, default=0.0, help="Available cash for risk check")
    args = parser.parse_args()

    # Init components
    adapter = MiniQMTAdapter(
        account_name="real",
        base_url=args.broker_url,
    )
    ledger_path = args.ledger_path or f"data/trade_{args.trade_date}.db"
    ledger = TradeLedger(db_path=ledger_path)
    service = ExecutionService(adapter, ledger, strategy_id=args.strategy_id)

    # Load intents
    csv_path = Path(args.order_intents_path)
    if not csv_path.exists():
        log.error("Order intents file not found: %s", csv_path)
        sys.exit(1)

    requests = from_order_intents_csv(
        csv_path,
        trade_date=args.trade_date,
        run_id=args.run_id,
    )

    if not requests:
        log.info("No tradeable intents — nothing to submit.")
        return

    log.info("Loaded %d order intents from %s", len(requests), csv_path)

    # Pre-trade risk check (preview before approval)
    risk_result = None
    if not args.no_risk and not args.dry_run:
        from qsys.risk.pre_trade import check_pre_trade_risk
        # Fetch live snapshot for cash
        try:
            account = adapter.fetch_account_snapshot()
            cash = account.available_cash
        except Exception:
            cash = args.available_cash
        risk_result = check_pre_trade_risk(requests, available_cash=cash)

    # Manual approval gate
    if not args.auto_approve and not args.dry_run:
        if not prompt_approval(requests, risk_result):
            print("Orders rejected by user.")
            return

    # Submit
    result = service.prepare_and_submit(
        requests=requests,
        trade_date=args.trade_date,
        run_id=args.run_id,
        dry_run=args.dry_run,
        risk_check=not args.no_risk,
        available_cash=args.available_cash,
    )

    print(f"\n=== Submit Result ===")
    print(f"  Status:    {result['status']}")
    print(f"  Submitted: {result['submitted_count']}")
    print(f"  Rejected:  {result['rejected_count']}")
    if result.get("errors"):
        print(f"  Errors:    {result['errors']}")
    if result.get("acks"):
        print(f"\n  Acknowledgements:")
        for ack in result["acks"]:
            print(f"    {ack['intent_id']:40s} → broker_order_id={ack['broker_order_id']:30s} status={ack['status']}")
    print(f"\nNext: python scripts/live/poll_broker_orders.py --run-id {args.run_id}")
    print()


if __name__ == "__main__":
    main()
