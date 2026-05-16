#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops.qlib_candidate import build_candidate


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an out-of-place qlib candidate from raw data.")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT))
    parser.add_argument("--universe", default="csi800")
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--end-date", default="2026-04-30")
    parser.add_argument("--output-qlib-dir", default=None)
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/csi800_full_rebuild")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    result = build_candidate(
        base_dir=args.base_dir,
        universe=args.universe,
        start_date=args.start_date,
        end_date=args.end_date,
        output_qlib_dir=args.output_qlib_dir,
        output_dir=args.output_dir,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
