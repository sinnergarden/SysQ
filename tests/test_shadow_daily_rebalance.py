import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsys.backtest.portfolio import build_rank_weight_portfolio
from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
from qsys.ops.shadow_rebalance import (
    ORDER_INTENT_COLUMNS,
    POSITION_COLUMNS,
    REBALANCE_AUDIT_COLUMNS,
    TARGET_WEIGHT_COLUMNS,
    ShadowRebalanceArtifacts,
    ShadowRebalanceError,
)
from qsys.ops.state import load_json
from qsys.trader.account import Account, Position
from scripts.ops.run_shadow_daily import run_shadow_daily


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _make_usable_latest_model(base_dir: Path) -> dict[str, str]:
    model_dir = base_dir / "data" / "models" / "qlib_lgbm_extended"
    model_dir.mkdir(parents=True)
    for name in ["config_snapshot.json", "training_summary.json", "decisions.json", "meta.yaml", "model.pkl"]:
        (model_dir / name).write_text("{}\n", encoding="utf-8")
    payload = build_latest_shadow_model_payload(
        model_name="qlib_lgbm_extended",
        model_path=str(model_dir),
        mainline_object_name="feature_173",
        bundle_id="bundle_feature_173",
        train_run_id="shadow_retrain_2026-04-25_090807",
        trained_at="2026-04-25T09:08:07",
        status="success",
    )
    write_latest_shadow_model(base_dir, payload)
    return payload


def _fake_data_status():
    return {
        "trade_date": "2026-04-25",
        "status": "success",
        "mode": "freshness_check_only",
        "lightweight_check_only": True,
        "mainline_object_name": "feature_173",
        "health_report": {"blocking_issues": []},
    }


def _fake_feature_status():
    return {
        "trade_date": "2026-04-25",
        "status": "success",
        "mode": "readiness_check_only",
        "lightweight_check_only": True,
        "mainline_object_name": "feature_173",
        "degradation_level": "core_ok",
        "notes": ["lightweight_check_only"],
    }


def _fake_inference(*, trade_date, model_payload, output_dir, universe):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = output_dir / "predictions.csv"
    with predictions_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "trade_date",
                "instrument",
                "score",
                "model_name",
                "mainline_object_name",
                "bundle_id",
                "train_run_id",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "trade_date": trade_date,
                "instrument": "SH600000",
                "score": 0.23,
                "model_name": model_payload["model_name"],
                "mainline_object_name": model_payload["mainline_object_name"],
                "bundle_id": model_payload["bundle_id"],
                "train_run_id": model_payload["train_run_id"],
            }
        )
        writer.writerow(
            {
                "trade_date": trade_date,
                "instrument": "SZ000001",
                "score": 0.17,
                "model_name": model_payload["model_name"],
                "mainline_object_name": model_payload["mainline_object_name"],
                "bundle_id": model_payload["bundle_id"],
                "train_run_id": model_payload["train_run_id"],
            }
        )
    _write_json(
        output_dir / "inference_summary.json",
        {
            "trade_date": trade_date,
            "model_name": model_payload["model_name"],
            "model_path": model_payload["model_path"],
            "mainline_object_name": model_payload["mainline_object_name"],
            "bundle_id": model_payload["bundle_id"],
            "train_run_id": model_payload["train_run_id"],
            "prediction_count": 60,
            "score_min": 0.17,
            "score_max": 0.23,
            "score_mean": 0.20,
            "status": "success",
        },
    )
    return type("InferenceArtifacts", (), {
        "predictions_path": str(predictions_path),
        "inference_summary_path": str(output_dir / "inference_summary.json"),
        "prediction_count": 60,
    })()


def _fake_market_snapshot(trade_date, instruments):
    prices = {instrument: 10.0 + idx for idx, instrument in enumerate(sorted(instruments))}
    market_status = pd.DataFrame(
        {
            "is_suspended": False,
            "is_limit_up": False,
            "is_limit_down": False,
        },
        index=sorted(instruments),
    )
    return prices, market_status


