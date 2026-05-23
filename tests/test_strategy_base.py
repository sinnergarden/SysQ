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


class _BaseValidCandidate:
    """Reusable stub that satisfies the full StrategyCandidate protocol."""

    # ── Properties ──────────────────────────────────────────────────────

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

    # ── Data ────────────────────────────────────────────────────────────

    def resolve_data_date(self, trade_date: str) -> str:
        return trade_date

    def get_stock_name(self, ts_code: str) -> str:
        return ts_code

    def load_model(self) -> Any:
        return {"models": {}, "clean_features": []}

    def fetch_data(self, data_date: str) -> Any:
        return {"frame": [], "clean_features": []}

    # ── Predict + Plan ──────────────────────────────────────────────────

    def generate_predictions(self, data: Any) -> Any:
        return []

    def should_rebalance(self, trade_date: str) -> bool:
        return True

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        return True

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        return []

    # ── Execute + MTM ───────────────────────────────────────────────────

    def execute_plan(self, context: Any) -> Any:
        return None

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        pass

    def mark_to_market(self, context: Any) -> dict | None:
        return None

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        return None

    # ── Notifications ───────────────────────────────────────────────────

    def build_preopen_message(
        self, context: Any, rebalance_skipped: bool, predictions: Any
    ) -> str:
        return "preopen message"

    def build_postclose_message(
        self,
        context: Any,
        mtm: dict | None = None,
        artifacts: Any = None,
        stale_check: dict | None = None,
        execution_committed: bool = False,
        execution_skipped: bool = False,
        idempotent_skip: bool = False,
    ) -> str:
        return "postclose message"

    def send_notification(self, text: str) -> None:
        pass


class TestStrategyCandidateProtocol(unittest.TestCase):
    """StrategyCandidate Protocol — structural typing, no inheritance needed."""

    def test_protocol_is_runtime_checkable(self):
        candidate = _BaseValidCandidate()
        self.assertIsInstance(candidate, StrategyCandidate)

    def test_missing_property_fails_check(self):
        class MissingProp:
            @property
            def strategy_id(self) -> str:
                return "test"

            # missing account_id, universe, etc.

        obj = MissingProp()
        self.assertNotIsInstance(obj, StrategyCandidate)

    def test_missing_runtime_method_fails_check(self):
        """Missing a required runtime hook → not a StrategyCandidate."""
        class MissingMethod:
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
            # Missing: resolve_data_date, load_model, fetch_data, etc.

        obj = MissingMethod()
        self.assertNotIsInstance(obj, StrategyCandidate)

    def test_alpha_v1_adapter_is_strategy_candidate(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        adapter = AlphaV1StrategyAdapter()
        self.assertIsInstance(adapter, StrategyCandidate)

    def test_protocol_works_with_isinstance_check(self):
        self.assertIsInstance(_BaseValidCandidate(), StrategyCandidate)


if __name__ == "__main__":
    unittest.main()
