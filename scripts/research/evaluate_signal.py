#!/usr/bin/env python3
"""Evaluate a signal run against a label artifact.

Usage::

    python scripts/research/evaluate_signal.py \\
        --signal-id alpha_v1_score \\
        --signal-run-id smoke_20260518_20260525 \\
        --label-id forward_return_5d \\
        --score-column score \\
        --n-groups 5 \\
        --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.research.evaluation import SignalEvaluator  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a signal run against a label artifact"
    )
    parser.add_argument("--signal-id", required=True)
    parser.add_argument("--signal-run-id", required=True)
    parser.add_argument("--label-id", required=True)
    parser.add_argument("--score-column", default="score")
    parser.add_argument("--n-groups", type=int, default=5)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--min-count", type=int, default=5)
    parser.add_argument("--root", default="data/research")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    evaluator = SignalEvaluator(root=args.root)
    result = evaluator.evaluate(
        signal_id=args.signal_id,
        signal_run_id=args.signal_run_id,
        label_id=args.label_id,
        score_column=args.score_column,
        n_groups=args.n_groups,
        start_date=args.start_date,
        end_date=args.end_date,
        min_count=args.min_count,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        overwrite=args.overwrite,
    )

    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
