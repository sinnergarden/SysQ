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

import hashlib
import json
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
    "runtime_binding.strategy_id",
    "runtime_binding.strategy_config_path",
    "runtime_binding.strategy_config_sha256",
    "runtime_binding.model_ref.mode",
    "runtime_binding.model_ref.model_id",
    "runtime_binding.model_ref.model_path",
    "runtime_binding.model_ref.artifact_hash",
    "runtime_binding.model_ref.pointer_path",
    "runtime_binding.model_ref.pointer_sha256",
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


def _model_artifact_hash(model_dir: Path) -> str:
    if model_dir.is_symlink():
        raise ValueError(f"model path must not be a symlink: {model_dir}")
    files = sorted(path for path in model_dir.rglob("*") if path.is_file())
    if not files:
        raise ValueError(f"model artifact directory is empty: {model_dir}")
    digest = hashlib.sha256()
    for path in files:
        if path.is_symlink():
            raise ValueError(f"model artifact contains symlink: {path}")
        digest.update(path.relative_to(model_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


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

    # ── Lineage cross-validation (promotion domain requires the signal /
    #    backtest IDs to resolve to the candidate).  Reading the candidate
    #    YAML and comparing key fields catches pointers whose IDs are fake
    #    even though the candidate file itself exists. ──
    try:
        cand = yaml.safe_load(cand_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ candidate unreadable at {cand_path}: {e}")
        return 1
    if not isinstance(cand, dict):
        print(f"❌ candidate at {cand_path} is not a mapping")
        return 1

    cand_sr = cand.get("signal_ref") or {}
    cand_br = cand.get("backtest_ref") or {}
    cand_st = cand.get("strategy") or {}
    sr = raw.get("signal_ref") or {}
    mismatches = []
    if cand.get("candidate_id") != raw.get("candidate_id"):
        mismatches.append("candidate_id")
    if cand_sr.get("signal_id") != sr.get("signal_id"):
        mismatches.append("signal_ref.signal_id")
    if cand_sr.get("signal_run_id") != sr.get("signal_run_id"):
        mismatches.append("signal_ref.signal_run_id")
    if cand_br.get("backtest_id") != raw.get("backtest_id"):
        mismatches.append("backtest_id")
    if cand_br.get("strategy_run_id") != raw.get("strategy_run_id"):
        mismatches.append("strategy_run_id")
    if cand_st.get("strategy_config_id") != raw.get("strategy_config_id"):
        mismatches.append("strategy_config_id")
    if cand_st.get("strategy_template_id") != raw.get("strategy_template_id"):
        mismatches.append("strategy_template_id")
    if mismatches:
        print(f"❌ Promotion lineage mismatch vs candidate {raw.get('candidate_path')}: {mismatches}")
        return 1

    runtime = raw.get("runtime_binding") or {}
    if cand.get("runtime_binding") != runtime:
        print("❌ Promotion runtime_binding does not match candidate")
        return 1
    strategy_id = runtime.get("strategy_id")
    if cand_st.get("strategy_id") != strategy_id:
        print("❌ Promotion strategy_id does not match candidate strategy")
        return 1

    config_path = (root / str(runtime.get("strategy_config_path", ""))).resolve()
    try:
        config_path.relative_to(root.resolve())
    except ValueError:
        print(f"❌ strategy config escapes project root: {config_path}")
        return 1
    if not config_path.is_file():
        print(f"❌ strategy config does not exist: {config_path}")
        return 1
    if hashlib.sha256(config_path.read_bytes()).hexdigest() != runtime.get(
        "strategy_config_sha256"
    ):
        print(f"❌ strategy config hash mismatch: {config_path}")
        return 1
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if config.get("strategy_id") != strategy_id:
        print(f"❌ strategy config identity mismatch: {config_path}")
        return 1

    model_ref = runtime.get("model_ref") or {}
    pointer_path = (root / str(model_ref.get("pointer_path", ""))).resolve()
    try:
        pointer_path.relative_to(root.resolve())
    except ValueError:
        print(f"❌ model pointer escapes project root: {pointer_path}")
        return 1
    if not pointer_path.is_file():
        print(f"❌ model pointer does not exist: {pointer_path}")
        return 1
    if hashlib.sha256(pointer_path.read_bytes()).hexdigest() != model_ref.get(
        "pointer_sha256"
    ):
        print(f"❌ model pointer hash mismatch: {pointer_path}")
        return 1
    try:
        model_pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"❌ model pointer unreadable: {pointer_path}: {exc}")
        return 1
    pointer_fields = {
        "schema_version": 2,
        "strategy_id": strategy_id,
        "mode": "shadow",
        "status": "approved",
        "model_id": model_ref.get("model_id"),
        "model_path": model_ref.get("model_path"),
        "artifact_hash": model_ref.get("artifact_hash"),
    }
    bad_pointer_fields = [
        key for key, value in pointer_fields.items()
        if model_pointer.get(key) != value
    ]
    if bad_pointer_fields:
        print(f"❌ model pointer binding mismatch: {bad_pointer_fields}")
        return 1

    model_relative = Path(str(model_ref.get("model_path", "")))
    unresolved_model_dir = root / model_relative
    current = root.resolve()
    for part in model_relative.parts:
        current = current / part
        if current.is_symlink():
            print(f"❌ model path contains symlink component: {current}")
            return 1
    model_dir = unresolved_model_dir.resolve()
    try:
        model_dir.relative_to(root.resolve())
    except ValueError:
        print(f"❌ model artifact escapes project root: {model_dir}")
        return 1
    if model_dir.exists():
        try:
            actual_model_hash = _model_artifact_hash(model_dir)
        except ValueError as exc:
            print(f"❌ invalid model artifact: {exc}")
            return 1
        if actual_model_hash != model_ref.get("artifact_hash"):
            print(f"❌ model artifact hash mismatch: {model_dir}")
            return 1
    else:
        print(
            f"⚠ model artifact is external to git checkout; runtime will require: "
            f"{model_dir}"
        )

    print(f"✅ Shadow promotion pointer valid: {POINTER_PATH}")
    print(f"   candidate_id = {raw.get('candidate_id')}")
    print(f"   signal_id    = {sr.get('signal_id')}")
    print(f"   backtest_id  = {raw.get('backtest_id')}")
    print(f"   strategy_id  = {strategy_id}")
    print(f"   model_id     = {model_ref.get('model_id')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
