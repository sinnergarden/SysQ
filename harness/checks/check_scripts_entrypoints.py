#!/usr/bin/env python3
"""Static check: verify scripts/ top-level only contains canonical entrypoints.

Rules:
1. Read canonical entrypoints from ``docs/requirements/harness_map.yaml``.
2. Collect all ``scripts/*.py`` files that are listed as entrypoints.
3. Add a compatibility allowlist for wrappers (e.g. run_daily_batch.py).
4. Scan ``scripts/*.py`` — any file not in the allowlist is a violation.
5. Does NOT check subdirectory scripts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
HARNESS_MAP_PATH = PROJECT_ROOT / "docs" / "requirements" / "harness_map.yaml"

# Scripts that are allowed at top-level even though they aren't canonical
# entrypoints.  These are compatibility wrappers kept for backward compat.
WRAPPER_ALLOWLIST = {
    "run_daily_batch.py",
}

# Scripts that should never be at top-level regardless of allowlist.
# If they appear, suggest a move target.
DEPRECATED_SCRIPTS: dict[str, str] = {}


def _parse_entrypoint_script(entrypoint: str) -> str | None:
    """Extract the script filename from an entrypoint string.

    ``scripts/run_daily.py --mode train`` -> ``run_daily.py``
    ``scripts/data_sync.py`` -> ``data_sync.py``
    ``TBD`` -> None
    """
    entrypoint = entrypoint.strip()
    if entrypoint == "TBD" or not entrypoint.startswith("scripts/"):
        return None
    parts = entrypoint.split()
    script_path = parts[0]
    if not script_path.startswith("scripts/"):
        return None
    return script_path[len("scripts/"):]


def main() -> int:
    violations: list[str] = []

    # Build allowlist from harness_map.yaml
    allowlist: set[str] = set(WRAPPER_ALLOWLIST)

    if not HARNESS_MAP_PATH.exists():
        violations.append(f"MISSING: {HARNESS_MAP_PATH}")
    else:
        try:
            with open(HARNESS_MAP_PATH) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            violations.append(f"INVALID YAML in {HARNESS_MAP_PATH}: {e}")
            data = None

        if data and "usecases" in data:
            for uc_id, uc_data in data["usecases"].items():
                for entrypoint in uc_data.get("entrypoints", []):
                    script = _parse_entrypoint_script(entrypoint)
                    if script:
                        allowlist.add(script)

    if not SCRIPTS_DIR.exists():
        violations.append(f"MISSING: {SCRIPTS_DIR}")
    else:
        for f in sorted(SCRIPTS_DIR.iterdir()):
            if not f.is_file() or not f.name.endswith(".py") or f.name == "__init__.py":
                continue
            if f.name not in allowlist:
                violations.append(
                    f"  {f.name}: not a canonical entrypoint. "
                    f"Move to scripts/dev/, scripts/ops/, scripts/research/, "
                    f"scripts/checks/, or scripts/deprecated/"
                )

    if violations:
        print(f"❌ Found {len(violations)} scripting entrypoint violation(s):\n")
        for v in violations:
            print(f"  {v}")
        print()
        print("Canonical top-level entrypoints are defined in:")
        print("  docs/requirements/harness_map.yaml")
        print("Compatibility wrappers (not canonical):")
        for w in sorted(WRAPPER_ALLOWLIST):
            print(f"  - {w}")
        return 1

    print("✅ All top-level scripts are canonical entrypoints or allowed wrappers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
