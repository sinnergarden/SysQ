#!/usr/bin/env python3
"""Validate portfolio snapshot artifact (snapshot_index.json).

Checks the daily snapshot index for required top-level and artifact structure.
Read-only. Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_TOP_KEYS = {"execution_date", "archive_root", "stages"}
REQUIRED_STAGES = {"pre_open", "post_close"}


def _check_artifact_block(label: str, block: dict, result: dict) -> None:
    """Check an {category, path, exists} artifact entry."""
    if not isinstance(block, dict):
        result["errors"].append(f"{label}: not a dict")
        return
    for key in ("category", "path"):
        if key not in block:
            result["errors"].append(f"{label}: missing {key}")


def check_portfolio_snapshot(path: Path) -> dict:
    result = {
        "status": "passed",
        "path": str(path),
        "errors": [],
        "warnings": [],
        "execution_date": None,
        "stages_present": [],
        "artifact_count": 0,
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
    for stage_name in REQUIRED_STAGES:
        if stage_name in stages:
            result["stages_present"].append(stage_name)
            stage_block = stages[stage_name]
            if isinstance(stage_block, dict):
                artifacts = stage_block.get("artifacts", {})
                for art_name, art_block in artifacts.items():
                    result["artifact_count"] += 1
                    _check_artifact_block(f"{stage_name}/{art_name}", art_block, result)
            else:
                result["errors"].append(f"stage {stage_name}: not a dict")

    if result["errors"]:
        result["status"] = "failed"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate portfolio snapshot artifact")
    parser.add_argument("--path", required=True, help="snapshot_index.json path")
    args = parser.parse_args()

    result = check_portfolio_snapshot(Path(args.path))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed",) else 1)


if __name__ == "__main__":
    main()
