#!/usr/bin/env python3
"""Run a rolling research pipeline from a config file.

Usage::

    python scripts/research/run_rolling_research.py \\
        --config configs/research/alpha_v1_rolling_smoke.yaml \\
        --overwrite-experiment
"""

from __future__ import annotations
import warnings; warnings.warn("Use scripts/run_research.py instead (UC-4)", DeprecationWarning, stacklevel=2)

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))

from qsys.research.rolling_runner import RollingResearchRunner  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Rolling research pipeline")
    parser.add_argument("--config", required=True, help="YAML/JSON config path")
    parser.add_argument("--root", default="data/research")
    parser.add_argument("--overwrite-signal", action="store_true")
    parser.add_argument("--overwrite-eval", action="store_true")
    parser.add_argument("--overwrite-backtest", action="store_true")
    parser.add_argument("--overwrite-experiment", action="store_true")
    parser.add_argument("--overwrite-all", action="store_true")
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(json.dumps({"status": "failed", "error": f"Config not found: {config_path}"}))
        sys.exit(1)

    runner = RollingResearchRunner(root=args.root)
    result = runner.run(
        config=str(config_path),
        overwrite_signal=args.overwrite_all or args.overwrite_signal,
        overwrite_eval=args.overwrite_all or args.overwrite_eval,
        overwrite_backtest=args.overwrite_all or args.overwrite_backtest,
        overwrite_experiment=args.overwrite_all or args.overwrite_experiment,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
