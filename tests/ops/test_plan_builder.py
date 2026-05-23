"""Tests for qsys.ops.plan_builder."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.plan_builder import (
    ORDER_INTENT_COLUMNS,
    POSITION_COLUMNS,
    REBALANCE_AUDIT_COLUMNS,
    TARGET_WEIGHT_COLUMNS,
    build_order_intents,
    build_plan_from_predictions,
    build_target_weights,
    load_shadow_account,
    read_predictions,
)
from qsys.trader.account import Account


# ── Helpers ──────────────────────────────────────────────────────────────────

def _fake_predictions_df(trade_date: str = "2026-05-22") -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": trade_date, "instrument": "600001", "score": 0.05,
         "model_name": "test", "mainline_object_name": "test"},
        {"trade_date": trade_date, "instrument": "600002", "score": 0.03,
         "model_name": "test", "mainline_object_name": "test"},
        {"trade_date": trade_date, "instrument": "600003", "score": -0.01,
         "model_name": "test", "mainline_object_name": "test"},
    ])


# ── Tests: read_predictions ─────────────────────────────────────────────────

class TestReadPredictions(unittest.TestCase):
    """read_predictions — validation and sorting."""

    def test_reads_valid_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preds.csv"
            _fake_predictions_df().to_csv(path, index=False)
            df = read_predictions(path)
            self.assertEqual(len(df), 3)
            self.assertIn("model_name", df.columns)

    def test_missing_required_column_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preds.csv"
            pd.DataFrame({"instrument": ["600001"], "score": [0.1]}).to_csv(path, index=False)
            from qsys.ops.market_snapshot import ShadowRebalanceError
            with self.assertRaises(ShadowRebalanceError):
                read_predictions(path)

    def test_empty_csv_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preds.csv"
            pd.DataFrame(columns=["trade_date", "instrument", "score"]).to_csv(path, index=False)
            from qsys.ops.market_snapshot import ShadowRebalanceError
            with self.assertRaises(ShadowRebalanceError):
                read_predictions(path)

    def test_sorts_by_score_desc(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preds.csv"
            _fake_predictions_df().to_csv(path, index=False)
            df = read_predictions(path)
            self.assertTrue((df["score"] == df["score"].sort_values(ascending=False).values).all())


# ── Tests: load_shadow_account ──────────────────────────────────────────────

class TestLoadShadowAccount(unittest.TestCase):
    """load_shadow_account — file-based account loading."""

    def test_empty_dir_returns_new_account(self):
        with tempfile.TemporaryDirectory() as tmp:
            account, prior, positions = load_shadow_account(Path(tmp))
            self.assertIsInstance(account, Account)
            self.assertAlmostEqual(account.cash, 1_000_000.0)
            self.assertTrue(positions.empty)

    def test_existing_account_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow_dir = Path(tmp)
            account_data = {
                "cash": 500_000.0, "available_cash": 500_000.0,
                "market_value": 200_000.0, "total_value": 700_000.0,
                "initial_capital": 1_000_000.0,
            }
            (shadow_dir / "account.json").write_text(json.dumps(account_data))
            account, prior, positions = load_shadow_account(shadow_dir)
            self.assertAlmostEqual(account.cash, 500_000.0)


# ── Tests: build_target_weights ─────────────────────────────────────────────

class TestBuildTargetWeights(unittest.TestCase):
    """build_target_weights — output contract."""

    def setUp(self):
        self.account = Account(init_cash=1_000_000.0)
        self.prices = {"600001": 10.0, "600002": 9.5, "600003": 11.0}
        self.predictions = _fake_predictions_df()

    def _portfolio_fn(self, scores, account, **kwargs):
        return {inst: 1.0 / len(scores) for inst in scores.index}

    def test_returns_weights_and_frame(self):
        weights, frame = build_target_weights(
            self.predictions, self.prices, self.account,
            portfolio_fn=self._portfolio_fn,
            top_n=10, buffer_hold=30, buffer_buy=20,
            single_stock_cap=0.10,
            strategy_id="test", strategy_version="1.0",
        )
        self.assertIsInstance(weights, dict)
        self.assertIsInstance(frame, pd.DataFrame)
        self.assertEqual(frame.columns.tolist(), TARGET_WEIGHT_COLUMNS)

    def test_missing_price_instruments_raises(self):
        with self.assertRaises(Exception):
            build_target_weights(
                self.predictions, {}, self.account,
                portfolio_fn=self._portfolio_fn,
                top_n=10, buffer_hold=30, buffer_buy=20,
                single_stock_cap=0.10,
                strategy_id="test", strategy_version="1.0",
            )


# ── Tests: build_order_intents ──────────────────────────────────────────────

class TestBuildOrderIntents(unittest.TestCase):
    """build_order_intents — output contract."""

    def setUp(self):
        self.account = Account(init_cash=1_000_000.0)
        self.prices = {"600001": 10.0, "600002": 9.5}
        self.predictions = _fake_predictions_df()

    def test_returns_expected_types(self):
        target_weights = {"600001": 0.5, "600002": 0.5}
        result = build_order_intents(
            self.account, self.predictions, target_weights, self.prices, "2026-05-22",
        )
        orders, intents_df, audit_df, cash_before, mv_before, tv_before = result
        self.assertIsInstance(orders, list)
        self.assertIsInstance(intents_df, pd.DataFrame)
        self.assertIsInstance(audit_df, pd.DataFrame)
        self.assertEqual(intents_df.columns.tolist(), ORDER_INTENT_COLUMNS)
        self.assertEqual(audit_df.columns.tolist(), REBALANCE_AUDIT_COLUMNS)
        self.assertGreater(cash_before, 0)


# ── Tests: build_plan_from_predictions ──────────────────────────────────────

class TestBuildPlanFromPredictions(unittest.TestCase):
    """build_plan_from_predictions — integration."""

    def _portfolio_fn(self, scores, account, **kwargs):
        return {inst: 1.0 / len(scores) for inst in scores.index}

    def test_writes_four_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow_dir = Path(tmp) / "shadow"
            shadow_dir.mkdir(parents=True)
            output_dir = Path(tmp) / "run"
            predictions = _fake_predictions_df()

            with patch("qsys.ops.plan_builder.fetch_market_snapshot") as mock_snapshot:
                mock_snapshot.return_value = (
                    {"600001": 10.0, "600002": 9.5, "600003": 11.0},
                    pd.DataFrame(),
                )
                result = build_plan_from_predictions(
                    shadow_dir=shadow_dir,
                    trade_date="2026-05-22",
                    predictions=predictions,
                    output_dir=output_dir,
                    portfolio_fn=self._portfolio_fn,
                    top_n=10, buffer_hold=30, buffer_buy=20,
                    single_stock_cap=0.10,
                    strategy_id="test_strat", strategy_version="1.0",
                )

            plan_dir = output_dir / "plan"
            self.assertEqual(result, plan_dir)
            self.assertTrue((plan_dir / "target_weights.csv").exists())
            self.assertTrue((plan_dir / "order_intents.csv").exists())
            self.assertTrue((plan_dir / "rebalance_audit.csv").exists())
            self.assertTrue((plan_dir / "plan_meta.json").exists())

    def test_plan_meta_has_strategy_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow_dir = Path(tmp) / "shadow"
            shadow_dir.mkdir(parents=True)
            output_dir = Path(tmp) / "run"

            with patch("qsys.ops.plan_builder.fetch_market_snapshot") as mock_snapshot:
                mock_snapshot.return_value = (
                    {"600001": 10.0},
                    pd.DataFrame(),
                )
                build_plan_from_predictions(
                    shadow_dir=shadow_dir,
                    trade_date="2026-05-22",
                    predictions=_fake_predictions_df(),
                    output_dir=output_dir,
                    portfolio_fn=self._portfolio_fn,
                    top_n=10, buffer_hold=30, buffer_buy=20,
                    single_stock_cap=0.10,
                    strategy_id="test_strat", strategy_version="1.0",
                )

            meta = json.loads((output_dir / "plan" / "plan_meta.json").read_text())
            self.assertEqual(meta["strategy_id"], "test_strat")


if __name__ == "__main__":
    unittest.main()
