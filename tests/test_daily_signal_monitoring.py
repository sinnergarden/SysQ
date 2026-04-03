import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.live.signal_monitoring import (
    _default_benchmark_loader,
    build_signal_quality_blockers,
    collect_signal_quality_snapshot,
    evaluate_signal_basket,
    inspect_signal_basket_price_readiness,
    save_signal_basket,
    write_signal_quality_outputs,
)


class TestDailySignalMonitoring(unittest.TestCase):
    def test_evaluate_signal_basket_calculates_returns_and_excess(self):
        basket = pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "score": 0.9,
                    "score_rank": 1,
                    "weight": 0.6,
                    "price": 10.0,
                    "signal_date": "2025-01-02",
                    "execution_date": "2025-01-03",
                    "price_basis_date": "2025-01-02",
                    "model_name": "demo",
                    "model_path": "data/models/demo",
                    "universe": "csi300",
                },
                {
                    "symbol": "BBB",
                    "score": 0.7,
                    "score_rank": 2,
                    "weight": 0.4,
                    "price": 20.0,
                    "signal_date": "2025-01-02",
                    "execution_date": "2025-01-03",
                    "price_basis_date": "2025-01-02",
                    "model_name": "demo",
                    "model_path": "data/models/demo",
                    "universe": "csi300",
                },
            ]
        )

        def price_loader(symbols, start_date, end_date):
            self.assertEqual(symbols, ["AAA", "BBB"])
            return pd.DataFrame(
                [
                    {"date": "2025-01-02", "symbol": "AAA", "close": 10.0},
                    {"date": "2025-01-03", "symbol": "AAA", "close": 11.0},
                    {"date": "2025-01-02", "symbol": "BBB", "close": 20.0},
                    {"date": "2025-01-03", "symbol": "BBB", "close": 18.0},
                ]
            )

        with patch("qsys.live.signal_monitoring._trading_days_between", return_value=1):
            observation = evaluate_signal_basket(
                basket,
                as_of_date="2025-01-03",
                price_loader=price_loader,
                benchmark_loader=lambda universe, start_date, end_date: 0.05,
            )

        self.assertEqual(observation["status"], "success")
        self.assertAlmostEqual(observation["equal_weight_return"], 0.0)
        self.assertAlmostEqual(observation["weighted_return"], 0.02)
        self.assertAlmostEqual(observation["weighted_excess_return"], -0.03)
        self.assertAlmostEqual(observation["top1_return"], 0.10)
        self.assertAlmostEqual(observation["top5_mean_return"], 0.0)
        self.assertAlmostEqual(observation["positive_ratio"], 0.5)

    def test_collect_signal_quality_snapshot_builds_horizon_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            signal_dir = Path(tmpdir)
            for signal_date in ["2025-01-03", "2025-01-02", "2025-01-01"]:
                save_signal_basket(
                    pd.DataFrame(
                        [
                            {
                                "symbol": "AAA",
                                "score": 0.5,
                                "score_rank": 1,
                                "weight": 1.0,
                                "price": 10.0,
                                "signal_date": signal_date,
                                "execution_date": signal_date,
                                "price_basis_date": signal_date,
                                "model_name": "demo",
                                "model_path": "data/models/demo",
                                "universe": "csi300",
                            }
                        ]
                    ),
                    output_dir=signal_dir,
                    signal_date=signal_date,
                )

            fake_observations = {
                "2025-01-03": {
                    "signal_date": "2025-01-03",
                    "execution_date": "2025-01-03",
                    "as_of_date": "2025-01-06",
                    "holding_days": 1,
                    "status": "success",
                    "reason": "ok",
                    "basket_size": 1,
                    "coverage_count": 1,
                    "coverage_ratio": 1.0,
                    "equal_weight_return": 0.01,
                    "weighted_return": 0.01,
                    "benchmark_return": 0.005,
                    "weighted_excess_return": 0.005,
                },
                "2025-01-02": {
                    "signal_date": "2025-01-02",
                    "execution_date": "2025-01-02",
                    "as_of_date": "2025-01-06",
                    "holding_days": 2,
                    "status": "success",
                    "reason": "ok",
                    "basket_size": 1,
                    "coverage_count": 1,
                    "coverage_ratio": 1.0,
                    "equal_weight_return": 0.03,
                    "weighted_return": 0.03,
                    "benchmark_return": 0.01,
                    "weighted_excess_return": 0.02,
                },
                "2025-01-01": {
                    "signal_date": "2025-01-01",
                    "execution_date": "2025-01-01",
                    "as_of_date": "2025-01-06",
                    "holding_days": 3,
                    "status": "success",
                    "reason": "ok",
                    "basket_size": 1,
                    "coverage_count": 1,
                    "coverage_ratio": 1.0,
                    "equal_weight_return": -0.02,
                    "weighted_return": -0.02,
                    "benchmark_return": -0.01,
                    "weighted_excess_return": -0.01,
                },
            }

            def fake_evaluate(basket_df, **kwargs):
                signal_date = str(basket_df["signal_date"].iloc[0])
                return dict(fake_observations[signal_date])

            with patch("qsys.live.signal_monitoring.evaluate_signal_basket", side_effect=fake_evaluate):
                snapshot = collect_signal_quality_snapshot(as_of_date="2025-01-06", signal_dir=signal_dir)

        self.assertEqual(snapshot.summary["horizon_1d"]["signal_date"], "2025-01-03")
        self.assertEqual(snapshot.summary["horizon_2d"]["signal_date"], "2025-01-02")
        self.assertEqual(snapshot.summary["horizon_3d"]["signal_date"], "2025-01-01")
        self.assertEqual(snapshot.summary["recent_vintage_count"], 3)
        self.assertAlmostEqual(snapshot.summary["recent_vintage_win_rate"], 2 / 3)
        self.assertAlmostEqual(snapshot.summary["recent_vintage_avg_weighted_return"], 0.0066666667, places=6)

    def test_price_readiness_classifies_missing_end_price(self):
        basket = pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "signal_date": "2025-01-02",
                    "execution_date": "2025-01-03",
                    "price_basis_date": "2025-01-02",
                },
                {
                    "symbol": "BBB",
                    "signal_date": "2025-01-02",
                    "execution_date": "2025-01-03",
                    "price_basis_date": "2025-01-02",
                },
            ]
        )

        def price_loader(symbols, start_date, end_date):
            return pd.DataFrame(
                [
                    {"date": "2025-01-02", "symbol": "AAA", "close": 10.0},
                    {"date": "2025-01-03", "symbol": "AAA", "close": 10.5},
                    {"date": "2025-01-02", "symbol": "BBB", "close": 20.0},
                ]
            )

        readiness = inspect_signal_basket_price_readiness(
            basket,
            as_of_date="2025-01-03",
            price_loader=price_loader,
        )

        self.assertEqual(readiness["status"], "partial")
        self.assertEqual(readiness["reason"], "missing_end_price")
        self.assertEqual(readiness["ready_count"], 1)
        self.assertEqual(readiness["missing_end_symbols"], ["BBB"])

    def test_build_signal_quality_blockers_only_flags_failed_or_partial_horizons(self):
        summary = {
            "horizon_1d": {"status": "success", "reason": "ok", "signal_date": "2025-01-03"},
            "horizon_2d": {"status": "failed", "reason": "missing_end_price", "signal_date": "2025-01-02"},
            "horizon_3d": {"status": "partial", "reason": "missing_start_price", "signal_date": "2025-01-01"},
        }
        blockers = build_signal_quality_blockers(summary, required_horizons=(1, 2, 3))
        self.assertEqual(len(blockers), 2)
        self.assertIn("horizon_2d", blockers[0])
        self.assertIn("horizon_3d", blockers[1])

    def test_default_benchmark_loader_uses_universe_symbols_for_signal_date(self):
        price_frame = pd.DataFrame(
            [
                {"date": "2025-01-02", "symbol": "AAA", "close": 10.0},
                {"date": "2025-01-03", "symbol": "AAA", "close": 11.0},
                {"date": "2025-01-02", "symbol": "BBB", "close": 20.0},
                {"date": "2025-01-03", "symbol": "BBB", "close": 21.0},
            ]
        )
        with patch("qsys.live.signal_monitoring._load_universe_symbols_for_date", return_value=["AAA", "BBB"]), patch(
            "qsys.live.signal_monitoring._default_price_loader",
            return_value=price_frame,
        ):
            benchmark_return = _default_benchmark_loader("csi300", "2025-01-02", "2025-01-03")

        self.assertAlmostEqual(benchmark_return, 0.075)

    def test_write_signal_quality_outputs_persists_summary_and_details(self):
        snapshot = type("Snapshot", (), {
            "summary": {"status": "success", "recent_vintage_count": 2},
            "observations": pd.DataFrame([
                {"signal_date": "2025-01-03", "weighted_return": 0.01},
                {"signal_date": "2025-01-02", "weighted_return": -0.02},
            ]),
        })()

        with tempfile.TemporaryDirectory() as tmpdir:
            written = write_signal_quality_outputs(snapshot, output_dir=tmpdir, as_of_date="2025-01-06")
            self.assertTrue(Path(written["signal_quality_vintages"]).exists())
            self.assertTrue(Path(written["signal_quality_summary"]).exists())
            with open(written["signal_quality_summary"], "r", encoding="utf-8") as handle:
                summary = json.load(handle)
            self.assertEqual(summary["recent_vintage_count"], 2)


if __name__ == "__main__":
    unittest.main()
