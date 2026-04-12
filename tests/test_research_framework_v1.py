import unittest

import pandas as pd

from qsys.evaluation.signal_metrics import compute_group_returns, compute_signal_metrics
from qsys.research.spec import ExperimentSpec
from qsys.research.signal import to_signal_frame
from qsys.reports.unified_schema import unified_run_artifacts
from qsys.strategy.engine import StrategyEngine


class TestResearchFrameworkV1(unittest.TestCase):
    def test_experiment_spec_defaults(self):
        spec = ExperimentSpec(
            run_name="demo",
            feature_set="baseline",
            model_type="qlib_lgbm",
            label_type="forward_return",
            strategy_type="rank_topk",
            universe="csi300",
            output_dir="experiments/demo",
        )
        payload = spec.to_dict()
        self.assertEqual(payload["top_k"], 5)
        self.assertEqual(payload["rebalance_mode"], "full_rebalance")
        self.assertEqual(payload["rebalance_freq"], "weekly")

    def test_signal_frame_normalization(self):
        signals = pd.Series([0.3, 0.1], index=["A", "B"])
        frame = to_signal_frame(signals)
        self.assertIn("signal_value", frame.columns)
        self.assertEqual(frame.loc["A", "signal_type"], "score")

    def test_strategy_accepts_signal_frame(self):
        engine = StrategyEngine(top_k=1)
        signal_frame = pd.DataFrame({"signal_value": [0.2, 0.5]}, index=["A", "B"])
        weights = engine.generate_target_weights(signal_frame)
        self.assertEqual(list(weights.keys()), ["B"])

    def test_strategy_cash_gate_can_stay_empty(self):
        engine = StrategyEngine(top_k=3, strategy_type="rank_topk_with_cash_gate")
        signal_frame = pd.DataFrame({"signal_value": [-0.2, -0.1, 0.0]}, index=["A", "B", "C"])
        weights = engine.generate_target_weights(signal_frame)
        self.assertEqual(weights, {})

    def test_signal_metrics_and_groups(self):
        rows = []
        for day in ["2025-01-02", "2025-01-03"]:
            for idx, symbol in enumerate(["A", "B", "C", "D", "E"], start=1):
                rows.append({
                    "date": day,
                    "instrument": symbol,
                    "signal_value": float(6 - idx),
                    "forward_return": float(0.01 * (6 - idx)),
                })
        panel = pd.DataFrame(rows)
        metrics = compute_signal_metrics(panel, label_horizon="5d")
        groups = compute_group_returns(panel, label_horizon="5d")
        paths = unified_run_artifacts("experiments")
        self.assertIn("signal_metrics", paths)
        self.assertIn("group_returns", paths)
        self.assertEqual(metrics["status"], "available")
        self.assertIn("IC", metrics)
        self.assertEqual(metrics["label_horizon"], "5d")
        self.assertFalse(groups.empty)
        self.assertIn("nav", groups.columns)
        self.assertIn("label_horizon", groups.columns)


if __name__ == "__main__":
    unittest.main()
