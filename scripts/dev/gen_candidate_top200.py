#!/usr/bin/env python3
"""Deprecated compatibility wrapper for canonical financial_rc inference."""

from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_daily import run_daily_main


def main() -> None:
    parser = argparse.ArgumentParser(description="Deprecated Top-N candidate wrapper")
    parser.add_argument("--trade-date", default="auto")
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--w60", type=float, default=0.5)
    parser.add_argument("--w180", type=float, default=0.5)
    args = parser.parse_args()

    if (args.w60, args.w180) != (0.5, 0.5):
        parser.error(
            "weights are pinned in configs/strategies/financial_rc.yaml (0.5/0.5)"
        )
    warnings.warn(
        "scripts/dev/gen_candidate_top200.py is deprecated; use "
        "scripts/run_daily.py --strategy financial_rc --mode infer",
        DeprecationWarning,
        stacklevel=2,
    )
    run_daily_main(
        [
            "--strategy",
            "financial_rc",
            "--mode",
            "infer",
            "--signal-date",
            args.trade_date,
            "--top-k",
            str(args.top_n),
        ]
    )


if __name__ == "__main__":
    main()
