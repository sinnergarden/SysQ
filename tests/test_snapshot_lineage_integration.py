from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from click.testing import CliRunner

from scripts.run_backtest import main as run_backtest_main
from scripts.run_strict_eval import build_lineage_payload, load_training_snapshot, main as run_strict_eval_main


class TestSnapshotLineageIntegration(unittest.TestCase):
    def test_load_training_snapshot_marks_legacy_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            payload = load_training_snapshot(Path(tmpdir))
        self.assertEqual(payload["status"], "not_found")
        self.assertEqual(payload["lineage_status"], "legacy_or_incomplete_lineage")

    def test_build_lineage_payload_preserves_snapshot_fields(self):
        payload = build_lineage_payload(
            {
                "input_mode": "bundle_id",
                "feature_set": None,
                "bundle_id": "bundle_semantic_demo",
                "factor_variants": ["close@raw", "ret_1d@raw"],
                "bundle_resolution_status": "resolved_via_manifest_compat_layer",
                "object_layer_status": "bundle_manifest_resolved_for_train_v1",
                "strategy_spec": {"strategy_type": "rank_topk", "top_k": 5},
                "cost_spec": {"min_trade_buffer_ratio": 0.1},
                "snapshot_path": "/tmp/config_snapshot.json",
            }
        )
        self.assertEqual(payload["bundle_id"], "bundle_semantic_demo")
        self.assertEqual(payload["input_mode"], "bundle_id")
        self.assertEqual(payload["strategy_spec"]["strategy_type"], "rank_topk")
        self.assertEqual(payload["cost_spec"]["min_trade_buffer_ratio"], 0.1)

    def test_run_backtest_consumes_snapshot_lineage(self):
        runner = CliRunner()
        fake_result = pd.DataFrame([
            {"date": "2025-01-02", "total_assets": 1000000, "trade_count": 1, "daily_turnover": 1000},
            {"date": "2025-01-03", "total_assets": 1010000, "trade_count": 1, "daily_turnover": 1200},
        ])

        class FakeEngine:
            def __init__(self, *args, **kwargs):
                self.last_signal_metrics = {"status": "available", "IC": 0.12, "label_horizon": "1d_fixed_in_v1_impl1"}
                self.last_group_returns = pd.DataFrame([
                    {"date": "2025-01-02", "group": 1, "mean_return": 0.01, "nav": 1.01, "label_horizon": "1d_fixed_in_v1_impl1"},
                ])
                self.last_exposure_summary = {"status": "available"}
                self.last_exposure_timeseries = pd.DataFrame([
                    {"date": "2025-01-02", "metric": "top1_weight", "value": 0.5},
                ])
                self.last_selection_daily = pd.DataFrame([
                    {"date": "2025-01-02", "instrument": "A", "signal_value": 0.9, "target_weight": 0.5, "selected_rank": 1},
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
                self.model_info = {}

            def to_markdown(self):
                return "ok"

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "models" / "qlib_lgbm_bundle_bundle_semantic_demo"
            model_dir.mkdir(parents=True)
            (model_dir / "config_snapshot.json").write_text(json.dumps({
                "input_mode": "bundle_id",
                "bundle_id": "bundle_semantic_demo",
                "factor_variants": ["close@raw", "ret_1d@raw"],
                "bundle_resolution_status": "resolved_via_manifest_compat_layer",
                "object_layer_status": "bundle_manifest_resolved_for_train_v1",
                "label_spec": {"label_type": "forward_return", "label_horizon": 5},
                "model_spec": {"model_type": "qlib_lgbm"},
                "strategy_spec": {"strategy_type": "rank_topk", "top_k": 5},
                "cost_spec": {"min_trade_buffer_ratio": 0.0},
            }), encoding="utf-8")
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
                        "--top_k", "11",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            config_payload = json.loads((experiments_dir / "config_snapshot.json").read_text(encoding="utf-8"))
            signal_payload = json.loads((experiments_dir / "signal_metrics.json").read_text(encoding="utf-8"))
            metrics_payload = json.loads((experiments_dir / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(config_payload["lineage"]["bundle_id"], "bundle_semantic_demo")
            self.assertEqual(config_payload["lineage"]["input_mode"], "bundle_id")
            self.assertEqual(config_payload["strategy_spec"]["strategy_type"], "rank_topk")
            self.assertEqual(metrics_payload["cost_spec"]["min_trade_buffer_ratio"], 0.0)
            self.assertEqual(signal_payload["lineage"]["bundle_id"], "bundle_semantic_demo")
            self.assertNotIn("top_k", signal_payload)

    def test_run_backtest_marks_legacy_lineage_when_snapshot_missing(self):
        runner = CliRunner()
        fake_result = pd.DataFrame([
            {"date": "2025-01-02", "total_assets": 1000000, "trade_count": 1, "daily_turnover": 1000},
            {"date": "2025-01-03", "total_assets": 1010000, "trade_count": 1, "daily_turnover": 1200},
        ])

        class FakeEngine:
            def __init__(self, *args, **kwargs):
                self.last_signal_metrics = {"status": "available", "IC": 0.12, "label_horizon": "1d_fixed_in_v1_impl1"}
                self.last_group_returns = pd.DataFrame([])
                self.last_exposure_summary = {"status": "available"}
                self.last_exposure_timeseries = pd.DataFrame([])
                self.last_selection_daily = pd.DataFrame([])

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
                self.model_info = {}

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
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            config_payload = json.loads((experiments_dir / "config_snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(config_payload["lineage"]["lineage_status"], "legacy_or_incomplete_lineage")
            self.assertEqual(config_payload["feature_set"], "extended")

    def test_run_strict_eval_consumes_snapshot_lineage(self):
        class FakeMetrics:
            annual_return = 0.1
            sharpe = 1.2
            max_drawdown = -0.05
            trade_count = 3
            total_return = 0.08

        class FakeResult:
            def __init__(self, model_name, model_path):
                self.period = "2025-01~2025-02"
                self.model_name = model_name
                self.model_path = model_path
                self.start_date = "2025-01-01"
                self.end_date = "2025-02-01"
                self.top_k = 5
                self.metrics = FakeMetrics()

        class FakeEvalReport:
            def __init__(self, baseline_path, extended_path):
                self.results = [
                    FakeResult("Baseline", baseline_path),
                    FakeResult("Extended", extended_path),
                ]

            def to_markdown(self):
                return "ok"

            def summary_table(self):
                return pd.DataFrame([{"x": 1}])

        class FakeEvaluator:
            def __init__(self, top_k):
                self.top_k = top_k

            def run_comparison(self, baseline_model_path, extended_model_path, end_date=None):
                return FakeEvalReport(baseline_model_path, extended_model_path)

            def save_report(self, report, output):
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text("saved\n", encoding="utf-8")

        class FakeJsonReport:
            def __init__(self):
                self.artifacts = {}
                self.model_info = {}

            def to_markdown(self):
                return "ok"

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            baseline = root / "models" / "baseline"
            extended = root / "models" / "extended"
            baseline.mkdir(parents=True)
            extended.mkdir(parents=True)
            (baseline / "config_snapshot.json").write_text(json.dumps({
                "input_mode": "feature_set",
                "feature_set": "extended",
                "strategy_spec": {"strategy_type": "rank_topk"},
            }), encoding="utf-8")
            (extended / "config_snapshot.json").write_text(json.dumps({
                "input_mode": "bundle_id",
                "bundle_id": "bundle_semantic_demo",
                "factor_variants": ["close@raw", "ret_1d@raw"],
                "strategy_spec": {"strategy_type": "rank_topk"},
                "cost_spec": {"min_trade_buffer_ratio": 0.0},
            }), encoding="utf-8")
            output = root / "experiments" / "strict_eval_results.csv"
            with patch("scripts.run_strict_eval.StrictEvaluator", FakeEvaluator), \
                 patch("scripts.run_strict_eval.StrictEvalReport.from_evaluation_report", return_value=FakeJsonReport()), \
                 patch("scripts.run_strict_eval.StrictEvalReport.save", return_value=str(root / "experiments" / "reports" / "strict_eval.json")):
                import sys
                argv = sys.argv
                sys.argv = [
                    "run_strict_eval.py",
                    "--baseline", str(baseline),
                    "--extended", str(extended),
                    "--output", str(output),
                ]
                try:
                    run_strict_eval_main()
                finally:
                    sys.argv = argv
            config_payload = json.loads((root / "experiments" / "config_snapshot.json").read_text(encoding="utf-8"))
            signal_payload = json.loads((root / "experiments" / "signal_metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(config_payload["extended_lineage"]["bundle_id"], "bundle_semantic_demo")
            self.assertEqual(config_payload["baseline_lineage"]["input_mode"], "feature_set")
            self.assertEqual(signal_payload["signal_eval_status"], "separate_from_portfolio_backtest")
            self.assertEqual(signal_payload["extended_lineage"]["bundle_id"], "bundle_semantic_demo")


if __name__ == "__main__":
    unittest.main()
