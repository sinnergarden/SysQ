"""Tests for AlphaV2StrategyAdapter — framework compatibility smoke strategy."""
from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import pandas as pd

from qsys.strategy.alpha_v2.adapter import AlphaV2StrategyAdapter


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_close_data(
    instruments: list[str],
    trade_date: str,
    lookback: int = 5,
    base_price: float = 10.0,
) -> pd.DataFrame:
    """Generate fake close-price history with a momentum trend."""
    import numpy as np

    rows = []
    # Simulate lookback days of price data
    for i in range(lookback):
        dt = pd.Timestamp(trade_date) - pd.Timedelta(days=lookback - i)
        factor = 1.0 + (i / lookback) * 0.05  # upward trend
        for inst in instruments:
            rows.append({
                "trade_date": dt,
                "instrument": inst,
                "$close": base_price * factor * (1 + np.random.uniform(-0.01, 0.01)),
            })
    return pd.DataFrame(rows)


def _fake_predictions(trade_date: str = "2026-05-22") -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": trade_date, "instrument": "600001",
         "score": 0.05, "model_name": "alpha_v2_smoke_momentum",
         "mainline_object_name": "alpha_v2_smoke"},
        {"trade_date": trade_date, "instrument": "600002",
         "score": 0.03, "model_name": "alpha_v2_smoke_momentum",
         "mainline_object_name": "alpha_v2_smoke"},
        {"trade_date": trade_date, "instrument": "600003",
         "score": -0.01, "model_name": "alpha_v2_smoke_momentum",
         "mainline_object_name": "alpha_v2_smoke"},
    ])


# ── Tests ──────────────────────────────────────────────────────────────────


class TestAlphaV2Identity(unittest.TestCase):
    """Identity and config properties."""

    def setUp(self):
        self.adapter = AlphaV2StrategyAdapter()

    def test_strategy_id(self):
        self.assertEqual(self.adapter.strategy_id, "alpha_v2")

    def test_account_id(self):
        self.assertEqual(self.adapter.account_id, "shadow_alpha_v2")

    def test_display_name_default(self):
        self.assertEqual(self.adapter.display_name, "Alpha V2 Smoke")

    def test_universe(self):
        self.assertEqual(self.adapter.universe, "csi300")

    def test_feature_set(self):
        self.assertEqual(self.adapter.feature_set, "alpha_v2_smoke")

    def test_model_version(self):
        self.assertEqual(self.adapter.model_version, "alpha_v2_smoke_202606")

    def test_signal_version(self):
        self.assertEqual(self.adapter.signal_version, "momentum_20d_rank")

    def test_rebalance_policy_defaults(self):
        policy = self.adapter.rebalance_policy
        self.assertEqual(policy["top_n"], 10)
        self.assertEqual(policy["buffer_hold"], 30)
        self.assertEqual(policy["buffer_buy"], 20)
        self.assertEqual(policy["single_stock_cap"], 0.10)
        self.assertEqual(policy["rebalance_freq"], "weekly")


class TestAlphaV2FromConfig(unittest.TestCase):
    """from_config loads YAML config correctly."""

    def setUp(self):
        self.adapter = AlphaV2StrategyAdapter.from_config()

    def test_empty_config_uses_defaults(self):
        self.assertEqual(self.adapter.strategy_id, "alpha_v2")

    def test_display_name_override(self):
        adapter = AlphaV2StrategyAdapter.from_config({"display_name": "Custom V2"})
        self.assertEqual(adapter.display_name, "Custom V2")

    def test_path_overrides(self):
        config = {
            "paths": {
                "predictions_dir": "/custom/preds",
                "ledger_db": "/custom/db.sqlite",
            }
        }
        adapter = AlphaV2StrategyAdapter.from_config(config)
        self.assertEqual(str(adapter._predictions_dir), "/custom/preds")
        self.assertEqual(adapter._ledger_db_path, "/custom/db.sqlite")

    def test_model_path_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "model_dir"):
            AlphaV2StrategyAdapter.from_config(
                {"paths": {"model_dir": "/custom/models"}}
            )

    def test_portfolio_overrides(self):
        config = {
            "portfolio": {
                "top_n": 5,
                "buffer_hold": 10,
                "buffer_buy": 5,
                "single_stock_cap": 0.20,
                "rebalance_freq": "daily",
            }
        }
        adapter = AlphaV2StrategyAdapter.from_config(config)
        policy = adapter.rebalance_policy
        self.assertEqual(policy["top_n"], 5)
        self.assertEqual(policy["single_stock_cap"], 0.20)
        self.assertEqual(policy["rebalance_freq"], "daily")

    def test_training_lookback_days(self):
        adapter = AlphaV2StrategyAdapter.from_config(
            {"training": {"lookback_days": 10}}
        )
        self.assertEqual(adapter._lookback_days, 10)


