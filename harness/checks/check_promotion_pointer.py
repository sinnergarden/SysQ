#!/usr/bin/env python3
"""Harness check: the shadow promotion pointer must be materialized and valid.

Verifies ``data/research/promotions/shadow.yaml`` exists and passes the
schema validation used by the daily ops path (``resolve_shadow_promotion``).
A missing or invalid pointer means the daily shadow line has no explicit
promotion lineage — the state the audit flagged as BLOCKER B1.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import yaml  # noqa: E402

from qsys.ops.promotion_resolver import (  # noqa: E402
    resolve_shadow_promotion,
    validate_shadow_promotion_payload,
)

POINTER_PATH = Path("data/research/promotions/shadow.yaml")


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    pointer = root / POINTER_PATH

    if not pointer.exists():
        print(f"❌ Promotion pointer missing: {POINTER_PATH}")
        print("   Run: python scripts/promote_candidate.py promote ... --target shadow")
        return 1

    # Schema-validate the raw nested YAML (dotted keys like signal_ref.signal_id).
    try:
        raw = yaml.safe_load(pointer.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Promotion pointer unreadable at {POINTER_PATH}: {e}")
        return 1
    missing = validate_shadow_promotion_payload(raw) if isinstance(raw, dict) else ["<not a mapping>"]
    if missing:
        print(f"❌ Promotion pointer missing required fields: {missing}")
        return 1

    # Resolve (also re-validates) and print lineage summary.
    try:
        payload = resolve_shadow_promotion(pointer)
    except Exception as e:
        print(f"❌ Promotion pointer invalid at {POINTER_PATH}: {e}")
        return 1

    print(f"✅ Shadow promotion pointer valid: {POINTER_PATH}")
    print(f"   candidate_id = {payload.get('candidate_id')}")
    print(f"   signal_id    = {payload.get('signal_id')}")
    print(f"   backtest_id  = {payload.get('backtest_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
