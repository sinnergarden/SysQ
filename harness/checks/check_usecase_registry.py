#!/usr/bin/env python3
"""Static check: verify use case registry structural completeness (domain-based).

Checks:
1. harness_map.yaml exists and is valid YAML.
2. Every UC in harness_map has all required fields (domain, status, ...).
3. Every UC's domain has a matching file in domains/.
4. Every active UC ID in harness_map appears as ``## UC_XXX`` in its domain file.
5. Every active UC ID in harness_map appears in 01_usecase_index.md.
6. Every ``## UC_XXX`` in domain files (unless merged/deprecated/archived)
   appears in harness_map.yaml.
7. Optional fields (supporting_tools, legacy_entrypoints, prompt_templates, notes)
   are allowed but not required.
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
DOMAINS_DIR = PROJECT_ROOT / "docs" / "requirements" / "domains"

REQUIRED_MAP_FIELDS = {
    "domain",
    "status",
    "owner_agent",
    "entrypoints",
    "artifacts",
    "checks",
    "allowed_paths",
    "forbidden_paths",
}

OPTIONAL_MAP_FIELDS = {
    "supporting_tools",
    "legacy_entrypoints",
    "prompt_templates",
    "notes",
}

SKIP_STATUSES = {"merged", "deprecated", "archived"}


def _get_status(content: str) -> str:
    """Extract the status line following ### Status or ## Status."""
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip() in ("### Status", "## Status"):
            if i + 1 < len(lines):
                return lines[i + 1].strip().lower()
    return ""


def _is_skipped_status(status_val: str) -> bool:
    for skip in SKIP_STATUSES:
        if status_val.startswith(skip):
            return True
    return False


def _extract_uc_blocks(content: str) -> dict[str, str]:
    """Return {UC_ID: section_text} for each ## UC_XXX block in a domain file."""
    blocks: dict[str, str] = {}
    sections = re.split(r"(?=^## UC_)", content, flags=re.MULTILINE)
    for sec in sections:
        m = re.match(r"^## (UC_\w+)", sec)
        if m:
            blocks[m.group(1)] = sec
    return blocks


def _load_map_usecases() -> dict:
    """Load harness_map.yaml. Returns {} on failure."""
    if not HARNESS_MAP_PATH.exists():
        return {}
    try:
        with open(HARNESS_MAP_PATH) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError:
        return {}
    if not data or "usecases" not in data:
        return {}
    return data["usecases"]


def check_harness_map() -> list[str]:
    """Check harness_map exists, is valid, has required fields."""
    violations: list[str] = []
    if not HARNESS_MAP_PATH.exists():
        violations.append(f"MISSING: {HARNESS_MAP_PATH}")
        return violations

    try:
        with open(HARNESS_MAP_PATH) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        violations.append(f"INVALID YAML: {e}")
        return violations

    if not isinstance(data, dict) or "usecases" not in data:
        violations.append(f"MISSING 'usecases' key")
        return violations

    usecases = data["usecases"]
    if not isinstance(usecases, dict):
        violations.append(f"'usecases' must be a dict")
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


def check_domain_files_exist() -> list[str]:
    """Check every UC's domain has a matching file."""
    violations: list[str] = []
    usecases = _load_map_usecases()
    if not usecases:
        return violations

    for uc_id, uc_data in usecases.items():
        domain = uc_data.get("domain", "")
        if not domain:
            violations.append(f"  {uc_id}: missing 'domain' field")
            continue
        domain_file = DOMAINS_DIR / f"{domain}.md"
        if not domain_file.exists():
            violations.append(f"  {uc_id}: domain='{domain}' but no file at domains/{domain}.md")

    return violations


def check_uc_in_domain_file() -> list[str]:
    """Check every active UC in harness_map appears in its domain file."""
    violations: list[str] = []
    usecases = _load_map_usecases()
    if not usecases:
        return violations

    for uc_id, uc_data in usecases.items():
        domain = uc_data.get("domain", "")
        domain_file = DOMAINS_DIR / f"{domain}.md"
        if not domain_file.exists():
            continue

        content = domain_file.read_text(encoding="utf-8")
        blocks = _extract_uc_blocks(content)

        if uc_id not in blocks:
            violations.append(
                f"  {uc_id}: not found as '## {uc_id}' in domains/{domain}.md"
            )

    return violations


def check_domain_ucs_in_harness_map() -> list[str]:
    """Check every active ## UC_XXX in domain files is in harness_map."""
    violations: list[str] = []

    if not DOMAINS_DIR.exists():
        return violations

    usecases = _load_map_usecases()
    map_uc_ids = set(usecases.keys())

    for md_path in sorted(DOMAINS_DIR.glob("*.md")):
        content = md_path.read_text(encoding="utf-8")
        blocks = _extract_uc_blocks(content)

        for uc_id, section_text in blocks.items():
            status = _get_status(section_text)
            if _is_skipped_status(status):
                continue
            if uc_id not in map_uc_ids:
                violations.append(
                    f"  {uc_id}: in domains/{md_path.name} (status={status}) "
                    f"but not in harness_map.yaml"
                )

    return violations


def check_usecase_index() -> list[str]:
    """Check every UC in harness_map appears in 01_usecase_index.md."""
    violations: list[str] = []

    if not USECASE_INDEX_PATH.exists():
        violations.append(f"MISSING: {USECASE_INDEX_PATH}")
        return violations

    index_content = USECASE_INDEX_PATH.read_text(encoding="utf-8")
    usecases = _load_map_usecases()

    for uc_id in usecases:
        if uc_id not in index_content:
            violations.append(f"  {uc_id}: not found in {USECASE_INDEX_PATH.name}")

    return violations


def main() -> int:
    violations: list[str] = []

    print("Checking use case registry (domain-based)...")

    violations.extend(check_harness_map())
    violations.extend(check_domain_files_exist())
    violations.extend(check_uc_in_domain_file())
    violations.extend(check_domain_ucs_in_harness_map())
    violations.extend(check_usecase_index())

    if violations:
        print(f"\n❌ Found {len(violations)} violation(s):\n")
        for v in violations:
            print(f"  {v}")
        print()
        return 1

    print("✅ Use case registry is structurally complete (domain-based).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
