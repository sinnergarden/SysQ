"""Tests for qsys/strategy/registry.py — registry, get_strategy_class, create_strategy."""
from __future__ import annotations

import pytest

from qsys.strategy.registry import (
    STRATEGY_REGISTRY,
    create_strategy,
    get_strategy_class,
)


class TestRegistry:
    """Registry contains built-in strategies."""

    def test_alpha_v1_registered(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        assert "alpha_v1" in STRATEGY_REGISTRY
        assert STRATEGY_REGISTRY["alpha_v1"] is AlphaV1StrategyAdapter

    def test_get_alpha_v1_class(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        cls = get_strategy_class("alpha_v1")
        assert cls is AlphaV1StrategyAdapter


class TestGetStrategyClass:
    """get_strategy_class resolves correctly or raises ValueError."""

    def test_known_strategy(self):
        cls = get_strategy_class("alpha_v1")
        assert cls is not None

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="alpha_v2"):
            get_strategy_class("alpha_v2")

    def test_unknown_strategy_message_lists_known(self):
        with pytest.raises(ValueError) as exc_info:
            get_strategy_class("nonexistent")
        assert "alpha_v1" in str(exc_info.value)


class TestCreateStrategy:
    """create_strategy returns instances."""

    def test_create_default(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        s = create_strategy("alpha_v1")
        assert isinstance(s, AlphaV1StrategyAdapter)

    def test_create_with_config(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        s = create_strategy("alpha_v1", config={"display_name": "Test"})
        assert isinstance(s, AlphaV1StrategyAdapter)
        assert s.display_name == "Test"

    def test_create_with_project_root(self, tmp_path):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        s = create_strategy("alpha_v1", project_root=tmp_path)
        assert isinstance(s, AlphaV1StrategyAdapter)

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError):
            create_strategy("nonexistent")


class TestRegistryNoDynamicImport:
    """Registry does not use dynamic imports."""

    def test_registry_is_simple_dict(self):
        assert isinstance(STRATEGY_REGISTRY, dict)
        assert all(isinstance(k, str) for k in STRATEGY_REGISTRY)
