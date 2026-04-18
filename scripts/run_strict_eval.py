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
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.evaluation import StrictEvaluator
from qsys.config import cfg
from qsys.reports.strict_eval import StrictEvalReport
from qsys.research import resolve_mainline_object_name
from qsys.reports.unified_schema import unified_run_artifacts, write_csv, write_json
from qsys.utils.logger import log


TRAINING_FIELDS = [
    "training_mode",
    "train_end_requested",
    "train_end_effective",
    "infer_date",
    "last_train_sample_date",
    "max_label_date_used",
    "is_label_mature_at_infer_time",
    "mlflow_root",
]


def load_training_summary(model_path: str) -> dict:
    payload = {field: None for field in TRAINING_FIELDS}
    model_dir = Path(model_path)
    json_path = model_dir / "training_summary.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            payload.update({field: data.get(field) for field in TRAINING_FIELDS})
            payload["status"] = "available"
            return payload
        except Exception:
            pass
    meta_path = model_dir / "meta.yaml"
    if meta_path.exists():
        try:
            data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
            summary = data.get("training_summary") or {}
            payload.update({field: summary.get(field) for field in TRAINING_FIELDS})
            payload["status"] = "available"
            return payload
        except Exception:
            pass
    payload["status"] = "not_available_in_strict_eval"
    return payload


def load_training_snapshot(model_path: str) -> dict[str, Any]:
    model_dir = Path(model_path)
    snapshot_path = model_dir / "config_snapshot.json"
    if not snapshot_path.exists():
        return {
            "status": "not_found",
            "lineage_status": "legacy_or_incomplete_lineage",
            "snapshot_path": str(snapshot_path),
        }
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        payload["status"] = "available"
        payload["snapshot_path"] = str(snapshot_path)
        payload.setdefault("lineage_status", payload.get("object_layer_status") or "snapshot_available")
        return payload
    except Exception as exc:
        return {
            "status": "invalid",
            "lineage_status": "legacy_or_incomplete_lineage",
            "snapshot_path": str(snapshot_path),
            "error": str(exc),
        }


def build_lineage_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    feature_set = snapshot.get("feature_set")
    bundle_id = snapshot.get("bundle_id")
    return {
        "input_mode": snapshot.get("input_mode") or "feature_set",
        "feature_set": feature_set,
        "bundle_id": bundle_id,
        "mainline_object_name": snapshot.get("mainline_object_name") or resolve_mainline_object_name(feature_set=feature_set, bundle_id=bundle_id),
        "legacy_feature_set_alias": snapshot.get("legacy_feature_set_alias") or feature_set,
        "factor_variants": list(snapshot.get("factor_variants") or []),
        "bundle_resolution_status": snapshot.get("bundle_resolution_status") or "legacy_or_incomplete_lineage",
        "object_layer_status": snapshot.get("object_layer_status") or "legacy_or_incomplete_lineage",
        "lineage_status": snapshot.get("lineage_status") or "legacy_or_incomplete_lineage",
        "label_spec": dict(snapshot.get("label_spec") or {}),
        "model_spec": dict(snapshot.get("model_spec") or {}),
        "strategy_spec": dict(snapshot.get("strategy_spec") or {}),
        "cost_spec": dict(snapshot.get("cost_spec") or {"status": "not_available_in_training_snapshot"}),
        "snapshot_path": snapshot.get("snapshot_path"),
    }


def _lineage_summary(lineage: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_mode": lineage.get("input_mode"),
        "feature_set": lineage.get("feature_set"),
        "bundle_id": lineage.get("bundle_id"),
        "mainline_object_name": lineage.get("mainline_object_name"),
        "legacy_feature_set_alias": lineage.get("legacy_feature_set_alias"),
        "strategy_type": (lineage.get("strategy_spec") or {}).get("strategy_type"),
        "lineage_status": lineage.get("lineage_status"),
    }


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

    print("\n=== Summary Comparison ===")
    summary = eval_report.summary_table()
    if not summary.empty:
        print(summary.to_string())

    duration = time.time() - start_time

    if not args.no_report:
        baseline_snapshot = load_training_snapshot(args.baseline)
        extended_snapshot = load_training_snapshot(args.extended)
        baseline_lineage = build_lineage_payload(baseline_snapshot)
        extended_lineage = build_lineage_payload(extended_snapshot)

        json_report = StrictEvalReport.from_evaluation_report(
            eval_report,
            baseline_path=args.baseline,
            extended_path=args.extended,
            top_k=args.top_k,
            duration_seconds=duration,
            notes=[
                f"Output CSV: {args.output}",
                f"Baseline lineage: {baseline_lineage.get('lineage_status')}",
                f"Extended lineage: {extended_lineage.get('lineage_status')}",
            ],
        )
        json_report.model_info.update({
            "baseline_lineage": _lineage_summary(baseline_lineage),
            "extended_lineage": _lineage_summary(extended_lineage),
        })

        unified_paths = unified_run_artifacts(Path(args.output).resolve().parent)
        json_report.artifacts["config_snapshot"] = write_json(unified_paths["config_snapshot"], {
            "baseline": args.baseline,
            "extended": args.extended,
            "end": args.end,
            "top_k": args.top_k,
            "output": args.output,
            "baseline_lineage": baseline_lineage,
            "extended_lineage": extended_lineage,
            "spec_status": "snapshot_driven_when_available_v1",
        })
        json_report.artifacts["training_summary"] = write_json(
            unified_paths["training_summary"],
            {
                "baseline_training_summary": load_training_summary(args.baseline),
                "extended_training_summary": load_training_summary(args.extended),
            },
        )
        json_report.artifacts["signal_metrics"] = write_json(
            unified_paths["signal_metrics"],
            {
                "status": "not_computed_in_strict_eval_flow",
                "baseline_lineage": baseline_lineage,
                "extended_lineage": extended_lineage,
                "top_k": args.top_k,
                "signal_eval_status": "separate_from_portfolio_backtest",
            },
        )
        json_report.artifacts["execution_audit"] = write_csv(unified_paths["execution_audit"], [])
        json_report.artifacts["suspicious_trades"] = write_csv(unified_paths["suspicious_trades"], [])
        json_report.artifacts["metrics"] = write_json(
            unified_paths["metrics"],
            {
                "rows": len(eval_report.results),
                "baseline_lineage": baseline_lineage,
                "extended_lineage": extended_lineage,
                "top_k": args.top_k,
            },
        )
        report_path = StrictEvalReport.save(json_report)
        log.info(f"Structured report saved to {report_path}")

        print("\n" + "=" * 60)
        print(json_report.to_markdown())
        print("=" * 60)

    log.info(f"Strict Evaluation Completed. Duration: {duration:.1f}s")


if __name__ == "__main__":
    main()
