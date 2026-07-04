#!/usr/bin/env python3
"""Static check: verify use case registry structural completeness.

Checks:
1. ``docs/requirements/harness_map.yaml`` exists and is valid YAML.
2. Every use case in the harness map has all required fields.
3. Every ``docs/requirements/usecases/uc_*.md`` has all required sections.
4. ``docs/requirements/01_usecase_index.md`` lists every use case ID.
5. TBD is acceptable but must be explicit — missing fields are not allowed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]

HARNESS_MAP_PATH = PROJECT_ROOT / "docs" / "requirements" / "harness_map.yaml"
USECASE_INDEX_PATH = PROJECT_ROOT / "docs" / "requirements" / "01_usecase_index.md"
USECASES_DIR = PROJECT_ROOT / "docs" / "requirements" / "usecases"

# Fields every use case entry in harness_map.yaml must have
REQUIRED_MAP_FIELDS = {
    "status",
    "owner_agent",
    "entrypoints",
    "artifacts",
    "checks",
    "allowed_paths",
    "forbidden_paths",
}

# Sections every uc_*.md must have
REQUIRED_SECTIONS = {
    "Status",
    "User Goal",
    "Scope",
    "Inputs",
    "Outputs",
    "Canonical Entrypoints",
    "Key Artifacts",
    "Required Checks",
    "Owner Agent",
    "Allowed Paths",
    "Forbidden Paths",
    "Open Questions",
}


def _find_sections(content: str) -> set[str]:
    """Extract markdown section headings (## or higher) from content."""
    sections = set()
    for line in content.splitlines():
        m = re.match(r"^#{1,3}\s+(.*)", line)
        if m:
            sections.add(m.group(1).strip())
    return sections


def check_harness_map() -> list[str]:
    """Check harness_map.yaml exists, is valid, and has required fields."""
    violations: list[str] = []

    if not HARNESS_MAP_PATH.exists():
        violations.append(f"MISSING: {HARNESS_MAP_PATH}")
        return violations

    try:
        with open(HARNESS_MAP_PATH) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        violations.append(f"INVALID YAML in {HARNESS_MAP_PATH}: {e}")
        return violations

    if not isinstance(data, dict) or "usecases" not in data:
        violations.append(f"MISSING 'usecases' key in {HARNESS_MAP_PATH}")
        return violations

    usecases = data["usecases"]
    if not isinstance(usecases, dict):
        violations.append(f"'usecases' must be a dict in {HARNESS_MAP_PATH}")
        return violations

    for uc_id, uc_data in usecases.items():
        if not isinstance(uc_data, dict):
            violations.append(f"  {uc_id}: must be a dict")
            continue
        for field in REQUIRED_MAP_FIELDS:
            if field not in uc_data:
                violations.append(f"  {uc_id}: missing required field '{field}'")
            elif uc_data[field] is None:
                violations.append(f"  {uc_id}: field '{field}' is None (use 'TBD' if unknown)")

    return violations


def check_usecase_md_files() -> list[str]:
    """Check every uc_*.md has all required sections."""
    violations: list[str] = []

    if not USECASES_DIR.exists():
        violations.append(f"MISSING directory: {USECASES_DIR}")
        return violations

    md_files = sorted(USECASES_DIR.glob("uc_*.md"))
    if not md_files:
        violations.append(f"No uc_*.md files found in {USECASES_DIR}")
        return violations

    for md_path in md_files:
        try:
            content = md_path.read_text(encoding="utf-8")
        except Exception as e:
            violations.append(f"  {md_path.name}: cannot read — {e}")
            continue

        sections = _find_sections(content)

        uc_id = md_path.stem  # e.g. "uc_daily_ops"
        for req_sec in REQUIRED_SECTIONS:
            # Support both "## Status" and "### Status"
            if req_sec not in sections:
                violations.append(f"  {uc_id}: missing required section '## {req_sec}'")

    return violations


def check_usecase_index() -> list[str]:
    """Check 01_usecase_index.md lists every use case ID."""
    violations: list[str] = []

    if not USECASE_INDEX_PATH.exists():
        violations.append(f"MISSING: {USECASE_INDEX_PATH}")
        return violations

    index_content = USECASE_INDEX_PATH.read_text(encoding="utf-8")

    if not USECASES_DIR.exists():
        return violations

    for md_path in USECASES_DIR.glob("uc_*.md"):
        uc_id = md_path.stem  # e.g. "uc_daily_ops"
        uc_id_upper = uc_id.upper()  # e.g. "UC_DAILY_OPS"
        # Check either form appears in index
        if uc_id not in index_content and uc_id_upper not in index_content:
            violations.append(f"  {uc_id}/{uc_id_upper}: not listed in {USECASE_INDEX_PATH.name}")

    return violations


def main() -> int:
    violations: list[str] = []

    print("Checking use case registry...")

    v = check_harness_map()
    violations.extend(v)

    v = check_usecase_md_files()
    violations.extend(v)

    v = check_usecase_index()
    violations.extend(v)

    if violations:
        print(f"\n❌ Found {len(violations)} violation(s):\n")
        for v in violations:
            print(f"  {v}")
        print()
        return 1

    print("✅ Use case registry is structurally complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
