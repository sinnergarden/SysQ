#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops.qlib_candidate import apply_candidate_switch, plan_candidate_switch


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or apply a qlib candidate switch.")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT))
    parser.add_argument("--candidate-qlib-dir", required=True)
    parser.add_argument("--validation-summary-path", default="experiments/ops_diagnostics/csi800_full_rebuild/candidate_validation_summary.json")
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/csi800_full_rebuild")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        result = apply_candidate_switch(
            base_dir=args.base_dir,
            candidate_qlib_dir=args.candidate_qlib_dir,
            validation_summary_path=args.validation_summary_path,
            output_dir=args.output_dir,
        )
    else:
        result = plan_candidate_switch(
            base_dir=args.base_dir,
            candidate_qlib_dir=args.candidate_qlib_dir,
            validation_summary_path=args.validation_summary_path,
            output_dir=args.output_dir,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
