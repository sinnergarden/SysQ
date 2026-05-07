#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops.candidate_coverage_gap import run_candidate_gap_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit CSI800 candidate validation coverage gaps without mutating data.")
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--candidate-qlib-dir", required=True)
    parser.add_argument("--universe", default="csi800")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", default="experiments/ops_diagnostics/csi800_candidate_gap_audit")
    parser.add_argument(
        "--validation-summary-path",
        default="experiments/ops_diagnostics/csi800_full_rebuild/candidate_validation_summary.json",
    )
    args = parser.parse_args()

    result = run_candidate_gap_audit(
        base_dir=args.base_dir,
        candidate_qlib_dir=args.candidate_qlib_dir,
        universe=args.universe,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        validation_summary_path=args.validation_summary_path,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
