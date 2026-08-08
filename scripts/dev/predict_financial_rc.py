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
    parser = argparse.ArgumentParser(
        description="Deprecated financial_rc inference wrapper"
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--weight-60d", type=float, default=0.5)
    parser.add_argument("--weight-180d", type=float, default=0.5)
    parser.add_argument("--output-root", default="outputs")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if (args.weight_60d, args.weight_180d) != (0.5, 0.5):
        parser.error(
            "weights are pinned in configs/strategies/financial_rc.yaml (0.5/0.5)"
        )
    if args.output_root != "outputs" or args.force:
        parser.error(
            "canonical inference artifacts are immutable under outputs/; override is forbidden"
        )

    warnings.warn(
        "scripts/dev/predict_financial_rc.py is deprecated; use "
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
            str(args.top_k),
        ]
    )


if __name__ == "__main__":
    main()
