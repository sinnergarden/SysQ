#!/usr/bin/env python3
"""
Compare weekly replay outputs between two branches.

Compares prediction signals, order intents, execution results,
positions, and ledger state across a set of trade dates.

Usage:
    python scripts/dev/compare_weekly_replay.py \\
        --baseline /path/to/main/project/root \\
        --candidate /path/to/branch/project/root \\
        --trade-dates 2026-05-18,2026-05-19,2026-05-20,2026-05-21,2026-05-22
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qsys.ops.replay import (
    FAIL,
    PASS,
    SKIP,
    compare_artifacts,
    compare_executions,
    compare_plans,
    compare_predictions,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare weekly replay outputs between branches")
    parser.add_argument("--baseline", required=True, help="Path to main branch project root")
    parser.add_argument("--candidate", required=True, help="Path to PR branch project root")
    parser.add_argument("--trade-dates", required=True, help="Comma-separated trade dates, e.g. 2026-05-18,2026-05-19,...")
    args = parser.parse_args()

    base_dir = Path(args.baseline)
    cand_dir = Path(args.candidate)

    trade_dates = [d.strip() for d in args.trade_dates.split(",") if d.strip()]
    print(f"Trade dates: {trade_dates}")
    print(f"Baseline: {base_dir}")
    print(f"Candidate: {cand_dir}")

    results: list[tuple[str, bool]] = []

    ok = compare_predictions(base_dir, cand_dir, trade_dates)
    results.append(("Predictions", ok))

    ok = compare_plans(base_dir, cand_dir, trade_dates)
    results.append(("Order Intents", ok))

    ok = compare_executions(base_dir, cand_dir, trade_dates)
    results.append(("Executions", ok))

    ok = compare_artifacts(base_dir, cand_dir, trade_dates)
    results.append(("ADR-007 Sidecars", ok))

    # Summary
    print(f"\n{'='*60}")
    print("📊 SUMMARY")
    print(f"{'='*60}")
    all_pass = True
    for category, ok in results:
        status = PASS if ok else FAIL
        print(f"  {status}: {category}")
        if not ok:
            all_pass = False

    if all_pass:
        print(f"\n{PASS}: All comparisons passed — replay matches baseline.\n")
        sys.exit(0)
    else:
        print(f"\n{FAIL}: One or more comparisons failed — semantics may have changed.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