class TestAlphaV2NoLightGBM(unittest.TestCase):
    """Adapter module has no LightGBM dependency."""

    def test_adapter_module_clean(self):
        import qsys.strategy.alpha_v2.adapter as mod
        src = mod.__file__
        if src:
            content = Path(src).read_text()
            self.assertNotIn("lightgbm", content)
            self.assertNotIn("ALPHA_V1_CANDIDATE", content)


class TestAlphaV2LoadModel(unittest.TestCase):
    """load_model — rule-based, no ML model."""

    def test_load_model_creates_model_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AlphaV2StrategyAdapter(project_root=Path(tmp))
            adapter.load_model()
            expected = Path(tmp) / "experiments/alpha_v2_models/rule_based_smoke_v1"
            self.assertTrue(expected.exists())


class TestAlphaV2Predictions(unittest.TestCase):
    """generate_predictions from close-price data."""

    def setUp(self):
        self.adapter = AlphaV2StrategyAdapter()

    def test_generate_predictions_returns_required_columns(self):
        data = _make_close_data(["600001", "600002", "600003"], "2026-05-22")
        preds = self.adapter.generate_predictions(data)
        required = {"trade_date", "instrument", "score", "model_name", "mainline_object_name"}
        self.assertTrue(required.issubset(preds.columns))
        self.assertEqual(len(preds), 3)

    def test_generate_predictions_scores_vary(self):
        """Momentum scores should differ across instruments."""
        data = _make_close_data(["600001", "600002"], "2026-05-22")
        preds = self.adapter.generate_predictions(data)
        scores = preds["score"].unique()
        self.assertGreater(len(scores), 1)

    def test_generate_predictions_empty_data_raises(self):
        empty = pd.DataFrame()
        with self.assertRaises(ValueError):
            self.adapter.generate_predictions(empty)

    def test_generate_predictions_single_instrument(self):
        data = _make_close_data(["600001"], "2026-05-22")
        preds = self.adapter.generate_predictions(data)
        self.assertEqual(len(preds), 1)

    def test_print_summary_does_not_crash(self):
        data = _make_close_data(["600001", "600002"], "2026-05-22")
        preds = self.adapter.generate_predictions(data)
        # Should not raise
        self.adapter.print_predictions_summary(preds)


class TestAlphaV2SavePredictions(unittest.TestCase):
    """save_predictions writes to alpha_v2 predictions dir."""

    def test_writes_to_alpha_v2_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = AlphaV2StrategyAdapter(project_root=Path(tmp))
            preds = _fake_predictions()
            run_root = Path(tmp) / "runs/2026-05-22"
            adapter.save_predictions(preds, run_root, "2026-05-22")
            expected = Path(tmp) / "experiments" / "alpha_v2_shadow_predictions" / "predictions_2026-05-22.csv"
            self.assertTrue(expected.exists())


