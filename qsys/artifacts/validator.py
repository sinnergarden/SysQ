"""
ADR-007 Artifact Validator.

Checks required fields and value ranges for each artifact type.
Returns a list of validation errors (empty = valid).
"""

from __future__ import annotations

from dataclasses import is_dataclass
from typing import Any


def validate_signal(artifact: Any) -> list[str]:
    errors: list[str] = []
    if not artifact.trade_date:
        errors.append("  trade_date is required")
    if not artifact.strategy_id:
        errors.append("  strategy_id is required")
    if not artifact.instrument:
        errors.append("  instrument is required")
    if artifact.score is None:
        errors.append("  score is required")
    if artifact.rank is not None and artifact.rank < 1:
        errors.append("  rank must be >= 1")
    return errors


def validate_order_intent(artifact: Any) -> list[str]:
    errors: list[str] = []
    if not artifact.trade_date:
        errors.append("  trade_date is required")
    if not artifact.strategy_id:
        errors.append("  strategy_id is required")
    if not artifact.account_id:
        errors.append("  account_id is required")
    if not artifact.instrument:
        errors.append("  instrument is required")
    if artifact.side not in ("BUY", "SELL", "HOLD"):
        errors.append(f"  side must be BUY/SELL/HOLD, got {artifact.side!r}")
    return errors


def validate_execution(artifact: Any) -> list[str]:
    errors: list[str] = []
    if not artifact.trade_date:
        errors.append("  trade_date is required")
    if not artifact.run_id:
        errors.append("  run_id is required")
    if not artifact.instrument:
        errors.append("  instrument is required")
    if artifact.side not in ("BUY", "SELL"):
        errors.append(f"  side must be BUY/SELL, got {artifact.side!r}")
    if artifact.quantity is not None and artifact.quantity <= 0:
        errors.append(f"  quantity must be > 0, got {artifact.quantity}")
    if artifact.status not in ("filled", "partial", "pending", "canceled"):
        errors.append(f"  unexpected status: {artifact.status!r}")
    return errors


def validate_snapshot(artifact: Any) -> list[str]:
    errors: list[str] = []
    if not artifact.trade_date:
        errors.append("  trade_date is required")
    if not artifact.account_id:
        errors.append("  account_id is required")
    if artifact.total_asset is not None and artifact.total_asset < 0:
        errors.append(f"  total_asset must be >= 0, got {artifact.total_asset}")
    return errors


def validate_manifest(artifact: Any) -> list[str]:
    errors: list[str] = []
    if not artifact.run_id:
        errors.append("  run_id is required")
    if not artifact.trade_date:
        errors.append("  trade_date is required")
    if artifact.status not in ("started", "completed", "failed"):
        errors.append(f"  status must be started/completed/failed, got {artifact.status!r}")
    return errors


def validate(artifact: Any) -> list[str]:
    """Validate any artifact. Returns list of error messages (empty = valid)."""
    if not is_dataclass(artifact):
        return ["artifact is not a dataclass"]

    type_name = type(artifact).__name__

    if type_name == "SignalArtifact":
        return validate_signal(artifact)
    elif type_name == "OrderIntentArtifact":
        return validate_order_intent(artifact)
    elif type_name == "ExecutionArtifact":
        return validate_execution(artifact)
    elif type_name == "PortfolioSnapshot":
        return validate_snapshot(artifact)
    elif type_name == "RunManifest":
        return validate_manifest(artifact)
    elif type_name == "CandidateReport":
        return []
    else:
        return [f"unknown artifact type: {type_name}"]
