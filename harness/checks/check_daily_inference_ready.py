#!/usr/bin/env python3
"""CLI harness for the shared fail-closed daily inference readiness gate."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qsys.ops.inference_readiness import check_inference_ready


def main() -> int:
    parser = argparse.ArgumentParser(description="Check daily inference readiness")
    parser.add_argument(
        "--trade-date",
        required=True,
        help="Signal/data date (YYYY-MM-DD or auto); retained name for compatibility",
    )
    parser.add_argument("--execution-date", help="Expected next open session")
    parser.add_argument("--strategy-id", required=True, help="Strategy identifier")
    args = parser.parse_args()

    run_anchor = datetime.now(timezone.utc)
    results = check_inference_ready(
        args.trade_date,
        args.strategy_id,
        execution_date=args.execution_date,
        project_root=PROJECT_ROOT,
        now=run_anchor,
    )
    ready = all(ok for _name, ok, _detail in results)
    print(
        f"Daily inference readiness: {'READY' if ready else 'BLOCKED'} "
        f"({args.trade_date} / {args.strategy_id})\n"
    )
    for name, ok, detail in results:
        print(f"  {'✅' if ok else '❌'} {name}: {detail}")
    return 0 if ready else 2


if __name__ == "__main__":
    sys.exit(main())