class TestAlphaV2BuildPlan(unittest.TestCase):
    """build_plan writes plan artifacts."""

    def _patch_market_snapshot(self):
        """Mock fetch_market_snapshot to return fake prices."""
        import pandas as pd
        patcher = patch(
            "qsys.ops.plan_builder.fetch_market_snapshot",
            return_value=(
                {"600001": 10.0, "600002": 9.5, "600003": 11.0},
                pd.DataFrame({"instrument": ["600001", "600002", "600003"],
                              "is_suspended": [False, False, False],
                              "is_limit_up": [False, False, False],
                              "is_limit_down": [False, False, False]}),
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_build_plan_writes_csvs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._patch_market_snapshot()
            adapter = AlphaV2StrategyAdapter(project_root=Path(tmp))
            preds = _fake_predictions()
            plan_dir = Path(tmp) / "plan"
            result = adapter.build_plan(preds, plan_dir)
            self.assertTrue(result)
            self.assertTrue((plan_dir / "target_weights.csv").exists())
            self.assertTrue((plan_dir / "order_intents.csv").exists())
            self.assertTrue((plan_dir / "rebalance_audit.csv").exists())
            self.assertTrue((plan_dir / "plan_meta.json").exists())

    def test_plan_meta_has_alpha_v2_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._patch_market_snapshot()
            adapter = AlphaV2StrategyAdapter(project_root=Path(tmp))
            preds = _fake_predictions()
            plan_dir = Path(tmp) / "plan"
            adapter.build_plan(preds, plan_dir)
            meta = json.loads((plan_dir / "plan_meta.json").read_text())
            self.assertEqual(meta["strategy_id"], "alpha_v2")
            self.assertEqual(meta["strategy_version"], "alpha_v2_smoke_202606")

    def test_load_plan_instruments(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._patch_market_snapshot()
            adapter = AlphaV2StrategyAdapter(project_root=Path(tmp))
            preds = _fake_predictions()
            plan_dir = Path(tmp) / "plan"
            adapter.build_plan(preds, plan_dir)
            instruments = adapter.load_plan_instruments(plan_dir)
            self.assertIn("600001", instruments)
            self.assertEqual(len(instruments), 3)


class TestAlphaV2Training(unittest.TestCase):
    """train returns no-training-required result."""

    def setUp(self):
        self.adapter = AlphaV2StrategyAdapter()

    def test_train_returns_training_result(self):
        from qsys.model.training import TrainingResult

        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_context(Path(tmp))
            result = self.adapter.train(ctx)
            self.assertIsInstance(result, TrainingResult)
            self.assertEqual(result.status, "success")
            self.assertEqual(result.strategy_id, "alpha_v2")
            self.assertEqual(result.model_version, "alpha_v2_smoke_202606")
            self.assertIn("no training required", result.message)

    def test_train_in_debug_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_context(Path(tmp), debug=True)
            result = self.adapter.train(ctx)
            self.assertEqual(result.status, "success")


class TestAlphaV2StrategyCandidateProtocol(unittest.TestCase):
    """Adapter satisfies StrategyCandidate runtime_checkable protocol."""

    def test_is_strategy_candidate(self):
        from qsys.strategy.base import StrategyCandidate

        adapter = AlphaV2StrategyAdapter()
        self.assertIsInstance(adapter, StrategyCandidate)


class TestAlphaV2ShouldRebalance(unittest.TestCase):
    """should_rebalance logic."""

    def test_default_not_weekly_returns_true(self):
        adapter = AlphaV2StrategyAdapter()
        adapter._rebalance_freq = "daily"
        self.assertTrue(adapter.should_rebalance("2026-05-22"))

    def test_weekly_first_time_returns_true(self):
        adapter = AlphaV2StrategyAdapter()
        self.assertTrue(adapter.should_rebalance("2026-05-22"))


class TestAlphaV2Notifications(unittest.TestCase):
    """Notification methods produce output without crashing."""

    def setUp(self):
        self.adapter = AlphaV2StrategyAdapter()

    def test_build_preopen_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_context(Path(tmp))
            preds = _fake_predictions()
            msg = self.adapter.build_preopen_message(ctx, False, preds)
            self.assertIn("Alpha V2 Smoke", msg)
            self.assertIn("2026-05-22", msg)

    def test_build_postclose_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_context(Path(tmp))
            msg = self.adapter.build_postclose_message(ctx)
            self.assertIn("Alpha V2 Smoke", msg)
            self.assertIn("2026-05-22", msg)

    def test_load_artifacts_from_missing_dir_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = _fake_context(Path(tmp))
            result = self.adapter.load_artifacts_for_notification(ctx)
            self.assertIsNone(result)


class TestAlphaV2FetchOpenPrices(unittest.TestCase):
    """fetch_open_prices — strategy-agnostic qlib usage."""

    def test_empty_instruments_returns_empty(self):
        adapter = AlphaV2StrategyAdapter()
        result = adapter.fetch_open_prices("2026-05-22", [])
        self.assertEqual(result, {})


class TestAlphaV2ResolveDataDate(unittest.TestCase):
    """resolve_data_date — fallback to input if qlib unavailable."""

    def test_fallback_to_input(self):
        adapter = AlphaV2StrategyAdapter()
        result = adapter.resolve_data_date("2026-05-22")
        self.assertEqual(result, "2026-05-22")


# ── Test helpers ───────────────────────────────────────────────────────────

def _fake_context(project_root: Path, debug: bool = False) -> object:
    from types import SimpleNamespace
    return SimpleNamespace(
        trade_date="2026-05-22",
        mode="train",
        run_root=project_root / "run",
        project_root=project_root,
        strategy_id="alpha_v2",
        account_id="shadow_alpha_v2",
        debug_run=debug,
        no_notify=True,
        force_rerun=False,
        reason=None,
        output_dir=None,
    )


if __name__ == "__main__":
    unittest.main()
