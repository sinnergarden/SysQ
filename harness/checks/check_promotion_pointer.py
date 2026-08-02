#!/usr/bin/env python3
"""Harness check: the shadow promotion pointer must be materialized and valid.

Verifies ``data/research/promotions/shadow.yaml`` exists and passes the
schema validation used by the daily ops path.  A missing or invalid pointer
means the daily shadow line has no explicit promotion lineage — the state the
audit flagged as BLOCKER B1.

This check is intentionally SELF-CONTAINED: it does NOT import ``qsys.ops``
(whose ``__init__`` pulls in a heavy module chain that can require local
runtime config such as ``config/settings.yaml``).  It reads the YAML directly
and validates the required fields itself, so it runs on a clean checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml  # noqa: E402

POINTER_PATH = Path("data/research/promotions/shadow.yaml")

# Mirrors qsys/ops/promotion_resolver.py::_SHADOW_REQUIRED_FIELDS.
_SHADOW_REQUIRED_FIELDS = (
    "candidate_id",
    "candidate_path",
    "signal_ref.signal_id",
    "signal_ref.signal_run_id",
    "strategy_config_id",
    "strategy_template_id",
    "backtest_id",
    "strategy_run_id",
    "promoted_at",
    "promoted_by",
)


def _get_nested(data: dict, dotted: str):
    cur: object = data
    for p in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def validate_payload(payload: dict) -> list[str]:
    """Return the list of missing required fields (empty == valid)."""
    missing: list[str] = []
    for dotted in _SHADOW_REQUIRED_FIELDS:
        val = _get_nested(payload, dotted)
        if val is None or (isinstance(val, str) and not val.strip()):
            missing.append(dotted)
    return missing


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    pointer = root / POINTER_PATH

    if not pointer.exists():
        print(f"❌ Promotion pointer missing: {POINTER_PATH}")
        print("   Run: python scripts/promote_candidate.py promote ... --target shadow")
        return 1

    try:
        raw = yaml.safe_load(pointer.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Promotion pointer unreadable at {POINTER_PATH}: {e}")
        return 1
    if not isinstance(raw, dict):
        print(f"❌ Promotion pointer at {POINTER_PATH} is not a mapping")
        return 1

    missing = validate_payload(raw)
    if missing:
        print(f"❌ Promotion pointer missing required fields: {missing}")
        return 1

    # Contract checks the daily consumer relies on (resolve_shadow_promotion).
    if raw.get("artifact_type") != "shadow_promotion_pointer":
        print(f"❌ artifact_type = {raw.get('artifact_type')!r}, expected 'shadow_promotion_pointer'")
        return 1
    if raw.get("promotion_target") != "shadow":
        print(f"❌ promotion_target = {raw.get('promotion_target')!r}, expected 'shadow'")
        return 1
    cand_path = root / str(raw.get("candidate_path", ""))
    if not cand_path.exists():
        print(f"❌ candidate_path does not exist: {raw.get('candidate_path')}")
        return 1

    sr = raw.get("signal_ref") or {}
    print(f"✅ Shadow promotion pointer valid: {POINTER_PATH}")
    print(f"   candidate_id = {raw.get('candidate_id')}")
    print(f"   signal_id    = {sr.get('signal_id')}")
    print(f"   backtest_id  = {raw.get('backtest_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