class TestShadowDailyRebalance(unittest.TestCase):
    def test_successful_rebalance_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()
            ), patch("scripts.ops.run_shadow_daily.run_shadow_daily_inference", side_effect=_fake_inference), patch(
                "qsys.ops.shadow_rebalance.fetch_market_snapshot", side_effect=_fake_market_snapshot
            ):
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            self.assertTrue((run_dir / "05_shadow" / "target_weights.csv").exists())
            self.assertTrue((run_dir / "05_shadow" / "order_intents.csv").exists())
            self.assertTrue((run_dir / "05_shadow" / "execution_summary.json").exists())
            self.assertTrue((run_dir / "05_shadow" / "account_after.json").exists())
            self.assertTrue((run_dir / "05_shadow" / "positions_after.csv").exists())
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "success")
            self.assertEqual(summary["overall_status"], "success")
            self.assertEqual(summary["decision_status"], "shadow_rebalanced")
            self.assertEqual(summary["price_mode"], "shadow_mark_price")

    def test_ledger_persistence_across_runs(self):
        def varied_inference(*, trade_date, model_payload, output_dir, universe):
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            predictions_path = output_dir / "predictions.csv"
            rows = [
                ("SH600000", 0.23),
                ("SZ000001", 0.17),
            ]
            if trade_date == "2026-04-26":
                rows = [
                    ("SH600000", 0.11),
                    ("SZ000002", 0.29),
                ]
            with predictions_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "trade_date",
                        "instrument",
                        "score",
                        "model_name",
                        "mainline_object_name",
                        "bundle_id",
                        "train_run_id",
                    ],
                )
                writer.writeheader()
                for instrument, score in rows:
                    writer.writerow(
                        {
                            "trade_date": trade_date,
                            "instrument": instrument,
                            "score": score,
                            "model_name": model_payload["model_name"],
                            "mainline_object_name": model_payload["mainline_object_name"],
                            "bundle_id": model_payload["bundle_id"],
                            "train_run_id": model_payload["train_run_id"],
                        }
                    )
            _write_json(output_dir / "inference_summary.json", {"trade_date": trade_date, "status": "success", "prediction_count": 60})
            return type("InferenceArtifacts", (), {
                "predictions_path": str(predictions_path),
                "inference_summary_path": str(output_dir / "inference_summary.json"),
                "prediction_count": 60,
            })()

        def exact_resolution(requested_date, **kwargs):
            return {
                "requested_date": requested_date,
                "resolved_trade_date": requested_date,
                "last_qlib_date": requested_date,
                "status": "success",
                "reason": "requested_date is available in qlib",
                "is_exact_match": True,
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily.resolve_daily_trade_date", side_effect=exact_resolution), patch(
                "scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()
            ), patch("scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()), patch(
                "scripts.ops.run_shadow_daily.run_shadow_daily_inference", side_effect=varied_inference
            ), patch("qsys.ops.shadow_rebalance.fetch_market_snapshot", side_effect=_fake_market_snapshot):
                first = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test", trade_date="2026-04-25")
                second = run_shadow_daily(base_dir, run_id="shadow_2026-04-26_090807", triggered_by="test", trade_date="2026-04-26")

            account = load_json(base_dir / "shadow" / "account.json")
            positions = pd.read_csv(base_dir / "shadow" / "positions.csv")
            ledger = pd.read_csv(base_dir / "shadow" / "ledger.csv")
            self.assertEqual(account["last_run_id"], "shadow_2026-04-26_090807")
            self.assertFalse(positions.empty)
            self.assertGreaterEqual(len(ledger), 2)
            self.assertIn("shadow_2026-04-25_090807", set(ledger["run_id"]))
            self.assertIn("shadow_2026-04-26_090807", set(ledger["run_id"]))
            self.assertEqual(load_json(Path(second["run_dir"]) / "daily_summary.json")["overall_status"], "success")

    def test_no_order_day_keeps_artifact_contract_stable(self):
        def no_order_market_snapshot(trade_date, instruments):
            prices = {instrument: 10.0 + idx for idx, instrument in enumerate(sorted(instruments))}
            market_status = pd.DataFrame(
                {
                    "is_suspended": False,
                    "is_limit_up": False,
                    "is_limit_down": False,
                },
                index=sorted(instruments),
            )
            return prices, market_status

        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            shadow_dir = base_dir / "shadow"
            shadow_dir.mkdir(parents=True, exist_ok=True)
            _write_json(
                shadow_dir / "account.json",
                {
                    "trade_date": "2026-04-24",
                    "cash": 0.0,
                    "available_cash": 0.0,
                    "market_value": 1000000.0,
                    "total_value": 1000000.0,
                    "last_run_id": "shadow_2026-04-24_090807",
                    "initial_capital": 1000000.0,
                },
            )
            pd.DataFrame(
                [
                    {
                        "instrument": "SH600000",
                        "quantity": 50000,
                        "sellable_quantity": 50000,
                        "cost_price": 10.0,
                        "last_price": 10.0,
                        "market_value": 500000.0,
                    },
                    {
                        "instrument": "SZ000001",
                        "quantity": 45454,
                        "sellable_quantity": 45454,
                        "cost_price": 11.0,
                        "last_price": 11.0,
                        "market_value": 499994.0,
                    },
                ],
                columns=POSITION_COLUMNS,
            ).to_csv(shadow_dir / "positions.csv", index=False)
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()
            ), patch("scripts.ops.run_shadow_daily.run_shadow_daily_inference", side_effect=_fake_inference), patch(
                "qsys.ops.shadow_rebalance.fetch_market_snapshot", side_effect=no_order_market_snapshot
            ), patch("qsys.ops.plan_builder.OrderGenerator.generate_orders", return_value=[]):
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            order_intents = pd.read_csv(run_dir / "05_shadow" / "order_intents.csv")
            target_weights = pd.read_csv(run_dir / "05_shadow" / "target_weights.csv")
            positions_after = pd.read_csv(run_dir / "05_shadow" / "positions_after.csv")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            rebalance_audit = pd.read_csv(run_dir / "05_shadow" / "rebalance_audit.csv")
            ledger_text = (base_dir / "shadow" / "ledger.csv").read_text(encoding="utf-8")
            self.assertEqual(order_intents.columns.tolist(), ORDER_INTENT_COLUMNS)
            self.assertTrue(order_intents.empty)
            self.assertEqual(target_weights.columns.tolist(), TARGET_WEIGHT_COLUMNS)
            self.assertEqual(rebalance_audit.columns.tolist(), REBALANCE_AUDIT_COLUMNS)
            self.assertEqual(positions_after.columns.tolist(), POSITION_COLUMNS)
            self.assertEqual(execution_summary["status"], "success")
            self.assertEqual(execution_summary["order_count"], 0)
            self.assertTrue(execution_summary["no_trade_reason_counts"])
            self.assertIn("diff_below_lot_size", execution_summary["no_trade_reason_counts"])
            self.assertIn("diff_below_lot_size", set(rebalance_audit["reason"]))
            self.assertEqual(ledger_text.strip(), "run_id,trade_date,instrument,side,quantity,price,amount,fee,status,reason")

    def test_rebalance_failure_marks_daily_failed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _make_usable_latest_model(base_dir)
            with patch("scripts.ops.run_shadow_daily._build_data_status", return_value=_fake_data_status()), patch(
                "scripts.ops.run_shadow_daily._build_feature_status", return_value=_fake_feature_status()
            ), patch("scripts.ops.run_shadow_daily.run_shadow_daily_inference", side_effect=_fake_inference), patch(
                "scripts.ops.run_shadow_daily.run_shadow_rebalance", side_effect=ShadowRebalanceError("mock rebalance boom")
            ):
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            manifest = load_json(run_dir / "manifest.json")
            summary = load_json(run_dir / "daily_summary.json")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            self.assertEqual(manifest["stage_status"]["shadow_rebalance"]["status"], "failed")
            self.assertEqual(summary["overall_status"], "failed")
            self.assertEqual(summary["decision_status"], "failed")
            self.assertEqual(execution_summary["status"], "failed")
            self.assertEqual(execution_summary["error"], "mock rebalance boom")


