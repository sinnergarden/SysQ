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


def _extract_lesson_blocks(content: str) -> list[tuple[str, str]]:
    """Extract accepted lesson blocks. Returns [(LM-001, full_block_text), ...].

    Uses positional slicing so duplicate LM-IDs are detected — dict would
    silently overwrite them.
    """
    lessons: list[tuple[str, str]] = []
    matches = list(re.finditer(r"^### (LM-\d+):?.*$", content, flags=re.MULTILINE))
    for idx, match in enumerate(matches):
        lesson_id = match.group(1)
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        lessons.append((lesson_id, content[start:end]))
    return lessons


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
    for prefix in ("- **Status**:", "- Status:"):
        if prefix in block:
            # Extract the text after the prefix
            for line in block.splitlines():
                if line.startswith(prefix):
                    val = line[len(prefix):].strip()
                    return val.lower() if val else ""
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
    if not lessons:
        violations.append("No accepted lessons found under '## Accepted Lessons'")

    seen_ids: set[str] = set()
    for lid, block in lessons:
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
