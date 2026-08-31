#!/usr/bin/env python3
"""Validate a Stage-B rolling model experiment independently."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.research.stage_b_validation import validate_stage_b_experiment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--research-root", default=Path("data/research"), type=Path)
    parser.add_argument("--cache-manifest", required=True, type=Path)
    parser.add_argument("--cache-validation", required=True, type=Path)
    parser.add_argument("--holdout-start", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = validate_stage_b_experiment(
        config_path=args.config,
        research_root=args.research_root,
        cache_manifest_path=args.cache_manifest,
        cache_validation_path=args.cache_validation,
        holdout_start=args.holdout_start,
        output_path=args.output,
    )
    print(
        f"validated {len(result['signals'])} Stage-B SignalRuns: {args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
