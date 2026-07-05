#!/usr/bin/env python3
"""Check loop_memory.md structural integrity.

Requirements:
1. File exists at docs/agents/loop_memory.md.
2. ``## Accepted Lessons`` section exists.
3. Each accepted lesson uses heading ``### LM-NNN: title``.
4. LM IDs are unique.
5. Each lesson has fields: Status, Trigger, Failure type, Root cause, Fix, Validation.
6. Status value must be ``accepted``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MEMORY_PATH = PROJECT_ROOT / "docs" / "agents" / "loop_memory.md"

REQUIRED_LESSON_FIELDS = {
    "Status",
    "Trigger",
    "Failure type",
    "Root cause",
    "Fix",
    "Validation",
}


def _extract_lesson_blocks(content: str) -> dict[str, str]:
    """Extract each accepted lesson block. Returns {LM-001: full_block_text}."""
    blocks: dict[str, str] = {}
    # Split by ### LM- headers
    sections = re.split(r"(?=^### (LM-\d+))", content, flags=re.MULTILINE)
    for i, sec in enumerate(sections):
        m = re.match(r"^### (LM-\d+):?\s*(.*)", sec, re.MULTILINE)
        if m:
            blocks[m.group(1).strip()] = sec
    return blocks


def _has_fields(block: str) -> list[str]:
    """Check that a lesson block contains all required fields."""
    missing: list[str] = []
    for field in REQUIRED_LESSON_FIELDS:
        if f"- **{field}**:" in block:
            continue
        if f"- {field}:" in block:
            continue
        missing.append(field)
    return missing


def _get_status(block: str) -> str:
    """Extract Status value from lesson block."""
    m = re.search(r"^- (?:Status|\\*\\*Status\\*\\*):\s*(.*)", block, re.MULTILINE)
    if m:
        return m.group(1).strip().lower()
    return ""


def main() -> int:
    violations: list[str] = []

    if not MEMORY_PATH.exists():
        violations.append(f"MISSING: {MEMORY_PATH}")
        print(f"❌ Found 1 violation(s):\n  {violations[0]}\n")
        return 1

    content = MEMORY_PATH.read_text(encoding="utf-8")

    # 2. Accepted Lessons section exists
    if "## Accepted Lessons" not in content:
        violations.append("Missing '## Accepted Lessons' section")

    # 3-4. Extract and validate lessons
    lessons = _extract_lesson_blocks(content)
    seen_ids: set[str] = set()
    for lid, block in lessons.items():
        if lid in seen_ids:
            violations.append(f"Duplicate lesson ID: {lid}")
        seen_ids.add(lid)

        # Check required fields
        missing = _has_fields(block)
        if missing:
            violations.append(f"  {lid}: missing fields: {', '.join(missing)}")

        # Check status
        status = _get_status(block)
        if status and status != "accepted":
            violations.append(f"  {lid}: status must be 'accepted', got '{status}'")

    if violations:
        print(f"❌ Found {len(violations)} loop memory violation(s):\n")
        for v in violations:
            print(f"  {v}")
        print()
        return 1

    print("✅ loop memory structure is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
