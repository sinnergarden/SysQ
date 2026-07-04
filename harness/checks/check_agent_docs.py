#!/usr/bin/env python3
"""Static check: verify AI agent documentation completeness.

Checks:
1. ``AGENTS.md`` exists at repo root.
2. ``docs/agents/README.md`` exists.
3. ``docs/agents/main_agent.md`` exists and has required sections.
4. ``docs/agents/builder_agent.md`` exists and has required sections.
5. ``docs/agents/reviewer_agent.md`` exists and has required sections.
6. ``docs/agents/workspace_claude_redirect.md`` exists.
7. ``AGENTS.md`` must mention: use case, harness_map.yaml, allowed_paths,
   forbidden_paths, UC_TEMPORARY_REQUESTS.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTS = PROJECT_ROOT / "AGENTS.md"
AGENTS_DIR = PROJECT_ROOT / "docs" / "agents"

ROLE_FILES = [
    "main_agent.md",
    "builder_agent.md",
    "reviewer_agent.md",
]

REQUIRED_FILES = [
    "README.md",
    "workspace_claude_redirect.md",
    *ROLE_FILES,
]

ROLE_REQUIRED_SECTIONS = {
    "Mission",
    "开工前必读",
    "禁止",
    "交接格式",
}

AGENTS_REQUIRED_KEYWORDS = [
    "use case",
    "harness_map.yaml",
    "allowed_paths",
    "forbidden_paths",
    "UC_TEMPORARY_REQUESTS",
]


def _find_role_sections(content: str) -> set[str]:
    sections = set()
    for line in content.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m:
            sections.add(m.group(1).strip())
    return sections


def main() -> int:
    violations: list[str] = []

    if not AGENTS.exists():
        violations.append("MISSING: AGENTS.md at repo root")
    else:
        content = AGENTS.read_text(encoding="utf-8")
        for kw in AGENTS_REQUIRED_KEYWORDS:
            if kw not in content:
                violations.append(f"  AGENTS.md: missing keyword '{kw}'")

    if not AGENTS_DIR.exists():
        violations.append(f"MISSING directory: {AGENTS_DIR}")
    else:
        for fname in REQUIRED_FILES:
            fpath = AGENTS_DIR / fname
            if not fpath.exists():
                violations.append(f"MISSING: docs/agents/{fname}")

    for fname in ROLE_FILES:
        fpath = AGENTS_DIR / fname
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8")
        sections = _find_role_sections(content)
        for sec in ROLE_REQUIRED_SECTIONS:
            if sec not in sections:
                violations.append(f"  docs/agents/{fname}: missing required section '## {sec}'")

    if violations:
        print(f"❌ Found {len(violations)} agent doc violation(s):\n")
        for v in violations:
            print(f"  {v}")
        print()
        return 1

    print("✅ All agent docs are present and structurally complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
