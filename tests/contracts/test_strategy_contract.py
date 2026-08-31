"""Contract tests for the StrategyCandidate protocol.

Every strategy adapter must satisfy ``StrategyCandidate`` at runtime.
These tests verify that property and method contracts are met.
"""

from __future__ import annotations

import inspect

import pytest

from qsys.strategy.base import StrategyCandidate
from qsys.strategy.registry import STRATEGY_REGISTRY


# ── Helpers ────────────────────────────────────────────────────────────────────


def _adapter_params():
    """Yield ``(strategy_id, adapter_class)`` pairs for parametrization."""
    return sorted(STRATEGY_REGISTRY.items())


# ── Protocol conformance ───────────────────────────────────────────────────────


class TestStrategyCandidateProtocol:
    """Verify ``StrategyCandidate`` is a well-formed runtime-checkable protocol."""

    def test_is_runtime_checkable(self):
        import typing
        assert isinstance(StrategyCandidate, typing._ProtocolMeta)

    def test_identity_properties_defined(self):
        for prop in ("strategy_id", "account_id", "display_name"):
            assert hasattr(StrategyCandidate, prop), f"missing property: {prop}"

    def test_config_properties_defined(self):
        for prop in ("universe", "feature_set", "model_version",
                     "signal_version", "rebalance_policy"):
            assert hasattr(StrategyCandidate, prop), f"missing property: {prop}"

    def test_runtime_hooks_defined(self):
        hooks = [
            "resolve_data_date", "get_stock_name", "load_model", "fetch_data",
            "generate_predictions", "print_predictions_summary",
            "should_rebalance", "build_plan", "load_plan_instruments",
            "save_predictions", "fetch_open_prices",
            "execute_plan", "commit_execution", "mark_to_market",
            "load_artifacts_for_notification",
            "build_preopen_message", "build_postclose_message",
            "send_notification", "train",
        ]
        for hook in hooks:
            assert hasattr(StrategyCandidate, hook), f"missing hook: {hook}"

    def test_registered_adapters_are_not_none(self):
        assert len(STRATEGY_REGISTRY) > 0, "strategy registry is empty"


# ── Per-adapter structural tests ───────────────────────────────────────────────


