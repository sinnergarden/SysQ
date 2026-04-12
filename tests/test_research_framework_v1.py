import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from qsys.evaluation.signal_metrics import compute_group_returns, compute_signal_metrics
from qsys.research.spec import ExperimentSpec, V1_IMPL1_FIXED_LABEL_HORIZON
from qsys.research.signal import to_signal_frame
from qsys.reports.unified_schema import unified_run_artifacts
from qsys.strategy.engine import StrategyEngine
from scripts.run_backtest import main as run_backtest_main


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
        self.assertEqual(payload["label_horizon"], V1_IMPL1_FIXED_LABEL_HORIZON)

    def test_experiment_spec_rejects_fake_label_horizon(self):
        with self.assertRaises(ValueError):
            ExperimentSpec(
                run_name="demo",
                feature_set="baseline",
                model_type="qlib_lgbm",
                label_type="forward_return",
                strategy_type="rank_topk",
                universe="csi300",
                output_dir="experiments/demo",
                label_horizon="5d",
            )

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

    def test_rank_plus_binary_gate_requires_explicit_binary(self):
        engine = StrategyEngine(top_k=3, strategy_type="rank_plus_binary_gate")
        signal_frame = pd.DataFrame({"signal_value": [0.3, 0.2, 0.1]}, index=["A", "B", "C"])
        with self.assertRaises(ValueError):
            engine.generate_target_weights(signal_frame)

    def test_rank_plus_binary_gate_filters_then_ranks(self):
        engine = StrategyEngine(top_k=2, strategy_type="rank_plus_binary_gate")
        signal_frame = pd.DataFrame(
            {"signal_value": [0.9, 0.8, 0.7, 0.6], "binary": [0, 1, 1, 0]},
            index=["A", "B", "C", "D"],
        )
        weights = engine.generate_target_weights(signal_frame)
        self.assertEqual(list(weights.keys()), ["B", "C"])

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
        metrics = compute_signal_metrics(panel, label_horizon=V1_IMPL1_FIXED_LABEL_HORIZON)
        groups = compute_group_returns(panel, label_horizon=V1_IMPL1_FIXED_LABEL_HORIZON)
        paths = unified_run_artifacts("experiments")
        self.assertIn("signal_metrics", paths)
        self.assertIn("group_returns", paths)
        self.assertEqual(metrics["status"], "available")
        self.assertIn("IC", metrics)
        self.assertEqual(metrics["label_horizon"], V1_IMPL1_FIXED_LABEL_HORIZON)
        self.assertFalse(groups.empty)
        self.assertIn("nav", groups.columns)
        self.assertIn("label_horizon", groups.columns)

    def test_signal_metrics_reject_fake_horizon(self):
        panel = pd.DataFrame([
            {"date": "2025-01-02", "instrument": "A", "signal_value": 1.0, "forward_return": 0.01},
            {"date": "2025-01-02", "instrument": "B", "signal_value": 0.5, "forward_return": 0.0},
        ])
        with self.assertRaises(ValueError):
            compute_signal_metrics(panel, label_horizon="5d")

    def test_signal_frame_keeps_binary_column(self):
        frame = to_signal_frame(pd.DataFrame({"score": [0.3, 0.2], "binary": [1, 0]}, index=["A", "B"]))
        self.assertIn("binary", frame.columns)
        self.assertEqual(frame.loc["A", "signal_value"], 0.3)

    def test_run_backtest_rejects_unsupported_cli_value(self):
        runner = CliRunner()
        result = runner.invoke(run_backtest_main, ["--model_type", "foo_model"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not_supported_in_v1", result.output)

    def test_run_backtest_writes_cli_driven_config_snapshot(self):
        runner = CliRunner()
        fake_result = pd.DataFrame([
            {"date": "2025-01-02", "total_assets": 1000000, "trade_count": 1, "daily_turnover": 1000},
            {"date": "2025-01-03", "total_assets": 1010000, "trade_count": 1, "daily_turnover": 1200},
        ])

        class FakeEngine:
            def __init__(self, *args, **kwargs):
                self.last_signal_metrics = {"status": "available", "IC": 0.12, "RankIC": 0.1, "ICIR": 1.1, "RankICIR": 0.9, "long_short_spread": 0.02, "label_horizon": V1_IMPL1_FIXED_LABEL_HORIZON}
                self.last_group_returns = pd.DataFrame([
                    {"date": "2025-01-02", "group": 1, "mean_return": 0.01, "nav": 1.01, "label_horizon": V1_IMPL1_FIXED_LABEL_HORIZON},
                    {"date": "2025-01-02", "group": 5, "mean_return": -0.01, "nav": 0.99, "label_horizon": V1_IMPL1_FIXED_LABEL_HORIZON},
                ])

            def run(self):
                return fake_result

            def save_report(self, output_dir, prefix="backtest"):
                return {"daily": str(Path(output_dir) / f"{prefix}_daily.csv")}

        class FakeSection:
            name = "Performance"
            metrics = {"total_return": "1.00%"}

        class FakeReport:
            def __init__(self):
                self.sections = [FakeSection()]
                self.artifacts = {}

            def to_markdown(self):
                return "ok"

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "models" / "qlib_lgbm_extended"
            model_dir.mkdir(parents=True)
            (model_dir / "meta.yaml").write_text("feature_set: extended\n", encoding="utf-8")
            experiments_dir = root / "experiments"
            experiments_dir.mkdir(parents=True)
            with patch("scripts.run_backtest.cfg.get_path", return_value=root), \
                 patch("scripts.run_backtest.BacktestEngine", FakeEngine), \
                 patch("scripts.run_backtest.BacktestReport.from_backtest_result", return_value=FakeReport()), \
                 patch("scripts.run_backtest.BacktestReport.save", return_value=str(root / "experiments" / "reports" / "backtest.json")):
                result = runner.invoke(
                    run_backtest_main,
                    [
                        "--model_path", str(model_dir),
                        "--start", "2025-01-02",
                        "--end", "2025-01-03",
                        "--feature_set", "phase123",
                        "--model_type", "qlib_lgbm",
                        "--label_type", "forward_return",
                        "--strategy_type", "rank_topk",
                        "--rebalance_mode", "full_rebalance",
                        "--rebalance_freq", "weekly",
                        "--inference_freq", "daily",
                        "--retrain_freq", "weekly",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            payload = __import__("json").loads((experiments_dir / "config_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["feature_set"], "phase123")
            self.assertEqual(payload["spec_source"], "explicit_cli_plus_artifact_inference_v1")
            self.assertEqual(payload["spec_inputs"]["feature_set"]["source"], "explicit_cli")
            self.assertEqual(payload["spec_inputs"]["model_type"]["resolved"], "qlib_lgbm")


if __name__ == "__main__":
    unittest.main()
