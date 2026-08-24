#!/usr/bin/env python3
"""Compare two frozen rolling prediction artifacts on retrain dates."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from qsys.evaluation.top_tail import TopTailValidationError, evaluate_top_tail, write_top_tail_artifacts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-predictions", required=True, type=Path)
    parser.add_argument("--candidate-predictions", required=True, type=Path)
    parser.add_argument("--baseline-rolling-windows", required=True, type=Path)
    parser.add_argument("--candidate-rolling-windows", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--label-manifest", required=True, type=Path)
    parser.add_argument("--score-column", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--maturity-end", default=None)
    parser.add_argument("--force", action="store_true", help="allow replacing a non-empty output directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        per_date, comparison = evaluate_top_tail(
            args.baseline_predictions,
            args.candidate_predictions,
            args.baseline_rolling_windows,
            args.candidate_rolling_windows,
            args.labels,
            args.label_manifest,
            score_column=args.score_column,
            maturity_end=args.maturity_end,
        )
        write_top_tail_artifacts(per_date, comparison, args.output_dir, force=args.force)
    except (TopTailValidationError, OSError, ValueError) as exc:
        print(f"top-tail evaluation failed: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.output_dir}/per_date.parquet")
    print(f"wrote {args.output_dir}/comparison.json")
    print(f"gate_pass={comparison['gate']['pass']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
