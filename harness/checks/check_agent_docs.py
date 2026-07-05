#!/usr/bin/env python3
"""Static check: verify AI agent documentation completeness (skill-first + harness-first + loop).

Checks:
1. AGENTS.md exists and contains required keywords.
2. docs/agents/*.md files exist (README, role docs, loop docs).
3. Role docs have required sections.
4. Skill file at .claude/skills/sysq-daily/SKILL.md exists.
5. Reviewer subagent at .claude/agents/sysq-reviewer.md exists.
6. Loop memory at docs/agents/loop_memory.md exists.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENTS = PROJECT_ROOT / "AGENTS.md"
AGENTS_DIR = PROJECT_ROOT / "docs" / "agents"
SKILLS_DIR = PROJECT_ROOT / ".claude" / "skills"
AGENTS_SUBDIR = PROJECT_ROOT / ".claude" / "agents"

ROLE_FILES = [
    "main_agent.md",
    "builder_agent.md",
    "reviewer_agent.md",
]

REQUIRED_FILES = [
    "README.md",
    "workspace_claude_redirect.md",
    "SYSQ_LOOP_ENGINEERING.md",
    "loop_memory.md",
    *ROLE_FILES,
]

ROLE_REQUIRED_SECTIONS = {
    "Mission",
    "开工前必读",
    "禁止",
    "交接格式",
}

AGENTS_REQUIRED_KEYWORDS = [
    "skill-first",
    "sysq-daily",
    "Subagent Policy",
    "Harness-First",
    "harness_map.yaml",
    "Improvement Loop",
    "loop_memory.md",
    "sysq-reviewer",
]

SKILL_DIRS = [
    "sysq-daily",
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

    # AGENTS.md
    if not AGENTS.exists():
        violations.append("MISSING: AGENTS.md at repo root")
    else:
        content = AGENTS.read_text(encoding="utf-8")
        for kw in AGENTS_REQUIRED_KEYWORDS:
            if kw not in content:
                violations.append(f"  AGENTS.md: missing required keyword '{kw}'")

    # docs/agents/ files
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

    # Skills
    for sdir in SKILL_DIRS:
        skill_path = SKILLS_DIR / sdir / "SKILL.md"
        if not skill_path.exists():
            violations.append(f"MISSING: .claude/skills/{sdir}/SKILL.md")

    # Reviewer subagent
    reviewer_agent = AGENTS_SUBDIR / "sysq-reviewer.md"
    if not reviewer_agent.exists():
        violations.append(f"MISSING: .claude/agents/sysq-reviewer.md")

    if violations:
        print(f"❌ Found {len(violations)} agent doc violation(s):\n")
        for v in violations:
            print(f"  {v}")
        print()
        return 1

    print("✅ All agent docs are present and structurally complete (skill-first + loop).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
