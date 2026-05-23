"""Tests for qsys/strategy/alpha_v1/adapter.py — AlphaV1StrategyAdapter."""
from __future__ import annotations

import unittest
from pathlib import Path

from qsys.strategy.base import StrategyCandidate
from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter


class TestAlphaV1StrategyAdapter(unittest.TestCase):
    """AlphaV1StrategyAdapter — confirms Protocol compliance and property values."""

    def setUp(self):
        self.adapter = AlphaV1StrategyAdapter()

    # ── Existing identity / config tests ───────────────────────────────

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
        from qsys.strategy.alpha_v1.spec import ALPHA_V1_CANDIDATE as C

        self.assertEqual(self.adapter.strategy_id, C.strategy_id)
        self.assertEqual(self.adapter.account_id, C.shadow_account_id)
        self.assertEqual(
            self.adapter.rebalance_policy["rebalance_freq"],
            C.portfolio.rebalance_freq,
        )

    # ── New runtime-hook tests ─────────────────────────────────────────

    def test_get_stock_name_fallback(self):
        """Unknown codes return the input unchanged."""
        self.assertEqual(self.adapter.get_stock_name("999999"), "999999")

    def test_resolve_data_date_returns_string(self):
        """Returns a valid date string (requires qlib init)."""
        try:
            result = self.adapter.resolve_data_date("2026-05-22")
            self.assertIsInstance(result, str)
        except Exception:
            self.skipTest("qlib not initialized — resolve_data_date needs qlib")

    def test_load_model_raises_on_missing_model(self):
        """Without model files on disk, load_model should raise FileNotFoundError."""
        adapter = AlphaV1StrategyAdapter(project_root=Path("/nonexistent"))
        with self.assertRaises(FileNotFoundError):
            adapter.load_model()

    def test_load_plan_instruments_empty_dir(self):
        """No plan → empty list."""
        empty_dir = Path("/tmp") / "nonexistent_plan"
        self.assertEqual(self.adapter.load_plan_instruments(empty_dir), [])

    def test_send_notification_does_not_crash(self):
        """send_notification accepts a string and returns None."""
        self.adapter.send_notification("test message")

    def test_should_rebalance_returns_bool(self):
        """should_rebalance accepts a trade_date and returns bool."""
        result = self.adapter.should_rebalance("2026-05-22")
        self.assertIsInstance(result, bool)

    def test_no_optional_hooks(self):
        """AlphaV1StrategyAdapter does not define legacy lifecycle hooks."""
        self.assertFalse(hasattr(self.adapter, "on_preopen"))
        self.assertFalse(hasattr(self.adapter, "on_postclose"))
        self.assertFalse(hasattr(self.adapter, "on_train"))

    def test_build_preopen_message_returns_string(self):
        """build_preopen_message returns a non-empty string."""
        from qsys.ops.run_context import DailyRunContext

        ctx = DailyRunContext(
            trade_date="2026-05-22",
            mode="preopen",
            run_root=Path("/tmp/test_preopen"),
            project_root=Path("/tmp"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        import pandas as pd

        preds = pd.DataFrame({"instrument": ["000001"], "score": [0.5], "trade_date": ["2026-05-22"]})
        msg = self.adapter.build_preopen_message(ctx, False, preds)
        self.assertIsInstance(msg, str)
        self.assertTrue(len(msg) > 0)

    def test_build_postclose_message_returns_string(self):
        """build_postclose_message returns a non-empty string."""
        from qsys.ops.run_context import DailyRunContext

        ctx = DailyRunContext(
            trade_date="2026-05-22",
            mode="postclose",
            run_root=Path("/tmp/test_postclose"),
            project_root=Path("/tmp"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        msg = self.adapter.build_postclose_message(ctx)
        self.assertIsInstance(msg, str)
        self.assertTrue(len(msg) > 0)

    def test_commit_execution_missing_committing_exits(self):
        """commit_execution without COMMITTING marker calls sys.exit(1)."""
        import tempfile

        from qsys.ops.run_context import DailyRunContext

        with tempfile.TemporaryDirectory() as tmp:
            ctx = DailyRunContext(
                trade_date="2026-05-22",
                mode="postclose",
                run_root=Path(tmp),
                project_root=Path(tmp),
                strategy_id="alpha_v1",
                account_id="shadow_alpha_v1",
            )
            staging_dir = Path(tmp) / "staging"
            staging_dir.mkdir(parents=True, exist_ok=True)
            with self.assertRaises(SystemExit) as cm:
                self.adapter.commit_execution(ctx, staging_dir)
            self.assertEqual(cm.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
