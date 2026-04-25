import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops import build_latest_shadow_model_payload, write_latest_shadow_model
from qsys.ops.shadow_rebalance import ORDER_INTENT_COLUMNS, POSITION_COLUMNS, TARGET_WEIGHT_COLUMNS, ShadowRebalanceArtifacts, ShadowRebalanceError
from qsys.ops.state import load_json
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
            "prediction_count": 2,
            "score_min": 0.17,
            "score_max": 0.23,
            "score_mean": 0.20,
            "status": "success",
        },
    )
    return type("InferenceArtifacts", (), {
        "predictions_path": str(predictions_path),
        "inference_summary_path": str(output_dir / "inference_summary.json"),
        "prediction_count": 2,
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
                "qsys.ops.shadow_rebalance._fetch_market_snapshot", side_effect=_fake_market_snapshot
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
            _write_json(output_dir / "inference_summary.json", {"trade_date": trade_date, "status": "success"})
            return type("InferenceArtifacts", (), {
                "predictions_path": str(predictions_path),
                "inference_summary_path": str(output_dir / "inference_summary.json"),
                "prediction_count": len(rows),
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
            ), patch("qsys.ops.shadow_rebalance._fetch_market_snapshot", side_effect=_fake_market_snapshot):
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
                "qsys.ops.shadow_rebalance._fetch_market_snapshot", side_effect=no_order_market_snapshot
            ), patch("qsys.ops.shadow_rebalance.OrderGenerator.generate_orders", return_value=[]):
                result = run_shadow_daily(base_dir, run_id="shadow_2026-04-25_090807", triggered_by="test")

            run_dir = Path(result["run_dir"])
            order_intents = pd.read_csv(run_dir / "05_shadow" / "order_intents.csv")
            target_weights = pd.read_csv(run_dir / "05_shadow" / "target_weights.csv")
            positions_after = pd.read_csv(run_dir / "05_shadow" / "positions_after.csv")
            execution_summary = load_json(run_dir / "05_shadow" / "execution_summary.json")
            ledger_text = (base_dir / "shadow" / "ledger.csv").read_text(encoding="utf-8")
            self.assertEqual(order_intents.columns.tolist(), ORDER_INTENT_COLUMNS)
            self.assertTrue(order_intents.empty)
            self.assertEqual(target_weights.columns.tolist(), TARGET_WEIGHT_COLUMNS)
            self.assertEqual(positions_after.columns.tolist(), POSITION_COLUMNS)
            self.assertEqual(execution_summary["status"], "success")
            self.assertEqual(execution_summary["order_count"], 0)
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
