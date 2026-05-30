#!/usr/bin/env python3
"""Validate order intents artifact — structure, fields, and consistency.

Checks an order intents JSON file (as produced by preopen pipeline).
Read-only. Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_TOP_KEYS = {"artifact_type", "execution_date", "account_name", "intents"}
REQUIRED_INTENT_KEYS = {"intent_id", "symbol", "side", "amount", "price", "est_value", "status"}
VALID_SIDES = {"buy", "sell"}


def check_order_intents(path: Path) -> dict:
    result = {
        "status": "passed",
        "path": str(path),
        "errors": [],
        "warnings": [],
        "intent_count": 0,
        "total_amount": 0.0,
        "sides": {},
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

    # Check top-level keys
    if not isinstance(data, dict):
        result["status"] = "failed"
        result["errors"].append("root is not a JSON object")
        return result

    missing_top = REQUIRED_TOP_KEYS - set(data.keys())
    if missing_top:
        result["status"] = "failed"
        result["errors"].append(f"missing top-level keys: {sorted(missing_top)}")
        return result

    if not isinstance(data.get("intents"), list):
        result["status"] = "failed"
        result["errors"].append("intents is not a list")
        return result

    intents = data["intents"]
    result["intent_count"] = len(intents)
    result["execution_date"] = data.get("execution_date")
    result["account_name"] = data.get("account_name")

    for i, intent in enumerate(intents):
        if not isinstance(intent, dict):
            result["errors"].append(f"intent[{i}] is not a dict")
            continue

        missing = REQUIRED_INTENT_KEYS - set(intent.keys())
        if missing:
            result["errors"].append(f"intent[{i}] missing keys: {sorted(missing)}")

        side = intent.get("side")
        if side and side not in VALID_SIDES:
            result["warnings"].append(f"intent[{i}] unknown side: {side}")
        if side:
            result["sides"][side] = result["sides"].get(side, 0) + 1

        est_val = intent.get("est_value")
        if est_val is not None:
            result["total_amount"] += float(est_val)

    if result["errors"]:
        result["status"] = "failed"
    elif result["intent_count"] == 0:
        result["warnings"].append("order intents file is empty (0 intents)")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate order intents artifact")
    parser.add_argument("--path", required=True, help="Order intents JSON path")
    args = parser.parse_args()

    result = check_order_intents(Path(args.path))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed",) else 1)


if __name__ == "__main__":
    main()
