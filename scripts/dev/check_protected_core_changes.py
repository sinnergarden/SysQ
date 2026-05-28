#!/usr/bin/env python3
"""
Check git diff against Protected Core path patterns.

Warns when a diff includes changes to Protected Core modules
(defined in ADR-005). Output is advisory — does not block CI.

Usage:
    python scripts/dev/check_protected_core_changes.py
    python scripts/dev/check_protected_core_changes.py --ref origin/main
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


# ADR-005 Protected Core path patterns (prefix matches)
PROTECTED_CORE_PATTERNS: list[str] = [
    "qsys/data/",
    "qsys/ledger/",
    "qsys/backtest/",
    "qsys/trader/account.py",
    "qsys/trader/matcher.py",
    "qsys/ops/run_archive/",
    "qsys/broker/",
    "scripts/run_daily.py",
    "scripts/ops/",
]


def _git_diff_files(ref: str | None = None) -> list[str]:
    """Return list of changed file paths from git diff."""
    cmd = ["git", "diff", "--name-only"]
    if ref:
        cmd.append(ref)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, cwd=PROJECT_ROOT)
        return [f.strip() for f in result.stdout.splitlines() if f.strip()]
    except subprocess.CalledProcessError as e:
        print(f"⚠  git diff failed: {e.stderr.strip()}", file=sys.stderr)
        return []


def _is_protected(filepath: str) -> bool:
    """Check if a filepath matches any Protected Core pattern."""
    for pattern in PROTECTED_CORE_PATTERNS:
        if filepath.startswith(pattern):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check git diff against Protected Core path patterns (ADR-005)"
    )
    parser.add_argument(
        "--ref", default=None,
        help="Git ref to diff against (e.g. origin/main). Default: unstaged changes."
    )
    args = parser.parse_args()

    changed_files = _git_diff_files(args.ref)

    if not changed_files:
        print("✅ No changed files detected.")
        return

    protected_changes: list[str] = []
    other_changes: list[str] = []

    for fp in changed_files:
        if _is_protected(fp):
            protected_changes.append(fp)
        else:
            other_changes.append(fp)

    if other_changes:
        print(f"\n📦 Non-protected changes ({len(other_changes)}):")
        for fp in other_changes:
            print(f"   {fp}")

    if protected_changes:
        sep = "!" * 60
        print(f"\n{sep}")
        print(f"⚠  PROTECTED CORE CHANGES DETECTED ({len(protected_changes)}):")
        print(sep)
        for fp in protected_changes:
            print(f"   🔒 {fp}")
        print()
        print("PR must include (per ADR-005):")
        print("   1. Core Change Reason — why must Protected Core be modified")
        print("   2. Semantic Impact — does this change matcher/ledger/backtest behavior?")
        print("   3. Regression Tests — test results for affected modules")
        print("   4. Rollback Plan — how to revert in production")
        sys.exit(0)
    else:
        print(f"\n✅ No Protected Core changes detected. ({len(other_changes)} non-protected files changed)")


if __name__ == "__main__":
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    os.chdir(PROJECT_ROOT)
    main()
