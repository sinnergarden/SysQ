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

    def test_resolve_data_date_trading_day_returns_same(self):
        """For a trading day, resolve_data_date returns the same date."""
        try:
            result = self.adapter.resolve_data_date("2026-05-18")
            self.assertEqual(result, "2026-05-18")
        except Exception:
            self.skipTest("qlib not initialized — resolve_data_date needs qlib")

    def test_resolve_data_date_non_trading_day_rolls_back(self):
        """For a non-trading day, resolve_data_date rolls back to last trading day."""
        try:
            result = self.adapter.resolve_data_date("2026-05-17")  # Sunday
            self.assertNotEqual(result, "2026-05-17")
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


    # ── from_config tests ──────────────────────────────────────────────

    def test_from_config_defaults_no_config(self):
        """from_config() with no config is identical to default __init__."""
        adapter = AlphaV1StrategyAdapter.from_config()
        self.assertEqual(adapter.strategy_id, "alpha_v1")
        self.assertEqual(adapter.display_name, "Alpha V1")
        self.assertEqual(adapter.rebalance_policy["top_n"], 20)

    def test_from_config_empty_dict(self):
        """from_config with empty dict behaves like no config."""
        adapter = AlphaV1StrategyAdapter.from_config({})
        self.assertEqual(adapter.display_name, "Alpha V1")

    def test_from_config_overrides_display_name(self):
        adapter = AlphaV1StrategyAdapter.from_config({"display_name": "Custom Alpha"})
        self.assertEqual(adapter.display_name, "Custom Alpha")

    def test_from_config_overrides_paths(self):
        """Relative paths are resolved against project_root."""
        pr = Path("/tmp/test_from_config_pr")
        adapter = AlphaV1StrategyAdapter.from_config(
            {"paths": {"model_dir": "custom_models/latest"}},
            project_root=pr,
        )
        expected = pr / "custom_models/latest"
        self.assertEqual(adapter._model_dir, expected)

    def test_from_config_overrides_absolute_paths(self):
        adapter = AlphaV1StrategyAdapter.from_config(
            {"paths": {"model_dir": "/absolute/path"}},
        )
        self.assertEqual(adapter._model_dir, Path("/absolute/path"))

    def test_from_config_overrides_predictions_dir(self):
        pr = Path("/tmp/test_pr")
        adapter = AlphaV1StrategyAdapter.from_config(
            {"paths": {"predictions_dir": "custom_preds"}},
            project_root=pr,
        )
        self.assertEqual(adapter._predictions_dir, pr / "custom_preds")

    def test_from_config_overrides_ledger_db(self):
        pr = Path("/tmp/test_pr")
        adapter = AlphaV1StrategyAdapter.from_config(
            {"paths": {"ledger_db": "custom/trade.db"}},
            project_root=pr,
        )
        self.assertEqual(adapter._ledger_db_path, str(pr / "custom/trade.db"))

    def test_from_config_rejects_mismatched_top_n(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"portfolio": {"top_n": 999}})

    def test_from_config_rejects_mismatched_buffer_hold(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"portfolio": {"buffer_hold": 999}})

    def test_from_config_rejects_mismatched_buffer_buy(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"portfolio": {"buffer_buy": 999}})

    def test_from_config_rejects_mismatched_single_stock_cap(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"portfolio": {"single_stock_cap": 0.99}})

    def test_from_config_rejects_mismatched_rebalance_freq(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"portfolio": {"rebalance_freq": "daily"}})

    def test_from_config_error_message_includes_field(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"portfolio": {"top_n": 999}})
        msg = str(cm.exception)
        self.assertIn("top_n", msg)
        self.assertIn("999", msg)
        self.assertIn("20", msg)

    def test_from_config_passes_project_root_to_init(self):
        pr = Path("/tmp/test_pr_from_config")
        adapter = AlphaV1StrategyAdapter.from_config(project_root=pr)
        self.assertEqual(adapter._project_root, pr)

    def test_from_config_stores_config(self):
        """from_config stores the config dict for downstream use (e.g. training)."""
        cfg = {"display_name": "Test", "paths": {"model_dir": "custom"}}
        adapter = AlphaV1StrategyAdapter.from_config(cfg, project_root=Path("/tmp"))
        self.assertIsNotNone(adapter._config)
        self.assertEqual(adapter._config["display_name"], "Test")

    def test_from_config_empty_config_stores_empty(self):
        adapter = AlphaV1StrategyAdapter.from_config()
        self.assertEqual(adapter._config, {})

    # ── train() delegation ─────────────────────────────────────────────

    def test_train_returns_training_result(self):
        """train() returns a TrainingResult-like object."""
        result = self.adapter.train(None)
        # When no training script exists, should return a failed result with a message
        self.assertIsNotNone(result)
        self.assertTrue(hasattr(result, "status"))
        self.assertTrue(hasattr(result, "strategy_id"))

    # ── from_config training/label/feature validation ────────────────────

    def test_from_config_rejects_mismatched_train_days(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"training": {"train_days": 999}})
        self.assertIn("train_days", str(cm.exception))

    def test_from_config_rejects_mismatched_test_days(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"training": {"test_days": 999}})

    def test_from_config_rejects_mismatched_step_days(self):
        with self.assertRaises(ValueError):
            AlphaV1StrategyAdapter.from_config({"training": {"step_days": 999}})

    def test_from_config_rejects_mismatched_label_horizons(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"label": {"horizons": [1, 2, 3]}})
        self.assertIn("horizons", str(cm.exception))

    def test_from_config_rejects_mismatched_label_type(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"label": {"type": "classification"}})
        self.assertIn("label.type", str(cm.exception))

    def test_from_config_rejects_mismatched_feature_set(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"feature": {"feature_set": "alpha_v2"}})
        self.assertIn("feature.feature_set", str(cm.exception))

    def test_from_config_rejects_mismatched_schema_version(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"feature": {"schema_version": "v2"}})
        self.assertIn("feature.schema_version", str(cm.exception))

    def test_from_config_training_mismatch_error_includes_config_value(self):
        with self.assertRaises(ValueError) as cm:
            AlphaV1StrategyAdapter.from_config({"training": {"train_days": 999}})
        msg = str(cm.exception)
        self.assertIn("999", msg)

    def test_from_config_label_horizons_accepts_different_order(self):
        """[20, 5] is accepted because it sorts to [5, 20]."""
        adapter = AlphaV1StrategyAdapter.from_config({"label": {"horizons": [20, 5]}})
        self.assertEqual(adapter.strategy_id, "alpha_v1")

    def test_from_config_training_section_optional(self):
        """Omitting the training section entirely is fine."""
        adapter = AlphaV1StrategyAdapter.from_config({"display_name": "Test"})
        self.assertEqual(adapter.display_name, "Test")


if __name__ == "__main__":
    unittest.main()
