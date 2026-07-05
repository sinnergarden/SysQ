#!/usr/bin/env python3
"""Check daily inference readiness: verify that inference has enough explicit state to proceed.

Usage:
    python harness/checks/check_daily_inference_ready.py \
        --trade-date YYYY-MM-DD \
        --strategy-id STRATEGY_ID

Output:
    PASS if all checks pass, FAIL with missing items otherwise.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Project root heuristic: this file is at harness/checks/{name}.py
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _check_model_pointer(strategy_id: str) -> tuple[bool, str]:
    """Check if model pointer exists for strategy_id."""
    # Strategy-aware pointer path (from P0-2 model_resolver convention)
    pointer = _PROJECT_ROOT / "artifacts" / "registry" / "models" / strategy_id / "shadow.json"
    if pointer.exists():
        return (True, f"Found: {pointer}")
    # Legacy fallback for alpha_v1
    legacy = _PROJECT_ROOT / "models" / "latest_shadow_model.json"
    if strategy_id == "alpha_v1" and legacy.exists():
        return (True, f"Found: {legacy}")
    return (False, f"Missing: {pointer}")


def check_inference_ready(trade_date: str, strategy_id: str) -> list[tuple[str, bool, str]]:
    """Run all inference readiness checks. Returns list of (check_name, passed, detail)."""
    results: list[tuple[str, bool, str]] = []

    # 1. trade_date format
    try:
        datetime.strptime(trade_date, "%Y-%m-%d")
        results.append(("trade_date format", True, f"valid: {trade_date}"))
    except ValueError:
        results.append(("trade_date format", False, f"invalid: {trade_date}"))

    # 2. strategy_id non-empty
    if strategy_id:
        results.append(("strategy_id non-empty", True, strategy_id))
    else:
        results.append(("strategy_id non-empty", False, "empty"))

    # 3. Model pointer exists
    ok, detail = _check_model_pointer(strategy_id)
    results.append(("model pointer", ok, detail))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Check daily inference readiness")
    parser.add_argument("--trade-date", required=True, help="Target trade date (YYYY-MM-DD)")
    parser.add_argument("--strategy-id", required=True, help="Strategy identifier")
    args = parser.parse_args()

    results = check_inference_ready(args.trade_date, args.strategy_id)

    all_pass = True
    print(f"Daily inference readiness check: {args.trade_date} / {args.strategy_id}")
    print()
    for name, ok, detail in results:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}: {detail}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print("PASS: All checks passed.")
        return 0
    else:
        print("FAIL: Some checks failed. Review above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
