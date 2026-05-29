#!/usr/bin/env python3
"""Build an experiment index from references to existing research artifacts.

Usage::

    python scripts/research/build_experiment_index.py \\
        --experiment-id alpha_v1_cached_signal_smoke \\
        --title \"alpha_v1 cached signal smoke\" \\
        --signal-run alpha_v1_score:smoke_20260518_20260525 \\
        --signal-eval alpha_v1_score:smoke_20260518_20260525:forward_return_5d \\
        --backtest <strategy_run_id>:<backtest_id> \\
        --overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.research.experiment import ExperimentIndex, ExperimentSpec  # noqa: E402


def _parse_kv(value: str) -> tuple[str, str, str | None]:
    """Parse signal_run: signal_id:signal_run_id or signal_eval: signal_id:signal_run_id:label_id"""
    parts = value.split(":", 2)
    if len(parts) >= 2:
        return (parts[0], parts[1], parts[2] if len(parts) >= 3 else None)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build an experiment index from artifact references"
    )
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--title", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--tag", action="append", default=None, help="Tag (repeatable)")
    parser.add_argument("--signal-run", action="append", default=None,
                        help="signal_id:signal_run_id")
    parser.add_argument("--signal-eval", action="append", default=None,
                        help="signal_id:signal_run_id:label_id")
    parser.add_argument("--backtest", action="append", default=None,
                        help="strategy_run_id:backtest_id")
    parser.add_argument("--root", default="data/research")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--rebuild-only", action="store_true",
                        help="Skip add, only rebuild indexes")
    args = parser.parse_args()

    index = ExperimentIndex(root=args.root)
    spec = ExperimentSpec(
        experiment_id=args.experiment_id,
        title=args.title,
        description=args.description,
        tags=args.tag,
    )

    if not args.rebuild_only:
        index.create(spec, overwrite=args.overwrite)

        if args.signal_run:
            for sr in args.signal_run:
                parts = sr.split(":")
                if len(parts) >= 2:
                    index.add_signal_run(args.experiment_id, signal_id=parts[0], signal_run_id=parts[1])

        if args.signal_eval:
            for se in args.signal_eval:
                parts = se.split(":")
                if len(parts) >= 3:
                    index.add_signal_eval(args.experiment_id, signal_id=parts[0],
                                          signal_run_id=parts[1], label_id=parts[2])

        if args.backtest:
            for bt in args.backtest:
                parts = bt.split(":")
                if len(parts) >= 2:
                    index.add_backtest_run(args.experiment_id, strategy_run_id=parts[0], backtest_id=parts[1])

    result = index.rebuild_indexes(args.experiment_id)
    result_dict = result.to_dict()
    result_dict["status"] = "passed"
    print(json.dumps(result_dict, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
