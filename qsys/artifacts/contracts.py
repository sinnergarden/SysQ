"""
ADR-007 Artifact Contracts.

Standard dataclasses for all six artifact types defined in ADR-007.
Missing fields are explicitly set to None / "not_available" / "not_applicable".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any

import pandas as pd


# ── Helpers ────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now().isoformat()


def _today() -> str:
    return date.today().isoformat()


NOT_AVAILABLE = "not_available"
NOT_APPLICABLE = "not_applicable"


# ── SignalArtifact ─────────────────────────────────────────────────────

@dataclass
class SignalArtifact:
    """Record of strategy signal output for a single instrument on a trade date."""
    trade_date: str
    strategy_id: str
    instrument: str
    score: float
    rank: int
    candidate_id: str = NOT_AVAILABLE
    model_version: str = NOT_AVAILABLE
    signal_version: str = NOT_AVAILABLE
    data_cutoff: str = NOT_AVAILABLE
    raw_prediction: float | None = None
    normalized_score: float | None = None
    target_weight_raw: float | None = None
    config_hash: str = NOT_AVAILABLE
    feature_schema_version: str = NOT_AVAILABLE
    created_at: str = NOT_AVAILABLE


# ── OrderIntentArtifact ────────────────────────────────────────────────

@dataclass
class OrderIntentArtifact:
    """Record of a single order intent from signal-to-plan conversion."""
    trade_date: str
    strategy_id: str
    account_id: str
    instrument: str
    side: str
    target_weight: float
    current_weight: float
    target_quantity: int | None = None
    current_quantity: int | None = None
    delta_quantity: int | None = None
    reason: str = NOT_AVAILABLE
    constraints: str = NOT_APPLICABLE
    created_at: str = NOT_AVAILABLE


# ── ExecutionArtifact ──────────────────────────────────────────────────

@dataclass
class ExecutionArtifact:
    """Record of a single fill from simulated or real execution."""
    trade_date: str
    run_id: str
    strategy_id: str
    account_id: str
    instrument: str
    side: str
    quantity: int
    price: float
    commission: float
    status: str
    order_id: str = NOT_AVAILABLE
    fill_id: str = NOT_AVAILABLE
    stamp_tax: float = 0.0
    slippage: float = 0.0
    reason: str = NOT_AVAILABLE
    created_at: str = NOT_AVAILABLE


# ── PortfolioSnapshot ──────────────────────────────────────────────────

@dataclass
class PortfolioSnapshot:
    """Portfolio MTM snapshot at a point in time."""
    trade_date: str
    account_id: str
    strategy_id: str
    cash: float
    market_value: float
    total_asset: float
    daily_pnl: float | None = None
    daily_return: float | None = None
    position_count: int = 0
    turnover: float = 0.0
    cumulative_pnl: float | None = None
    cumulative_pnl_pct: float | None = None
    initial_capital: float | None = None
    details: list | None = None
    created_at: str = NOT_AVAILABLE


# ── CandidateReport ────────────────────────────────────────────────────

@dataclass
class CandidateReport:
    """Candidate strategy evaluation report (Research → Candidate promotion)."""
    candidate_id: str
    strategy_id: str
    research_id: str = NOT_AVAILABLE
    hypothesis: str = NOT_AVAILABLE
    feature_set: str = NOT_AVAILABLE
    label: str = NOT_AVAILABLE
    model: str = NOT_AVAILABLE
    train_window: str = NOT_AVAILABLE
    validation_result: str = NOT_APPLICABLE
    backtest_result: str = NOT_APPLICABLE
    risk_summary: str = NOT_AVAILABLE
    known_issues: str = NOT_AVAILABLE
    promotion_decision: str = "pending"
    next_action: str = NOT_AVAILABLE


# ── RunManifest ────────────────────────────────────────────────────────

@dataclass
class RunManifest:
    """Run metadata for traceability and audit."""
    run_id: str
    trade_date: str
    stage: str
    strategy_id: str
    account_id: str
    status: str
    git_commit: str = NOT_AVAILABLE
    config_hash: str = NOT_AVAILABLE
    data_version: str = NOT_AVAILABLE
    model_version: str = NOT_AVAILABLE
    signal_version: str = NOT_AVAILABLE
    input_artifacts: list[dict[str, str]] = field(default_factory=list)
    output_artifacts: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    notes: str | None = None
    created_at: str = NOT_AVAILABLE
    updated_at: str = NOT_AVAILABLE


# ── Validation helpers ────────────────────────────────────────────────

REQUIRED_PREDICTION_COLUMNS = frozenset({
    "trade_date", "instrument", "score", "model_name", "mainline_object_name",
})
REQUIRED_TARGET_WEIGHT_COLUMNS = frozenset({
    "trade_date", "instrument", "score", "rank", "target_weight",
    "strategy_id", "strategy_version", "portfolio_method",
    "model_name", "mainline_object_name",
})
REQUIRED_ORDER_INTENT_COLUMNS = frozenset({
    "trade_date", "instrument", "side", "target_weight", "current_weight",
    "target_value", "current_value", "diff_value", "requested_qty", "reason",
})
REQUIRED_REBALANCE_AUDIT_COLUMNS = frozenset({
    "trade_date", "instrument", "score", "target_weight", "current_weight",
    "target_value", "current_value", "diff_value", "requested_qty",
    "action", "reason",
})
REQUIRED_PLAN_META_FIELDS = frozenset({
    "trade_date", "reference_date", "strategy_id", "strategy_version",
    "portfolio_method", "top_n", "buffer_hold", "buffer_buy",
    "single_stock_cap", "cash_before", "market_value_before",
    "total_value_before", "buy_count", "sell_count", "total_orders", "build_ts",
})
REQUIRED_EXECUTION_SUMMARY_FIELDS = frozenset({
    "trade_date", "run_id", "status", "strategy_id", "strategy_version",
    "portfolio_method", "order_count", "buy_count", "sell_count",
    "filled_count", "rejected_count", "cash_before", "cash_after",
    "market_value_before", "market_value_after", "total_value_before",
    "total_value_after", "turnover", "no_real_orders",
})
REQUIRED_MTM_SNAPSHOT_FIELDS = frozenset({
    "trade_date", "account_id", "cash", "market_value", "total_value",
    "cumulative_pnl", "cumulative_pnl_pct", "daily_pnl",
})
REQUIRED_TRAINING_RESULT_FIELDS = frozenset({
    "strategy_id", "model_version", "model_dir", "status",
    "metrics", "artifacts",
})


def validate_predictions_frame(df: pd.DataFrame) -> None:
    """Raise ValueError if *df* lacks any required prediction column."""
    missing = sorted(REQUIRED_PREDICTION_COLUMNS.difference(df.columns))
    if missing:
        raise ValueError(f"predictions frame missing columns: {missing}")


def validate_target_weights_frame(df: pd.DataFrame) -> None:
    """Raise ValueError if *df* lacks any required target-weight column."""
    missing = sorted(REQUIRED_TARGET_WEIGHT_COLUMNS.difference(df.columns))
    if missing:
        raise ValueError(f"target_weights frame missing columns: {missing}")


def validate_order_intents_frame(df: pd.DataFrame) -> None:
    """Raise ValueError if *df* lacks any required order-intent column."""
    missing = sorted(REQUIRED_ORDER_INTENT_COLUMNS.difference(df.columns))
    if missing:
        raise ValueError(f"order_intents frame missing columns: {missing}")


def validate_rebalance_audit_frame(df: pd.DataFrame) -> None:
    """Raise ValueError if *df* lacks any required rebalance-audit column."""
    missing = sorted(REQUIRED_REBALANCE_AUDIT_COLUMNS.difference(df.columns))
    if missing:
        raise ValueError(f"rebalance_audit frame missing columns: {missing}")


def validate_plan_meta(data: dict[str, Any]) -> None:
    """Raise ValueError if *data* lacks any required plan_meta field."""
    missing = sorted(REQUIRED_PLAN_META_FIELDS.difference(data.keys()))
    if missing:
        raise ValueError(f"plan_meta missing fields: {missing}")


def validate_execution_summary(data: dict[str, Any]) -> None:
    """Raise ValueError if *data* lacks any required execution-summary field."""
    missing = sorted(REQUIRED_EXECUTION_SUMMARY_FIELDS.difference(data.keys()))
    if missing:
        raise ValueError(f"execution_summary missing fields: {missing}")


def validate_mtm_snapshot(data: dict[str, Any]) -> None:
    """Raise ValueError if *data* lacks any required MTM-snapshot field."""
    missing = sorted(REQUIRED_MTM_SNAPSHOT_FIELDS.difference(data.keys()))
    if missing:
        raise ValueError(f"mtm_snapshot missing fields: {missing}")


def validate_training_result(data: dict[str, Any]) -> None:
    """Raise ValueError if *data* lacks any required training-result field."""
    missing = sorted(REQUIRED_TRAINING_RESULT_FIELDS.difference(data.keys()))
    if missing:
        raise ValueError(f"training_result missing fields: {missing}")


# ── Conversion helpers ─────────────────────────────────────────────────

def artifact_to_dict(a: Any) -> dict[str, Any]:
    """Convert an artifact dataclass to a dict. None → JSON null."""
    return asdict(a)
