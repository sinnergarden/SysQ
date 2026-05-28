#!/usr/bin/env python3
"""Classify changed files into risk categories.

Input:
  --changed-files <path>   A text file with one changed file path per line
                           (e.g. from ``git diff --name-only > /tmp/changes.txt``).

Output:
  JSON with categorized file lists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RED_ZONE_PATTERNS: list[str] = [
    "qsys/data/calendar",
    "qsys/ops/daily_runner",
    "qsys/trader/matcher",
    "qsys/trader/diff",
    "qsys/ledger/",
    "qsys/ops/mtm",
    "qsys/ops/commit_guard",
    "qsys/backtest/engine",
]

RESEARCH_ARTIFACT_PATTERNS: list[str] = [
    "qsys/label/",
    "qsys/signal/",
    "qsys/research/paths",
    "qsys/research/manifest",
    "scripts/checks/",
]

STRATEGY_PATTERNS: list[str] = [
    "qsys/strategy/",
    "configs/strategies/",
]

DOCS_PATTERNS: list[str] = [
    "docs/",
]

TEST_PATTERNS: list[str] = [
    "tests/",
]


def _classify(file_path: str) -> list[str]:
    categories: list[str] = []
    for cat, patterns in [
        ("red_zone", RED_ZONE_PATTERNS),
        ("research_artifact", RESEARCH_ARTIFACT_PATTERNS),
        ("strategy", STRATEGY_PATTERNS),
        ("docs_only", DOCS_PATTERNS),
        ("tests", TEST_PATTERNS),
    ]:
        for pat in patterns:
            if pat in file_path:
                categories.append(cat)
                break
    if not categories:
        categories.append("other")
    return categories


def check_pr_scope(changed_files: list[str]) -> dict:
    result: dict[str, list[str]] = {
        "red_zone": [],
        "research_artifact": [],
        "strategy": [],
        "docs_only": [],
        "tests": [],
        "scripts": [],
        "other": [],
    }

    for f in changed_files:
        cats = _classify(f)
        for c in cats:
            if c == "tests":
                result["tests"].append(f)
            elif c == "docs_only":
                result["docs_only"].append(f)
            elif c == "research_artifact":
                result["research_artifact"].append(f)
            elif c == "strategy":
                result["strategy"].append(f)
            elif c == "red_zone":
                result["red_zone"].append(f)
            else:
                result["other"].append(f)

    # Also place scripts/ into their own bucket
    for f in changed_files:
        if f.startswith("scripts/") and f not in (
            result["red_zone"] + result["research_artifact"]
        ):
            result["scripts"].append(f)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify PR changed files into risk categories"
    )
    parser.add_argument(
        "--changed-files",
        nargs="?",
        const=None,
        help="Path to file with one changed file path per line",
    )
    args = parser.parse_args()

    if not args.changed_files:
        print("Usage: python scripts/checks/check_pr_scope.py --changed-files <path>")
        print()
        print("Example:")
        print("  git diff --name-only main...HEAD > /tmp/changes.txt")
        print("  python scripts/checks/check_pr_scope.py --changed-files /tmp/changes.txt")
        sys.exit(0)

    path = Path(args.changed_files)
    if not path.exists():
        print(json.dumps({"error": f"File not found: {path}"}, indent=2))
        sys.exit(1)

    lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
    result = check_pr_scope(lines)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
