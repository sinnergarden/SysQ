"""
ADR-007 Artifact Contracts.

Standard dataclasses for all six artifact types defined in ADR-007.
Missing fields are explicitly set to None / "not_available" / "not_applicable".
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any


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


# ── Conversion helpers ─────────────────────────────────────────────────

def artifact_to_dict(a: Any) -> dict[str, Any]:
    """Convert an artifact dataclass to a dict, omitting None values."""
    d = asdict(a)
    return {k: v for k, v in d.items() if v is not None}
