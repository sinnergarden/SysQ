#!/usr/bin/env python3
"""Aggregate checker that runs signal schema, label schema, and no-lookahead.

Usage:
    python scripts/checks/check_research_artifacts.py \\
        --signal-path /tmp/signals/ \\
        --label-path /tmp/labels/ \\
        --output /tmp/check_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _run_check(module_path: str, arg_name: str, arg_value: str) -> dict:
    """Run a checker script and return its JSON result."""
    import subprocess

    result = subprocess.run(
        [sys.executable, module_path, f"--{arg_name}", arg_value],
        capture_output=True, text=True,
    )
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return {
            "status": "failed",
            "error": f"checker subprocess failed: {result.stderr.strip()}",
            "stdout": result.stdout.strip(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate research artifact checker"
    )
    parser.add_argument("--signal-path", help="Signal file or directory")
    parser.add_argument("--label-path", help="Label file or directory")
    parser.add_argument("--output", help="Path to write JSON report")
    args = parser.parse_args()

    checks_dir = Path(__file__).resolve().parent
    report: dict[str, dict] = {}

    if args.signal_path:
        report["signal_schema"] = _run_check(
            str(checks_dir / "check_signal_schema.py"), "path", args.signal_path
        )
        report["no_lookahead"] = _run_check(
            str(checks_dir / "check_no_lookahead.py"), "signal-path", args.signal_path
        )

    if args.label_path:
        report["label_schema"] = _run_check(
            str(checks_dir / "check_label_schema.py"), "path", args.label_path
        )

    overall = all(
        v.get("status") in ("passed", "degraded") for v in report.values()
    )

    output = {
        "status": "passed" if overall else "failed",
        "checks": report,
    }

    if args.output:
        Path(args.output).write_text(json.dumps(output, indent=2))

    print(json.dumps(output, indent=2))
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
