#!/usr/bin/env python3
"""
Strict factor comparison: Alpha158 baseline vs Extended features.

This script uses the unified StrictEvaluator from qsys.evaluation.
Defaults (from ROADMAP consensus):
- Main window: 2025-01-01 to latest available
- Auxiliary window: 2026 YTD (style-shift inspection)
- top_k=5 for all backtests
- Explicit train/valid/test separation

Usage:
    python scripts/run_strict_eval.py \
        --baseline data/models/qlib_lgbm \
        --extended data/models/qlib_lgbm_extended
"""
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.evaluation import StrictEvaluator
from qsys.config import cfg
from qsys.reports.strict_eval import StrictEvalReport
from qsys.utils.logger import log


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run Strict Evaluation: Baseline vs Extended"
    )
    parser.add_argument(
        "--baseline",
        default=str(project_root / "data" / "models" / "qlib_lgbm"),
        help="Path to baseline model (default: data/models/qlib_lgbm)"
    )
    parser.add_argument(
        "--extended", 
        default=str(project_root / "data" / "models" / "qlib_lgbm_extended"),
        help="Path to extended model (default: data/models/qlib_lgbm_extended)"
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date (YYYY-MM-DD). Defaults to latest available."
    )
    parser.add_argument(
        "--top_k",
        type=int,
        default=5,
        help="Number of stocks to select (default: 5)"
    )
    parser.add_argument(
        "--output",
        default=str(project_root / "experiments" / "strict_eval_results.csv"),
        help="Output CSV path"
    )
    parser.add_argument(
        "--no_report",
        action="store_true",
        help="Skip generating structured JSON report"
    )
    
    args = parser.parse_args()
    
    start_time = time.time()
    
    log.info(f"=== Strict Evaluation ===")
    log.info(f"Baseline: {args.baseline}")
    log.info(f"Extended: {args.extended}")
    log.info(f"Top K: {args.top_k}")
    
    evaluator = StrictEvaluator(top_k=args.top_k)
    
    eval_report = evaluator.run_comparison(
        baseline_model_path=args.baseline,
        extended_model_path=args.extended,
        end_date=args.end
    )
    
    print("\n" + eval_report.to_markdown())
    evaluator.save_report(eval_report, args.output)
    
    # Print summary comparison
    print("\n=== Summary Comparison ===")
    summary = eval_report.summary_table()
    if not summary.empty:
        print(summary.to_string())
    
    # Generate structured JSON report
    duration = time.time() - start_time
    
    if not args.no_report:
        json_report = StrictEvalReport.from_evaluation_report(
            eval_report,
            baseline_path=args.baseline,
            extended_path=args.extended,
            top_k=args.top_k,
            duration_seconds=duration,
            notes=[f"Output CSV: {args.output}"],
        )
        
        report_path = StrictEvalReport.save(json_report)
        log.info(f"Structured report saved to {report_path}")
        
        # Print markdown summary
        print("\n" + "=" * 60)
        print(json_report.to_markdown())
        print("=" * 60)
    
    log.info(f"Strict Evaluation Completed. Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()