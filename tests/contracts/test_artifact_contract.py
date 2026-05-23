"""Contract tests for artifact schemas and validation functions.

Verifies that:
1. Column constants in ``plan_builder`` / ``shadow_execution`` match the
   artifact-contract required sets.
2. Validation functions in ``qsys.artifacts.contracts`` correctly accept
   valid DataFrames / dicts and reject invalid ones.
3. ADR-007 dataclasses have the expected required fields.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from qsys.artifacts.contracts import (
    REQUIRED_PREDICTION_COLUMNS,
    REQUIRED_TARGET_WEIGHT_COLUMNS,
    REQUIRED_ORDER_INTENT_COLUMNS,
    REQUIRED_REBALANCE_AUDIT_COLUMNS,
    REQUIRED_PLAN_META_FIELDS,
    REQUIRED_EXECUTION_SUMMARY_FIELDS,
    REQUIRED_MTM_SNAPSHOT_FIELDS,
    REQUIRED_TRAINING_RESULT_FIELDS,
    SignalArtifact,
    OrderIntentArtifact,
    ExecutionArtifact,
    PortfolioSnapshot,
    CandidateReport,
    RunManifest,
    validate_predictions_frame,
    validate_target_weights_frame,
    validate_order_intents_frame,
    validate_rebalance_audit_frame,
    validate_plan_meta,
    validate_execution_summary,
    validate_mtm_snapshot,
    validate_training_result,
)
from qsys.ops.plan_builder import (
    TARGET_WEIGHT_COLUMNS,
    ORDER_INTENT_COLUMNS,
    REBALANCE_AUDIT_COLUMNS,
)
from qsys.ops.shadow_execution import LEDGER_COLUMNS


# ── Column-constant alignment ──────────────────────────────────────────────────


class TestColumnConstantsMatchContracts:
    """Verify that implementation column constants match contract definitions."""

    def test_target_weight_columns_match(self):
        assert set(TARGET_WEIGHT_COLUMNS) == REQUIRED_TARGET_WEIGHT_COLUMNS

    def test_order_intent_columns_match(self):
        assert set(ORDER_INTENT_COLUMNS) == REQUIRED_ORDER_INTENT_COLUMNS

    def test_rebalance_audit_columns_match(self):
        assert set(REBALANCE_AUDIT_COLUMNS) == REQUIRED_REBALANCE_AUDIT_COLUMNS

    def test_ledger_columns_defined(self):
        """LEDGER_COLUMNS must be a non-empty list of strings."""
        assert isinstance(LEDGER_COLUMNS, list)
        assert len(LEDGER_COLUMNS) >= 5
        for col in LEDGER_COLUMNS:
            assert isinstance(col, str) and col


# ── Validation function tests ──────────────────────────────────────────────────


class TestValidatePredictionsFrame:
    """Tests for ``validate_predictions_frame``."""

    def test_valid_frame_passes(self):
        df = pd.DataFrame(columns=list(REQUIRED_PREDICTION_COLUMNS))
        validate_predictions_frame(df)  # does not raise

    def test_missing_column_raises(self):
        df = pd.DataFrame(columns=["trade_date", "instrument"])
        with pytest.raises(ValueError, match="predictions frame missing columns"):
            validate_predictions_frame(df)

    def test_empty_frame_missing_all_columns_raises(self):
        df = pd.DataFrame()
        with pytest.raises(ValueError):
            validate_predictions_frame(df)


class TestValidateTargetWeightsFrame:
    """Tests for ``validate_target_weights_frame``."""

    def test_valid_frame_passes(self):
        df = pd.DataFrame(columns=list(REQUIRED_TARGET_WEIGHT_COLUMNS))
        validate_target_weights_frame(df)

    def test_missing_column_raises(self):
        df = pd.DataFrame(columns=["trade_date", "instrument"])
        with pytest.raises(ValueError, match="target_weights frame missing columns"):
            validate_target_weights_frame(df)


class TestValidateOrderIntentsFrame:
    """Tests for ``validate_order_intents_frame``."""

    def test_valid_frame_passes(self):
        df = pd.DataFrame(columns=list(REQUIRED_ORDER_INTENT_COLUMNS))
        validate_order_intents_frame(df)

    def test_missing_column_raises(self):
        df = pd.DataFrame(columns=["trade_date", "instrument"])
        with pytest.raises(ValueError, match="order_intents frame missing columns"):
            validate_order_intents_frame(df)


class TestValidateRebalanceAuditFrame:
    """Tests for ``validate_rebalance_audit_frame``."""

    def test_valid_frame_passes(self):
        df = pd.DataFrame(columns=list(REQUIRED_REBALANCE_AUDIT_COLUMNS))
        validate_rebalance_audit_frame(df)

    def test_missing_column_raises(self):
        df = pd.DataFrame(columns=["trade_date"])
        with pytest.raises(ValueError, match="rebalance_audit frame missing columns"):
            validate_rebalance_audit_frame(df)


class TestValidatePlanMeta:
    """Tests for ``validate_plan_meta``."""

    def test_valid_dict_passes(self):
        data = {k: None for k in REQUIRED_PLAN_META_FIELDS}
        validate_plan_meta(data)

    def test_missing_field_raises(self):
        data = {"trade_date": "2026-05-22"}
        with pytest.raises(ValueError, match="plan_meta missing fields"):
            validate_plan_meta(data)


class TestValidateExecutionSummary:
    """Tests for ``validate_execution_summary``."""

    def test_valid_dict_passes(self):
        data = {k: None for k in REQUIRED_EXECUTION_SUMMARY_FIELDS}
        validate_execution_summary(data)

    def test_missing_field_raises(self):
        data = {"trade_date": "2026-05-22"}
        with pytest.raises(ValueError, match="execution_summary missing fields"):
            validate_execution_summary(data)


class TestValidateMtmSnapshot:
    """Tests for ``validate_mtm_snapshot``."""

    def test_valid_dict_passes(self):
        data = {k: None for k in REQUIRED_MTM_SNAPSHOT_FIELDS}
        validate_mtm_snapshot(data)

    def test_missing_field_raises(self):
        data = {"trade_date": "2026-05-22"}
        with pytest.raises(ValueError, match="mtm_snapshot missing fields"):
            validate_mtm_snapshot(data)


class TestValidateTrainingResult:
    """Tests for ``validate_training_result``."""

    def test_valid_dict_passes(self):
        data = {k: None for k in REQUIRED_TRAINING_RESULT_FIELDS}
        validate_training_result(data)

    def test_missing_field_raises(self):
        data = {"strategy_id": "alpha_v1"}
        with pytest.raises(ValueError, match="training_result missing fields"):
            validate_training_result(data)


# ── ADR-007 dataclass structural tests ─────────────────────────────────────────


class TestSignalArtifact:
    """Structural tests for ``SignalArtifact``."""

    def test_required_fields(self):
        obj = SignalArtifact(
            trade_date="2026-05-22",
            strategy_id="alpha_v1",
            instrument="000001.SZ",
            score=0.75,
            rank=1,
        )
        assert obj.trade_date == "2026-05-22"
        assert obj.strategy_id == "alpha_v1"
        assert obj.instrument == "000001.SZ"
        assert obj.score == 0.75
        assert obj.rank == 1
        # Optional fields should have defaults
        assert obj.candidate_id == "not_available"

    def test_asdict_roundtrip(self):
        from dataclasses import asdict
        obj = SignalArtifact(
            trade_date="2026-05-22",
            strategy_id="alpha_v1",
            instrument="000001.SZ",
            score=0.75,
            rank=1,
        )
        d = asdict(obj)
        assert d["trade_date"] == "2026-05-22"
        assert d["score"] == 0.75


class TestOrderIntentArtifact:
    """Structural tests for ``OrderIntentArtifact``."""

    def test_required_fields(self):
        obj = OrderIntentArtifact(
            trade_date="2026-05-22",
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
            instrument="000001.SZ",
            side="buy",
            target_weight=0.05,
            current_weight=0.0,
        )
        assert obj.side == "buy"
        assert obj.reason == "not_available"


class TestExecutionArtifact:
    """Structural tests for ``ExecutionArtifact``."""

    def test_required_fields(self):
        obj = ExecutionArtifact(
            trade_date="2026-05-22",
            run_id="run_001",
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
            instrument="000001.SZ",
            side="buy",
            quantity=100,
            price=10.5,
            commission=0.5,
            status="filled",
        )
        assert obj.quantity == 100
        assert obj.price == 10.5


class TestPortfolioSnapshot:
    """Structural tests for ``PortfolioSnapshot``."""

    def test_required_fields(self):
        obj = PortfolioSnapshot(
            trade_date="2026-05-22",
            account_id="shadow_alpha_v1",
            strategy_id="alpha_v1",
            cash=1_000_000.0,
            market_value=500_000.0,
            total_asset=1_500_000.0,
        )
        assert obj.cash == 1_000_000.0
        assert obj.total_asset == 1_500_000.0


class TestCandidateReport:
    """Structural tests for ``CandidateReport``."""

    def test_required_fields(self):
        obj = CandidateReport(
            candidate_id="cand_001",
            strategy_id="alpha_v3",
        )
        assert obj.promotion_decision == "pending"


class TestRunManifest:
    """Structural tests for ``RunManifest``."""

    def test_required_fields(self):
        obj = RunManifest(
            run_id="run_001",
            trade_date="2026-05-22",
            stage="preopen",
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
            status="success",
        )
        assert obj.run_id == "run_001"
        assert obj.status == "success"
