"""Tests for qsys/strategy/alpha_v1/adapter.py — AlphaV1StrategyAdapter."""

from __future__ import annotations

import unittest

from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter
from qsys.strategy.base import StrategyCandidate


class TestAlphaV1StrategyAdapter(unittest.TestCase):
    """AlphaV1StrategyAdapter — confirms Protocol compliance and property values."""

    def setUp(self):
        self.adapter = AlphaV1StrategyAdapter()

    def test_is_strategy_candidate(self):
        self.assertIsInstance(self.adapter, StrategyCandidate)

    def test_strategy_id(self):
        self.assertEqual(self.adapter.strategy_id, "alpha_v1")

    def test_account_id(self):
        self.assertEqual(self.adapter.account_id, "shadow_alpha_v1")

    def test_universe(self):
        self.assertEqual(self.adapter.universe, "csi300")

    def test_feature_set(self):
        self.assertEqual(self.adapter.feature_set, "alpha_v1")

    def test_model_version(self):
        # Should match the singleton's version
        from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE
        self.assertEqual(self.adapter.model_version, ALPHA_V1_CANDIDATE.version)

    def test_signal_version(self):
        self.assertEqual(self.adapter.signal_version, "blend_0.8:0.2")

    def test_rebalance_policy(self):
        policy = self.adapter.rebalance_policy
        self.assertEqual(policy["top_n"], 20)
        self.assertEqual(policy["buffer_hold"], 60)
        self.assertEqual(policy["buffer_buy"], 40)
        self.assertEqual(policy["rebalance_freq"], "weekly")
        self.assertEqual(policy["single_stock_cap"], 0.07)

    def test_properties_are_consistent(self):
        """Adapter properties should match the config singleton."""
        from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE as C

        self.assertEqual(self.adapter.strategy_id, C.strategy_id)
        self.assertEqual(self.adapter.account_id, C.shadow_account_id)
        self.assertEqual(
            self.adapter.rebalance_policy["rebalance_freq"],
            C.portfolio.rebalance_freq,
        )

    def test_no_optional_hooks(self):
        """AlphaV1StrategyAdapter does not define lifecycle hooks."""
        self.assertFalse(hasattr(self.adapter, "on_preopen"))
        self.assertFalse(hasattr(self.adapter, "on_postclose"))
        self.assertFalse(hasattr(self.adapter, "on_train"))


if __name__ == "__main__":
    unittest.main()
