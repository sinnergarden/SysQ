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
    "harness_map.yaml",
    "EXECUTION_GATE",
    "UC_DAILY_INFERENCE_RUN",
    "check_inference_artifact.py",
    "LOOP_CHECK",
    "REVIEWER_TRIGGER",
    "OUTPUT CONTRACT",
    "不可越界规则",
]

def _skill_dirs_from_harness_map() -> list[str] | None:
    """Derive every skill referenced in harness_map.yaml.

    Returns ``None`` when the map cannot be parsed (missing PyYAML or a
    malformed file) so the check FAILS CLOSED instead of silently validating
    only ``sysq-daily`` — a missing skill like sysq-dev must not slip through.
    """
    try:
        import yaml
    except ImportError:
        return None
    map_path = PROJECT_ROOT / "docs" / "requirements" / "harness_map.yaml"
    try:
        data = yaml.safe_load(map_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    dirs: list[str] = []
    for uc in (data or {}).get("usecases", {}).values():
        skills = uc.get("skills") or {}
        for role in ("primary", "review", "dev"):
            s = skills.get(role)
            if isinstance(s, str) and s and s not in dirs:
                dirs.append(s)
    return dirs


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

    # Skills — derived from harness_map.yaml, fail-closed on parse error.
    skill_dirs = _skill_dirs_from_harness_map()
    if skill_dirs is None:
        violations.append(
            "  cannot parse harness_map.yaml skills (fail-closed): "
            "missing PyYAML or malformed map"
        )
    else:
        for sdir in skill_dirs:
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
