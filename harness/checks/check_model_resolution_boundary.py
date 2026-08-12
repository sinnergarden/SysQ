#!/usr/bin/env python3
"""Static check: verify production paths use ``model_resolver``.

Checks:
- ``qsys/strategy/alpha_v1/adapter.py`` must import ``resolve_model_for_strategy``
- ``qsys/strategy/alpha_v2/adapter.py`` must import ``resolve_model_for_strategy``
- ``qsys/ops/daily_runner.py`` must call ``resolve_model_for_strategy``
- ``qsys/live/scheduler.py`` must NOT define ``find_latest_model`` with mtime logic
- ``qsys/live/scheduler.py`` must NOT have ``fallback to latest`` pattern
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_RESOLVER_USE: dict[str, str] = {
    "qsys/strategy/alpha_v1/adapter.py": "resolve_model_for_strategy",
    "qsys/ops/daily_runner.py": "resolve_model_for_strategy",
}

# daily_runner's _resolve_model_path must NOT have a fallback path after
# calling resolve_model_for_strategy (no except, no ctx.run_root return).
FORBIDDEN_IN_DAILY_RUNNER_RESOLVE: list[str] = [
    "ctx.run_root",
    "except FileNotFoundError",
    "except Exception",
]

FORBIDDEN_IN_SCHEDULER: list[str] = [
    ".stat().st_mtime",
    "sort(key=lambda x: x.stat().st_mtime",
    "Falling back to latest model",
    "fallback to latest model",
]


def check_file_imports(path: Path, file_rel: str, expected_symbol: str) -> str | None:
    """Check that *path* imports or references *expected_symbol*."""
    if not path.exists():
        return f"  {file_rel}: FILE NOT FOUND"
    content = path.read_text(encoding="utf-8")
    if expected_symbol in content:
        return None
    return f"  {file_rel}: expected to use '{expected_symbol}'"


def check_file_forbidden(path: Path, file_rel: str, patterns: list[str]) -> list[str]:
    """Check that *path* does NOT contain any forbidden pattern."""
    violations: list[str] = []
    if not path.exists():
        return [f"  {file_rel}: FILE NOT FOUND"]
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    for lineno, line in enumerate(lines, 1):
        for pattern in patterns:
            if pattern in line:
                violations.append(f"  {file_rel}:{lineno}: {pattern}")
    return violations


def main() -> int:
    violations: list[str] = []

    # 1. Check adapters and daily_runner import resolver
    for file_rel, expected_symbol in EXPECTED_RESOLVER_USE.items():
        path = PROJECT_ROOT / file_rel
        violation = check_file_imports(path, file_rel, expected_symbol)
        if violation:
            violations.append(violation)

    # 2. Check scheduler for forbidden patterns
    scheduler_rel = "qsys/live/scheduler.py"
    scheduler_path = PROJECT_ROOT / scheduler_rel
    scheduler_violations = check_file_forbidden(scheduler_path, scheduler_rel, FORBIDDEN_IN_SCHEDULER)
    violations.extend(scheduler_violations)

    # 3. Check scheduler DOES still resolve via model_resolver
    scheduler_content = scheduler_path.read_text(encoding="utf-8")
    if "resolve_model_for_strategy" not in scheduler_content:
        violations.append(f"  {scheduler_rel}: must use resolve_model_for_strategy")

    # 4. Check find_latest_model is NOT a mtime-based implementation
    if "stat().st_mtime" in scheduler_content:
        violations.append(f"  {scheduler_rel}: find_latest_model must not use mtime sorting")

    # 5. Check daily_runner._resolve_model_path has no fallback
    dr_rel = "qsys/ops/daily_runner.py"
    dr_path = PROJECT_ROOT / dr_rel
    dr_content = dr_path.read_text(encoding="utf-8")
    if "_resolve_model_path" in dr_content:
        # Find the method body and check for forbidden patterns
        in_resolve = False
        for lineno, line in enumerate(dr_content.splitlines(), 1):
            if "def _resolve_model_path" in line:
                in_resolve = True
                continue
            if in_resolve and line.startswith("    ") and "def " in line:
                in_resolve = False
            if in_resolve:
                for pattern in FORBIDDEN_IN_DAILY_RUNNER_RESOLVE:
                    if pattern in line:
                        violations.append(
                            f"  {dr_rel}:{lineno}: _resolve_model_path must not "
                            f"contain '{pattern}' (no fallback allowed)"
                        )

    if violations:
        print(f"❌ Found {len(violations)} boundary violation(s):\n")
        for v in violations:
            print(v)
        return 1

    print("✅ All production paths correctly use model_resolver.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
