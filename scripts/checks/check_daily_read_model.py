#!/usr/bin/env python3
"""Validate daily ops manifest (daily_ops_manifest_<date>.json).

Checks the daily pipeline run manifest for required stage/status info,
artifact paths, and consistency. Does not require broker access.

Read-only. Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_TOP_KEYS = {"execution_date", "stages", "updated_at"}
STAGE_NAMES = {"pre_open", "post_close"}
STAGE_KEYS = {"status", "report_path", "summary"}


def check_daily_read_model(path: Path) -> dict:
    result = {
        "status": "passed",
        "path": str(path),
        "errors": [],
        "warnings": [],
        "execution_date": None,
        "stages": {},
    }

    if not path.exists():
        result["status"] = "failed"
        result["errors"].append("path not found")
        return result

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        result["status"] = "failed"
        result["errors"].append(f"unreadable JSON: {e}")
        return result

    if not isinstance(data, dict):
        result["status"] = "failed"
        result["errors"].append("root is not a JSON object")
        return result

    missing_top = REQUIRED_TOP_KEYS - set(data.keys())
    if missing_top:
        result["status"] = "failed"
        result["errors"].append(f"missing top-level keys: {sorted(missing_top)}")
        return result

    result["execution_date"] = data.get("execution_date")

    stages = data.get("stages", {})
    for stage_name in STAGE_NAMES:
        if stage_name not in stages:
            result["warnings"].append(f"stage {stage_name!r}: missing")
            continue

        stage = stages[stage_name]
        if not isinstance(stage, dict):
            result["warnings"].append(f"stage {stage_name!r}: not a dict")
            continue

        stage_info = {"status": stage.get("status", "unknown")}
        result["stages"][stage_name] = stage_info

        missing_keys = STAGE_KEYS - set(stage.keys())
        if missing_keys:
            result["warnings"].append(f"stage {stage_name!r}: missing keys: {sorted(missing_keys)}")

        # Check shadow plan status within summary
        summary = stage.get("summary", {})
        for plan_key in ("shadow_plan", "real_plan"):
            plan = summary.get(plan_key, {}) if isinstance(summary, dict) else {}
            if plan:
                plan_status = plan.get("status", "unknown")
                stage_info[f"{plan_key}_status"] = plan_status

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate daily ops manifest")
    parser.add_argument("--path", required=True, help="daily_ops_manifest.json path")
    args = parser.parse_args()

    result = check_daily_read_model(Path(args.path))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed",) else 1)


if __name__ == "__main__":
    main()
