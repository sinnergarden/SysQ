#!/usr/bin/env python3
"""Generate a live execution plan from the alpha_v1 signal pipeline.

Usage:
    python scripts/live/run_alpha_v1_live_plan.py \\
        --trade-date 2026-04-25 \\
        --run-id shadow_2026-04-25_090807 \\
        [--predictions-path /path/to/predictions.csv]

Produces:
    - Order intents CSV (usable by ``approve_and_submit_orders.py``)
    - Target weights CSV
    - Execution summary JSON

This is the first script in the Phase 1 live chain. It runs the alpha_v1
shadow rebalance pipeline and persists the artifacts for downstream scripts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qsys.ops.shadow_rebalance import run_alpha_v1_shadow_rebalance
from qsys.utils.logger import log


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate alpha_v1 live execution plan")
    parser.add_argument("--trade-date", required=True, help="Trading date YYYY-MM-DD")
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--predictions-path", required=True, help="Path to predictions CSV")
    parser.add_argument("--base-dir", default=".", help="Base data directory (default: cwd)")
    parser.add_argument("--output-dir", default=None, help="Output directory for plan artifacts")
    args = parser.parse_args()

    base_dir = Path(args.base_dir).resolve()
    output_dir = Path(args.output_dir).resolve() if args.output_dir else (base_dir / "daily" / args.trade_date / "pre_open")

    log.info("Generating live plan for %s run=%s", args.trade_date, args.run_id)

    artifacts = run_alpha_v1_shadow_rebalance(
        base_dir=base_dir,
        run_id=args.run_id,
        trade_date=args.trade_date,
        predictions_path=args.predictions_path,
        output_dir=output_dir,
    )

    print(f"\n=== Live Plan Generated ===")
    print(f"  Trade date:   {args.trade_date}")
    print(f"  Run ID:       {args.run_id}")
    print(f"  Orders:       {artifacts.order_count} (buy={artifacts.buy_count}, sell={artifacts.sell_count})")
    print(f"  Turnover:     {artifacts.turnover:.2f}")
    print(f"  Target weights: {artifacts.target_weights_path}")
    print(f"  Order intents:  {artifacts.order_intents_path}")
    print(f"\nNext: python scripts/live/approve_and_submit_orders.py")
    print(f"  --order-intents-path {artifacts.order_intents_path}")
    print(f"  --trade-date {args.trade_date}")
    print(f"  --run-id {args.run_id}")
    print()


if __name__ == "__main__":
    main()
