from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


# ── Run ID ────────────────────────────────────────────────────────────
# Format: {trade_date}_{mode}_{account_id}_{seq}
# Example: 20260517_paper_default_001


def build_run_id(trade_date: str, mode: str, account_id: str, seq: int = 1) -> str:
    return f"{trade_date}_{mode}_{account_id}_{seq:03d}"


# ── Run manifest schema ───────────────────────────────────────────────

MANIFEST_KEYS = frozenset({
    "run_id",
    "trade_date",
    "mode",
    "account_id",
    "model_path",
    "feature_set",
    "top_k",
    "universe",
    "created_at",
})


# ── DataFrame schema constants ────────────────────────────────────────
# These define the canonical column sets for each frame-like artifact.
# They act as an adapter/validator layer: old modules continue producing
# their current output shapes, and contracts validate/transform at the
# boundary.

PREDICTION_FRAME_COLUMNS = [
    "instrument",
    "date",
    "score",
]

TARGET_PORTFOLIO_COLUMNS = [
    "instrument",
    "target_weight",
    "score",
    "rank",
]

# Mirrors reconciliation.STANDARD_PLAN_COLUMNS — the canonical shape
# that both plan CSV and order intents are expected to align with.
TRADE_PLAN_COLUMNS = [
    "symbol",
    "side",
    "amount",
    "price",
    "est_value",
    "weight",
    "score",
    "score_rank",
    "target_value",
    "current_value",
    "diff_value",
    "weight_method",
    "plan_role",
    "execution_bucket",
    "cash_dependency",
    "t1_rule",
    "account_name",
    "signal_date",
    "plan_date",
    "execution_date",
    "price_basis_date",
    "price_basis_field",
    "price_basis_label",
    "status",
    "filled_amount",
    "filled_price",
    "fee",
    "tax",
    "total_cost",
    "order_id",
    "note",
]


# ── ExecutionResult (object-like contract) ────────────────────────────

@dataclass
class ExecutionResult:
    """Canonical representation of a single simulated execution.

    This bridges the gap between plan order intents and reconciliation:
    every intent_id present here is expected to appear in the plan CSV,
    and the aggregated results feed directly into position reconciliation.
    """
    intent_id: str
    trade_date: str
    account_id: str
    symbol: str
    side: str                     # buy / sell
    requested_quantity: int       # shares (A-share convention)
    filled_quantity: int
    avg_price: float
    status: str                   # filled / partial_fill / rejected
    reject_reason: str | None = None
    fees: float = 0.0
    source_run_id: str = ""


# ── Manifest dataclass (optional, for type-safe construction) ─────────

@dataclass
class RunManifest:
    run_id: str
    trade_date: str
    mode: str
    account_id: str
    model_path: str = ""
    feature_set: str = ""
    top_k: int = 0
    universe: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trade_date": self.trade_date,
            "mode": self.mode,
            "account_id": self.account_id,
            "model_path": self.model_path,
            "feature_set": self.feature_set,
            "top_k": self.top_k,
            "universe": self.universe,
            "created_at": self.created_at,
        }


# ── Validation helpers ────────────────────────────────────────────────


def validate_plan_df(df: pd.DataFrame, *, name: str = "plan") -> list[str]:
    """Return a list of missing-column warnings for a plan DataFrame.

    Returns an empty list when the frame conforms to TRADE_PLAN_COLUMNS
    (allowing extra columns).  Callers decide whether to warn or reject.
    """
    missing = [c for c in TRADE_PLAN_COLUMNS if c not in df.columns]
    return missing


def validate_intents(payload: dict[str, Any]) -> list[str]:
    """Return a list of issues found in an order_intents payload."""
    issues: list[str] = []
    if not isinstance(payload, dict):
        issues.append("payload is not a dict")
        return issues
    if payload.get("artifact_type") != "order_intents":
        issues.append(f"expected artifact_type='order_intents', got {payload.get('artifact_type')!r}")
    intents = payload.get("intents")
    if not isinstance(intents, list):
        issues.append("payload.intents is not a list")
        return issues
    if not intents:
        issues.append("payload.intents is empty")
    required_intent_keys = {"intent_id", "symbol", "side", "amount"}
    for i, intent in enumerate(intents):
        missing = required_intent_keys - set(intent.keys())
        if missing:
            issues.append(f"intents[{i}] missing keys: {missing}")
    return issues


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return a list of missing-key warnings for a run manifest."""
    missing = [k for k in MANIFEST_KEYS if k not in manifest]
    return missing
