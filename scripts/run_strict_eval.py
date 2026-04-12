#!/usr/bin/env python3
"""
Primary strict evaluation entrypoint.

Purpose:
- compare baseline vs extended model artifacts under the unified evaluation contract
- use roadmap defaults for main/aux windows and `top_k=5`
- emit structured strict-eval report

Typical usage:
- python scripts/run_strict_eval.py --baseline data/models/qlib_lgbm_phase123 --extended data/models/qlib_lgbm_phase123_extended

Key args:
- --baseline / --extended: model paths to compare
- --end: evaluation end date override
- --top_k: portfolio breadth (default 5)
- --no_report: skip JSON run report
"""
import sys
import time
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.evaluation import StrictEvaluator
from qsys.config import cfg
from qsys.reports.strict_eval import StrictEvalReport
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.utils.logger import log


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Run Strict Evaluation: Baseline vs Extended"
    )
    parser.add_argument(
        "--baseline",
        default=str(project_root / "data" / "models" / "qlib_lgbm_phase123"),
        help="Path to baseline model (default: data/models/qlib_lgbm_phase123)"
    )
    parser.add_argument(
        "--extended",
        default=str(project_root / "data" / "models" / "qlib_lgbm_phase123_extended"),
        help="Path to extended model (default: data/models/qlib_lgbm_phase123_extended)"
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
        
        unified_paths = unified_run_artifacts(Path(args.output).resolve().parent)
        json_report.artifacts["config_snapshot"] = write_json(unified_paths["config_snapshot"], {
            "baseline": args.baseline,
            "extended": args.extended,
            "end": args.end,
            "top_k": args.top_k,
            "output": args.output,
        })
        json_report.artifacts["training_summary"] = write_json(unified_paths["training_summary"], {})
        json_report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
        json_report.artifacts["suspicious_trades"] = write_csv(unified_paths["suspicious_trades"], [])
        json_report.artifacts["metrics"] = write_json(unified_paths["metrics"], {"rows": len(eval_report.results)})
        report_path = StrictEvalReport.save(json_report)
        log.info(f"Structured report saved to {report_path}")
        
        # Print markdown summary
        print("\n" + "=" * 60)
        print(json_report.to_markdown())
        print("=" * 60)
    
    log.info(f"Strict Evaluation Completed. Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()