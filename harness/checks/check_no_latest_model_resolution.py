#!/usr/bin/env python3
"""Static check: forbid ``latest`` / ``mtime`` / ``symlink`` model discovery in production paths.

Scans ``qsys/strategy/``, ``qsys/ops/``, ``qsys/live/``, ``qsys/model/`` for
patterns that bypass the approved pointer mechanism:

- ``stat().st_mtime``
- ``sort(key=lambda x: x.stat().st_mtime``
- ``os.symlink`` / ``Path.symlink_to``
- Hardcoded path ``experiments/alpha_v1_models/latest``
- ``_models/latest`` in path construction
- ``"Falling back to latest model"``

The following paths are excluded (they implement or use the pointer mechanism):
- ``qsys/ops/model_resolver.py``
- ``qsys/ops/model_registry.py``
- ``qsys/ops/manifest.py``
- ``tests/``
- ``scripts/dev/``
- ``scripts/deprecated/``
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Patterns that MUST NOT appear in production paths
FORBIDDEN_PATTERNS: list[re.Pattern] = [
    re.compile(r"\.stat\(\)\.st_mtime"),
    re.compile(r"sort\(key=lambda x: x\.stat\(\)\.st_mtime"),
    re.compile(r"\bos\.symlink\b"),
    re.compile(r"\.symlink_to\("),
    re.compile(r"experiments/alpha_v1_models/latest"),
    re.compile(r"experiments/alpha_v2_models/latest"),
    re.compile(r"_models/latest"),
    re.compile(r"Falling back to latest model"),
]

# Patterns that are allowed ONLY in specific files
ALLOWED_FOR_PATTERN: dict[str, list[str]] = {
    "qsys/ops/model_resolver.py": ["_models/latest"],  # backward compat read
    "qsys/ops/model_registry.py": ["_models/latest"],  # legacy path definition
    "qsys/ops/manifest.py": ["_models/latest"],  # legacy pointer name
    # training.py sorts *report files* by mtime, not model directories
    "qsys/ops/training.py": [r"\.stat\(\)\.st_mtime", r"sort\(key=lambda item: item\.stat\(\)\.st_mtime"],
}

SCAN_DIRS = [
    "qsys/strategy",
    "qsys/ops",
    "qsys/live",
    "qsys/model",
]


def _is_whitelisted(file_rel: str) -> bool:
    for path in ("tests/", "scripts/dev/", "scripts/deprecated/"):
        if file_rel.startswith(path):
            return True
    # Also exclude harness/ from itself
    if file_rel.startswith("harness/"):
        return True
    return False


def check_file(path: Path, file_rel: str) -> list[str]:
    """Return list of violations in *path*."""
    if _is_whitelisted(file_rel):
        return []

    violations: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    allowed_patterns = ALLOWED_FOR_PATTERN.get(file_rel, [])

    for lineno, line in enumerate(lines, 1):
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(line):
                # Check if this pattern is allowed for this file
                if pattern.pattern in allowed_patterns:
                    continue
                violations.append(f"  {file_rel}:{lineno}: {pattern.pattern}")

    return violations


def main() -> int:
    violations: list[str] = []
    for scan_dir in SCAN_DIRS:
        scan_path = PROJECT_ROOT / scan_dir
        if not scan_path.exists():
            continue
        for root, _dirs, files in os.walk(scan_path):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = Path(root) / f
                file_rel = str(path.relative_to(PROJECT_ROOT))
                v = check_file(path, file_rel)
                violations.extend(v)

    if violations:
        print(f"❌ Found {len(violations)} violation(s):\n")
        for v in violations:
            print(v)
        print("\nAll model discovery must go through qsys.ops.model_resolver.")
        return 1

    print("✅ No latest/mtime/symlink patterns found in production paths.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
