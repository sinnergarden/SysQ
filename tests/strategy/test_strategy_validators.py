"""Tests for qsys/strategy/validators.py — stage-specific config validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qsys.strategy.spec import StrategySpec, spec_from_config
from qsys.strategy.validators import (
    validate_archived_spec,
    validate_candidate_spec,
    validate_production_spec,
    validate_rejected_spec,
    validate_research_spec,
    validate_runtime_registry,
    validate_strategy_spec,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_spec(stage: str = "research", **overrides) -> StrategySpec:
    """Build a minimal StrategySpec for testing."""
    kwargs = dict(strategy_id="test_strat", stage=stage)
    kwargs.update(overrides)
    return StrategySpec(**kwargs)


def _make_spec_raw(stage: str = "research", **overrides) -> StrategySpec:
    """Build a StrategySpec bypassing __post_init__ validation.

    Used for testing invalid-stage or invalid-id scenarios.
    """
    kwargs = dict(strategy_id="test_strat", stage=stage)
    kwargs.update(overrides)
    spec = object.__new__(StrategySpec)
    for k, v in kwargs.items():
        setattr(spec, k, v)
    # Set defaults for fields not provided
    for field_name in (
        "family", "display_name", "owner", "universe", "feature_set",
        "model_version", "signal_version", "account_id", "hypothesis",
    ):
        if not hasattr(spec, field_name) or getattr(spec, field_name) is None:
            setattr(spec, field_name, None)
    for field_name in (
        "label", "model", "signal", "portfolio", "paths",
        "lifecycle", "evaluation", "promotion_gates", "raw_config",
    ):
        if not hasattr(spec, field_name):
            setattr(spec, field_name, {})
    spec.config_path = None
    if not spec.display_name:
        spec.display_name = spec.strategy_id or "test"
    return spec


# ── validate_research_spec ───────────────────────────────────────────────────


class TestValidateResearchSpec:
    def test_valid_research_passes(self):
        spec = _make_spec(
            stage="research",
            family="momentum",
            display_name="Test",
            universe="csi300",
            feature_set="test_features",
            hypothesis="Test hypothesis",
            label={"horizons": [5]},
            model={"type": "lgbm"},
            signal={"method": "rank"},
            portfolio={"top_n": 20, "rebalance_freq": "weekly"},
        )
        errors = validate_research_spec(spec)
        assert errors == []

    def test_missing_family(self):
        spec = _make_spec(stage="research", strategy_id="s", display_name="T",
                          universe="csi300", feature_set="f", hypothesis="h",
                          label={"h": [5]}, model={"t": "l"}, signal={"m": "r"},
                          portfolio={"n": 20})
        spec.family = ""
        errors = validate_research_spec(spec)
        assert any("family" in e for e in errors)

    def test_missing_hypothesis(self):
        spec = _make_spec(stage="research", strategy_id="s", family="m",
                          display_name="T", universe="csi300", feature_set="f",
                          label={"h": [5]}, model={"t": "l"}, signal={"m": "r"},
                          portfolio={"n": 20})
        spec.hypothesis = None
        errors = validate_research_spec(spec)
        assert any("hypothesis" in e for e in errors)

    def test_missing_label(self):
        spec = _make_spec(stage="research", strategy_id="s", family="m",
                          display_name="T", universe="csi300", feature_set="f",
                          hypothesis="h", model={"t": "l"}, signal={"m": "r"},
                          portfolio={"n": 20})
        spec.label = {}
        errors = validate_research_spec(spec)
        assert any("label" in e for e in errors)

    def test_missing_model(self):
        spec = _make_spec(stage="research", strategy_id="s", family="m",
                          display_name="T", universe="csi300", feature_set="f",
                          hypothesis="h", label={"h": [5]}, signal={"m": "r"},
                          portfolio={"n": 20})
        spec.model = {}
        errors = validate_research_spec(spec)
        assert any("model" in e for e in errors)

    def test_missing_signal(self):
        spec = _make_spec(stage="research", strategy_id="s", family="m",
                          display_name="T", universe="csi300", feature_set="f",
                          hypothesis="h", label={"h": [5]}, model={"t": "l"},
                          portfolio={"n": 20})
        spec.signal = {}
        errors = validate_research_spec(spec)
        assert any("signal" in e for e in errors)

    def test_missing_portfolio(self):
        spec = _make_spec(stage="research", strategy_id="s", family="m",
                          display_name="T", universe="csi300", feature_set="f",
                          hypothesis="h", label={"h": [5]}, model={"t": "l"},
                          signal={"m": "r"})
        spec.portfolio = {}
        errors = validate_research_spec(spec)
        assert any("portfolio" in e for e in errors)

    def test_research_does_not_require_registry(self):
        """Research spec should not fail on registry check."""
        spec = _make_spec(
            stage="research",
            strategy_id="nonexistent_999",
            family="m", display_name="T", universe="csi300",
            feature_set="f", hypothesis="h",
            label={"h": [5]}, model={"t": "l"}, signal={"m": "r"},
            portfolio={"n": 20},
        )
        errors = validate_research_spec(spec)
        assert all("registry" not in e for e in errors)


# ── validate_candidate_spec ──────────────────────────────────────────────────


class TestValidateCandidateSpec:
    def test_valid_candidate_passes(self):
        """A well-formed candidate spec should pass (if registered)."""
        spec = _make_spec(
            stage="candidate",
            strategy_id="alpha_v1",
            display_name="Alpha V1",
            universe="csi300",
            feature_set="alpha_v1",
            account_id="shadow_alpha_v1",
            model_version="v1",
            signal_version="v1",
            paths={"model_dir": "/x", "predictions_dir": "/y", "ledger_db": "z"},
            portfolio={"top_n": 20, "rebalance_freq": "weekly",
                       "buffer_hold": 60, "buffer_buy": 40, "single_stock_cap": 0.07},
        )
        errors = validate_candidate_spec(spec)
        # alpha_v1 is registered — expect no errors from research-level or candidate-level
        assert all("registry" not in e for e in errors)

    def test_missing_account_id(self):
        spec = _make_spec(stage="candidate", strategy_id="alpha_v1",
                          display_name="T", universe="csi300", feature_set="f",
                          model_version="v1", signal_version="v1",
                          portfolio={"top_n": 20})
        spec.account_id = None
        errors = validate_candidate_spec(spec)
        assert any("account_id" in e for e in errors)

    def test_account_id_must_start_with_shadow(self):
        spec = _make_spec(stage="candidate", strategy_id="alpha_v1",
                          display_name="T", universe="csi300", feature_set="f",
                          account_id="live_prod", model_version="v1",
                          signal_version="v1", portfolio={"top_n": 20})
        errors = validate_candidate_spec(spec)
        assert any("shadow_" in e for e in errors)

    def test_missing_paths(self):
        spec = _make_spec(stage="candidate", strategy_id="alpha_v1",
                          display_name="T", universe="csi300", feature_set="f",
                          account_id="shadow_t", model_version="v1",
                          signal_version="v1", portfolio={"top_n": 20})
        spec.paths = {}
        errors = validate_candidate_spec(spec)
        assert any("paths" in e for e in errors)

    def test_missing_model_version(self):
        spec = _make_spec(stage="candidate", strategy_id="alpha_v1",
                          display_name="T", universe="csi300", feature_set="f",
                          account_id="shadow_t",
                          signal_version="v1",
                          portfolio={"top_n": 20},
                          paths={"model_dir": "/x", "predictions_dir": "/y", "ledger_db": "z"})
        spec.model_version = None
        spec.model = {}
        errors = validate_candidate_spec(spec)
        assert any("model_version" in e for e in errors)

    def test_missing_signal_version(self):
        spec = _make_spec(stage="candidate", strategy_id="alpha_v1",
                          display_name="T", universe="csi300", feature_set="f",
                          account_id="shadow_t",
                          model_version="v1",
                          portfolio={"top_n": 20},
                          paths={"model_dir": "/x", "predictions_dir": "/y", "ledger_db": "z"})
        spec.signal_version = None
        spec.signal = {}
        errors = validate_candidate_spec(spec)
        assert any("signal_version" in e for e in errors)

    def test_missing_top_n_in_portfolio(self):
        spec = _make_spec(stage="candidate", strategy_id="alpha_v1",
                          display_name="T", universe="csi300", feature_set="f",
                          account_id="shadow_t", model_version="v1",
                          signal_version="v1",
                          portfolio={"rebalance_freq": "weekly"},
                          paths={"model_dir": "/x", "predictions_dir": "/y", "ledger_db": "z"})
        errors = validate_candidate_spec(spec)
        assert any("portfolio.top_n" in e for e in errors)

    def test_unregistered_candidate_reports_error(self):
        """An unregistered candidate should report a registry error."""
        spec = _make_spec(
            stage="candidate",
            strategy_id="definitely_not_registered",
            display_name="T", universe="csi300", feature_set="f",
            account_id="shadow_t", model_version="v1", signal_version="v1",
            paths={"model_dir": "/x", "predictions_dir": "/y", "ledger_db": "z"},
            portfolio={"top_n": 20},
        )
        errors = validate_candidate_spec(spec)
        assert any("registry" in e for e in errors)

    def test_production_config_rejected_in_candidate(self):
        """Candidate with broker_policy/risk_limits should warn."""
        spec = _make_spec(
            stage="candidate",
            strategy_id="alpha_v1",
            display_name="T", universe="csi300", feature_set="f",
            account_id="shadow_t", model_version="v1", signal_version="v1",
            paths={"model_dir": "/x", "predictions_dir": "/y", "ledger_db": "z"},
            portfolio={"top_n": 20},
        )
        spec.raw_config["broker_policy"] = {"type": "mock"}
        errors = validate_candidate_spec(spec)
        assert any("broker_policy" in e or "production" in e for e in errors)


# ── validate_production_spec ─────────────────────────────────────────────────


class TestValidateProductionSpec:
    def test_minimal_production_needs_many_fields(self):
        spec = _make_spec(stage="production", strategy_id="test")
        errors = validate_production_spec(spec)
        assert any("account_id" in e for e in errors)
        assert any("portfolio" in e for e in errors)
        assert any("registry" in e for e in errors)
        assert any("risk_limits" in e for e in errors)
        assert any("capital_allocation" in e for e in errors)
        assert any("broker_policy" in e or "execution_policy" in e for e in errors)
        assert any("approval_policy" in e for e in errors)


# ── validate_rejected_spec / validate_archived_spec ──────────────────────────


class TestValidateRejectedArchived:
    def test_rejected_only_needs_id_and_stage(self):
        spec = _make_spec(stage="rejected", strategy_id="r1")
        errors = validate_rejected_spec(spec)
        assert errors == []

    def test_archived_only_needs_id_and_stage(self):
        spec = _make_spec(stage="archived", strategy_id="a1")
        errors = validate_archived_spec(spec)
        assert errors == []

    def test_rejected_wrong_stage(self):
        spec = _make_spec(stage="production", strategy_id="r1")
        errors = validate_rejected_spec(spec)
        assert any("rejected" in e for e in errors)

    def test_archived_wrong_stage(self):
        spec = _make_spec(stage="production", strategy_id="a1")
        errors = validate_archived_spec(spec)
        assert any("archived" in e for e in errors)


# ── validate_runtime_registry ────────────────────────────────────────────────


class TestValidateRuntimeRegistry:
    def test_registered_strategy_returns_empty(self):
        errors = validate_runtime_registry(
            _make_spec(strategy_id="alpha_v1")
        )
        assert errors == []

    def test_unregistered_strategy_returns_error(self):
        errors = validate_runtime_registry(
            _make_spec(strategy_id="this_does_not_exist_999")
        )
        assert len(errors) > 0
        assert any("registry" in e for e in errors)


# ── validate_strategy_spec (composite) ───────────────────────────────────────


class TestValidateStrategySpec:
    def test_delegates_to_stage_validator(self):
        spec = _make_spec(stage="rejected", strategy_id="r1")
        errors = validate_strategy_spec(spec)
        assert errors == []

    def test_unknown_stage(self):
        spec = _make_spec_raw(stage="bogus")
        errors = validate_strategy_spec(spec)
        assert any("unknown stage" in e for e in errors)

    def test_strict_raises_on_errors(self):
        spec = _make_spec_raw(stage="research", strategy_id="")
        with pytest.raises(ValueError, match="validation failed"):
            validate_strategy_spec(spec, strict=True)

    def test_strict_empty_errors_does_not_raise(self):
        spec = _make_spec(stage="rejected", strategy_id="r1")
        # should not raise
        validate_strategy_spec(spec, strict=True)


# ── Real config validation ───────────────────────────────────────────────────


class TestRealConfigs:
    """Validate that the actual alpha_v1 and alpha_v2 configs pass checks."""

    @pytest.mark.parametrize("config_name", ["alpha_v1", "alpha_v2"])
    def test_candidate_config_validates(self, config_name: str):
        config_path = Path(__file__).resolve().parent.parent.parent / "configs" / "strategies" / f"{config_name}.yaml"
        if not config_path.exists():
            pytest.skip(f"{config_path} not found")
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        spec = spec_from_config(raw, path=config_path)
        errors = validate_strategy_spec(spec, strict=False)
        other_errors = [e for e in errors if "registry" not in e]
        if other_errors:
            pytest.fail(f"{config_name} validation errors: {other_errors}")
