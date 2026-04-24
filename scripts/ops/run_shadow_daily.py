#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops import DAILY_STAGES, finalize_run, format_run_id, initialize_run, update_stage_status
from qsys.ops.state import load_json


def build_stub_stage_payload(stage_name: str, run_id: str, trade_date: str) -> dict[str, object]:
    return {
        "stage": stage_name,
        "mode": "stub",
        "run_id": run_id,
        "trade_date": trade_date,
        "message": f"Stub stage completed: {stage_name}",
    }


def run_shadow_daily(base_dir: str | Path, *, run_id: str | None = None, triggered_by: str = "manual") -> dict[str, object]:
    now = datetime.now()
    resolved_run_id = run_id or format_run_id("daily", now)
    latest_model_pointer = str(Path(base_dir) / "models" / "latest_shadow_model.json")
    context = initialize_run(
        base_dir,
        run_type="daily",
        run_id=resolved_run_id,
        mainline_object_name="shadow_daily_stub_mainline",
        bundle_id="shadow_daily_stub_bundle",
        model_name="shadow_daily_stub_model",
        model_snapshot_path="",
        latest_model_pointer=latest_model_pointer,
        data_snapshot={"mode": "stub", "triggered_by": triggered_by},
        fallback_summary={"used": False},
        notes=["Stub daily runner: no real training, inference, or rebalance."],
    )

    stage_artifacts: dict[str, str] = {}
    for stage_name in DAILY_STAGES:
        started_at = datetime.now().replace(microsecond=0).isoformat()
        update_stage_status(
            context,
            stage_name=stage_name,
            status="running",
            started_at=started_at,
            message=f"Stage started: {stage_name}",
        )
        payload = build_stub_stage_payload(stage_name, context.run_id, context.trade_date)
        artifact_path = context.run_dir / f"{stage_name}.json"
        artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stage_artifacts[stage_name] = str(artifact_path)
        update_stage_status(
            context,
            stage_name=stage_name,
            status="success",
            ended_at=datetime.now().replace(microsecond=0).isoformat(),
            message=f"Stage completed in stub mode: {stage_name}",
            artifact_pointers={"stage_output": str(artifact_path)},
        )

    manifest = load_json(context.manifest_path)
    summary = finalize_run(
        context,
        daily_summary={
            "trade_date": manifest["trade_date"],
            "run_id": context.run_id,
            "run_type": "shadow_daily",
            "data_status": manifest["stage_status"]["data_sync"]["status"],
            "feature_status": manifest["stage_status"]["feature_refresh"]["status"],
            "train_status": manifest["stage_status"]["maybe_retrain"]["status"],
            "model_used": {
                "model_name": manifest["model_name"],
                "model_snapshot_path": manifest["model_snapshot_path"],
                "latest_model_pointer": manifest["latest_model_pointer"],
            },
            "inference_status": manifest["stage_status"]["inference"]["status"],
            "rebalance_status": manifest["stage_status"]["shadow_rebalance"]["status"],
            "shadow_order_count": 0,
            "degradation_level": "none",
            "decision_status": manifest["overall_status"],
            "notes": ["Stub daily workflow completed without external systems."],
        },
        notes=["Shadow daily skeleton completed in stub mode."],
        fallback_summary={"used": False, "reason": "stub_run"},
    )
    return {
        "run_id": context.run_id,
        "run_dir": str(context.run_dir),
        "manifest_path": str(context.manifest_path),
        "summary_path": str(context.summary_path),
        "overall_status": load_json(context.manifest_path)["overall_status"],
        "triggered_by": triggered_by,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the shadow daily stub workflow")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Project root for writing shadow ops artifacts")
    parser.add_argument("--run-id", help="Optional stable run_id for reruns")
    parser.add_argument("--triggered-by", default="manual", help="Who triggered this run")
    args = parser.parse_args()
    result = run_shadow_daily(args.base_dir, run_id=args.run_id, triggered_by=args.triggered_by)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