class TestShadowPortfolioReuse(unittest.TestCase):
    """Lightweight contract tests: shadow reuses the backtest's portfolio builder."""

    def setUp(self):
        self.instruments = [f"STOCK_{i:04d}" for i in range(50)]

    def _make_scores(self, seed: int = 0) -> pd.Series:
        rng = np.random.default_rng(seed)
        return pd.Series(rng.uniform(-1, 1, len(self.instruments)), index=self.instruments)

    def _make_account(self, held_instruments: list[str] | None = None) -> Account:
        account = Account(init_cash=1_000_000.0)
        for inst in (held_instruments or []):
            account.positions[inst] = Position(symbol=inst, total_amount=1000, sellable_amount=1000, avg_cost=10.0)
        return account

    def test_weights_sum_to_one(self):
        """Selected portfolio weights must sum to ~1.0."""
        scores = self._make_scores(seed=42)
        account = self._make_account()
        w = build_rank_weight_portfolio(scores, account, top_n=20, buffer_hold=60, buffer_buy=40, single_stock_cap=0.07)
        self.assertTrue(len(w) > 0)
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)

    def test_hold_buffer_keeps_existing_positions(self):
        """Existing positions within buffer_hold rank should be kept."""
        scores = self._make_scores(seed=42)
        # Force some held stocks to ranks 1, 5, 15, 30, 50 (within buffer_hold=60)
        held = list(scores.sort_values(ascending=False).index[[0, 4, 14, 29, 49]])
        account = self._make_account(held_instruments=held)
        w = build_rank_weight_portfolio(scores, account, top_n=20, buffer_hold=60, buffer_buy=40, single_stock_cap=0.07)
        for inst in held:
            self.assertIn(inst, w, f"held stock {inst} should be kept via buffer_hold")

    def test_new_buys_respect_buffer_buy(self):
        """New positions (not currently held) should be within buffer_buy rank."""
        scores = self._make_scores(seed=42)
        account = self._make_account()  # empty account
        w = build_rank_weight_portfolio(scores, account, top_n=20, buffer_hold=60, buffer_buy=40, single_stock_cap=0.07)
        ranks = pd.Series(range(1, len(scores) + 1), index=scores.sort_values(ascending=False).index)
        for inst in w:
            self.assertLessEqual(ranks[inst], 40, f"new buy {inst} rank {ranks[inst]} exceeds buffer_buy=40")

    def test_target_weights_columns(self):
        """_build_target_weights output includes all mandated columns."""
        from qsys.ops.shadow_rebalance import _build_target_weights
        scores = self._make_scores(seed=42)
        account = self._make_account(held_instruments=list(scores.index[:3]))
        current_prices = {inst: round(10.0 + i * 0.5, 2) for i, inst in enumerate(self.instruments)}

        pred_rows = []
        for inst in self.instruments:
            pred_rows.append({"trade_date": "2026-05-20", "instrument": inst, "score": scores[inst],
                              "model_name": "test", "mainline_object_name": "test"})
        predictions = pd.DataFrame(pred_rows)

        _, target_frame = _build_target_weights(
            predictions, current_prices, account,
            portfolio_fn=build_rank_weight_portfolio,
            top_n=20, buffer_hold=60, buffer_buy=40, single_stock_cap=0.07,
            strategy_id="alpha_v1", strategy_version="test",
        )
        self.assertEqual(target_frame.columns.tolist(), TARGET_WEIGHT_COLUMNS)
        self.assertTrue(all(target_frame["rank"] >= 1))
        self.assertTrue(all(target_frame["target_weight"] > 0))
        self.assertTrue(all(target_frame["strategy_id"] == "alpha_v1"))
        self.assertTrue(all(target_frame["strategy_version"] == "test"))
        self.assertTrue(all(target_frame["portfolio_method"] == "rank_weight_buffer"))
        self.assertAlmostEqual(target_frame["target_weight"].sum(), 1.0, places=6)
