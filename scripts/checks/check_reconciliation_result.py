#!/usr/bin/env python3
"""Validate reconciliation result CSVs + JSON.

Checks the three CSV files produced by postclose reconciliation:
  - reconcile_summary_<date>.csv
  - reconcile_positions_<date>.csv
  - reconcile_real_trades_<date>.csv

Also checks the JSON reconciliation_result.json produced by DailyRunner.

Read-only. Outputs JSON summary to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REQUIRED_SUMMARY_COLS = {"metric", "real", "shadow", "diff"}
REQUIRED_POSITION_COLS = {"symbol", "real_amount", "shadow_amount", "amount_diff",
                          "real_market_value", "shadow_market_value", "market_value_diff"}
REQUIRED_TRADES_COLS = {"symbol", "side", "amount", "price"}
REQUIRED_JSON_KEYS = {"execution_date", "strategy_id", "status", "reason",
                      "position_gap", "cash_gap"}


def _check_csv(path: Path, required_cols: set[str], label: str, result: dict) -> int:
    """Check a single reconciliation CSV. Returns row count."""
    import pandas as pd

    if not path.exists():
        result["errors"].append(f"{label}: path not found")
        return 0

    try:
        df = pd.read_csv(path)
    except Exception as e:
        result["errors"].append(f"{label}: unreadable: {e}")
        return 0

    if df.empty:
        result["warnings"].append(f"{label}: empty CSV")

    cols = set(df.columns)
    missing = required_cols - cols
    if missing:
        result["errors"].append(f"{label}: missing columns: {sorted(missing)}")

    return len(df)


def _check_json(path: Path, result: dict) -> None:
    """Check JSON reconciliation result fields."""
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        result["errors"].append(f"{path.name}: unreadable: {e}")
        return
    if not isinstance(data, dict):
        result["errors"].append(f"{path.name}: not a dict")
        return
    missing = REQUIRED_JSON_KEYS - set(data.keys())
    if missing:
        result["errors"].append(f"{path.name}: missing keys: {sorted(missing)}")
    valid_statuses = {"matched", "skipped", "warning", "blocked"}
    if data.get("status") not in valid_statuses:
        result["warnings"].append(f"{path.name}: unusual status: {data.get('status')}")


def check_reconciliation_result(dir_path: Path) -> dict:
    result = {
        "status": "passed",
        "path": str(dir_path),
        "errors": [],
        "warnings": [],
        "summary_count": 0,
        "position_count": 0,
        "trade_count": 0,
        "files_found": [],
    }

    if not dir_path.exists():
        result["status"] = "failed"
        result["errors"].append("path not found")
        return result
    if not dir_path.is_dir():
        result["status"] = "failed"
        result["errors"].append("path is not a directory")
        return result

    # Check JSON result first
    json_path = dir_path / "reconciliation_result.json"
    if json_path.exists():
        _check_json(json_path, result)
        result["files_found"].append(json_path.name)

    # Check CSV files
    csv_files = sorted(dir_path.glob("reconcile_*.csv"))
    result["files_found"].extend(f.name for f in csv_files)

    for f in csv_files:
        if "summary" in f.name:
            result["summary_count"] = _check_csv(f, REQUIRED_SUMMARY_COLS, f.name, result)
        elif "position" in f.name:
            result["position_count"] = _check_csv(f, REQUIRED_POSITION_COLS, f.name, result)
        elif "trade" in f.name or "real_trades" in f.name:
            result["trade_count"] = _check_csv(f, REQUIRED_TRADES_COLS, f.name, result)

    if result["errors"]:
        result["status"] = "failed"
    elif not result["files_found"]:
        result["warnings"].append("no reconciliation result files found in directory")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate reconciliation result directory")
    parser.add_argument("--path", required=True, help="Reconciliation output directory")
    args = parser.parse_args()

    result = check_reconciliation_result(Path(args.path))
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] in ("passed",) else 1)


if __name__ == "__main__":
    main()
