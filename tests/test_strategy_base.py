"""Tests for qsys/strategy/base.py — IStrategy ABC and StrategyCandidate Protocol."""

from __future__ import annotations

import unittest
from typing import Any

from qsys.strategy.base import IStrategy, StrategyCandidate


class TestIStrategy(unittest.TestCase):
    """IStrategy ABC — cannot instantiate without implementing generate_orders."""

    def test_cannot_instantiate_abstract(self):
        with self.assertRaises(TypeError):
            IStrategy()  # type: ignore[abstract]

    def test_concrete_subclass(self):
        class ConcreteStrategy(IStrategy):
            def generate_orders(self, signals, current_portfolio):
                return ["order1", "order2"]

        s = ConcreteStrategy()
        self.assertIsInstance(s, IStrategy)
        self.assertEqual(s.generate_orders(None, None), ["order1", "order2"])


class TestStrategyCandidateProtocol(unittest.TestCase):
    """StrategyCandidate Protocol — structural typing, no inheritance needed."""

    def test_protocol_is_runtime_checkable(self):
        # A class that satisfies the protocol but does not inherit from it
        class ValidCandidate:
            @property
            def strategy_id(self) -> str:
                return "test_strat"

            @property
            def account_id(self) -> str:
                return "shadow_test_strat"

            @property
            def universe(self) -> str:
                return "csi300"

            @property
            def feature_set(self) -> str:
                return "alpha_v1"

            @property
            def model_version(self) -> str:
                return "v1"

            @property
            def signal_version(self) -> str:
                return "blend_0.8:0.2"

            @property
            def rebalance_policy(self) -> dict[str, Any]:
                return {"top_n": 20}

        candidate = ValidCandidate()
        self.assertIsInstance(candidate, StrategyCandidate)

    def test_missing_property_fails_check(self):
        class MissingProp:
            @property
            def strategy_id(self) -> str:
                return "test"

            # missing account_id, universe, etc.

        obj = MissingProp()
        self.assertNotIsInstance(obj, StrategyCandidate)

    def test_optional_hooks_via_hasattr(self):
        class WithHooks:
            @property
            def strategy_id(self) -> str:
                return "s"

            @property
            def account_id(self) -> str:
                return "a"

            @property
            def universe(self) -> str:
                return "u"

            @property
            def feature_set(self) -> str:
                return "f"

            @property
            def model_version(self) -> str:
                return "m"

            @property
            def signal_version(self) -> str:
                return "s"

            @property
            def rebalance_policy(self) -> dict[str, Any]:
                return {}

            def on_preopen(self, context: Any) -> None:
                pass

            def on_postclose(self, context: Any) -> None:
                pass

        obj = WithHooks()
        self.assertTrue(hasattr(obj, "on_preopen"))
        self.assertTrue(hasattr(obj, "on_postclose"))
        self.assertFalse(hasattr(obj, "on_train"))

    def test_protocol_works_with_isinstance_check(self):
        """Verify isinstance(x, StrategyCandidate) works at runtime."""
        class Good:
            @property
            def strategy_id(self) -> str:
                return "g"

            @property
            def account_id(self) -> str:
                return "g_a"

            @property
            def universe(self) -> str:
                return "g_u"

            @property
            def feature_set(self) -> str:
                return "g_f"

            @property
            def model_version(self) -> str:
                return "g_m"

            @property
            def signal_version(self) -> str:
                return "g_s"

            @property
            def rebalance_policy(self) -> dict[str, Any]:
                return {}

        self.assertIsInstance(Good(), StrategyCandidate)


if __name__ == "__main__":
    unittest.main()