class TestRegisteredAdapters:
    """Structural checks for every adapter registered in the strategy registry."""

    @pytest.fixture(params=_adapter_params(), ids=lambda kv: kv[0])
    def adapter_pair(self, request) -> tuple[str, type]:
        return request.param

    # ── Protocol satisfaction ─────────────────────────────────────────

    def test_satisfies_protocol(self, adapter_pair: tuple[str, type]):
        """Every registered adapter must satisfy ``StrategyCandidate``."""
        _, cls = adapter_pair
        instance = cls()
        assert isinstance(instance, StrategyCandidate), (
            f"{cls.__name__} does not satisfy StrategyCandidate protocol"
        )

    # ── Identity properties ───────────────────────────────────────────

    def test_identity_properties_return_values(self, adapter_pair):
        """Identity properties must return non-empty strings."""
        _, cls = adapter_pair
        instance = cls()
        assert isinstance(instance.strategy_id, str) and instance.strategy_id
        assert isinstance(instance.account_id, str) and instance.account_id
        assert isinstance(instance.display_name, str) and instance.display_name

    def test_strategy_id_lowercase_snake(self, adapter_pair):
        """strategy_id must be lowercase snake_case."""
        _, cls = adapter_pair
        sid = cls().strategy_id
        assert sid == sid.lower(), f"strategy_id {sid!r} is not lowercase"
        assert "_" in sid or sid.isalpha(), (
            f"strategy_id {sid!r} does not look like snake_case"
        )

    # ── Config properties ─────────────────────────────────────────────

    def test_config_properties_return_values(self, adapter_pair):
        _, cls = adapter_pair
        instance = cls()
        assert isinstance(instance.universe, str) and instance.universe
        assert isinstance(instance.feature_set, str) and instance.feature_set
        assert isinstance(instance.model_version, str) and instance.model_version
        assert isinstance(instance.signal_version, str) and instance.signal_version
        rebal = instance.rebalance_policy
        assert isinstance(rebal, dict)
        assert "top_n" in rebal
        assert "single_stock_cap" in rebal

    # ── Method signature checks ───────────────────────────────────────

    def test_resolve_data_date_signature(self, adapter_pair):
        """resolve_data_date(trade_date: str) -> str"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.resolve_data_date)
        assert "trade_date" in sig.parameters

    def test_fetch_data_signature(self, adapter_pair):
        """fetch_data(data_date: str)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.fetch_data)
        assert "data_date" in sig.parameters

    def test_generate_predictions_signature(self, adapter_pair):
        """generate_predictions(data: Any)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.generate_predictions)
        assert "data" in sig.parameters

    def test_should_rebalance_signature(self, adapter_pair):
        """should_rebalance(trade_date: str)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.should_rebalance)
        assert "trade_date" in sig.parameters

    def test_build_plan_signature(self, adapter_pair):
        """build_plan(predictions: Any, target_dir: Any)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.build_plan)
        assert "predictions" in sig.parameters
        assert "target_dir" in sig.parameters

    def test_save_predictions_signature(self, adapter_pair):
        """save_predictions(predictions, run_root, trade_date)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.save_predictions)
        assert "predictions" in sig.parameters
        assert "run_root" in sig.parameters
        assert "trade_date" in sig.parameters

    def test_fetch_open_prices_signature(self, adapter_pair):
        """fetch_open_prices(trade_date, instruments)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.fetch_open_prices)
        assert "trade_date" in sig.parameters
        assert "instruments" in sig.parameters

    def test_execute_plan_signature(self, adapter_pair):
        """execute_plan(context)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.execute_plan)
        assert "context" in sig.parameters

    def test_commit_execution_signature(self, adapter_pair):
        """commit_execution(context, staging_dir)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.commit_execution)
        assert "context" in sig.parameters
        assert "staging_dir" in sig.parameters

    def test_mark_to_market_signature(self, adapter_pair):
        """mark_to_market(context)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.mark_to_market)
        assert "context" in sig.parameters

    def test_train_signature(self, adapter_pair):
        """train(context)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.train)
        assert "context" in sig.parameters

    def test_build_preopen_message_signature(self, adapter_pair):
        """build_preopen_message(context, rebalance_skipped, predictions)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.build_preopen_message)
        assert "context" in sig.parameters
        assert "rebalance_skipped" in sig.parameters
        assert "predictions" in sig.parameters

    def test_build_postclose_message_signature(self, adapter_pair):
        """build_postclose_message(context, ...)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.build_postclose_message)
        assert "context" in sig.parameters

    def test_send_notification_signature(self, adapter_pair):
        """send_notification(text)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.send_notification)
        assert "text" in sig.parameters

    def test_load_plan_instruments_signature(self, adapter_pair):
        """load_plan_instruments(plan_dir)"""
        _, cls = adapter_pair
        sig = inspect.signature(cls.load_plan_instruments)
        assert "plan_dir" in sig.parameters

    # ── Smoke tests: hooks that work with simple string args ──────────

    def test_resolve_data_date_returns_string_smoke(self, adapter_pair):
        _, cls = adapter_pair
        instance = cls()
        result = instance.resolve_data_date("2026-05-22")
        assert isinstance(result, str)
        assert result

    def test_should_rebalance_returns_bool_smoke(self, adapter_pair):
        _, cls = adapter_pair
        instance = cls()
        result = instance.should_rebalance("2026-05-22")
        assert isinstance(result, bool)

    def test_fetch_open_prices_returns_dict_smoke(self, adapter_pair):
        _, cls = adapter_pair
        instance = cls()
        result = instance.fetch_open_prices("2026-05-22", [])
        assert isinstance(result, dict)
