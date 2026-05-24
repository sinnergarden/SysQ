"""Tests for scripts/check_dr_bt_equivalence.py.

Uses monkeypatched BacktestRunner to avoid real backtest dependencies.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# Make scripts/ importable via importlib
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_dr_bt_equivalence.py"

spec = importlib.util.spec_from_file_location(
    "check_dr_bt_equivalence", str(SCRIPT_PATH),
)
check_module = importlib.util.module_from_spec(spec)
sys.modules["check_dr_bt_equivalence"] = check_module
spec.loader.exec_module(check_module)

from qsys.backtest.result import BacktestRunResult
from qsys.backtest.strategy_runner import BacktestRunner


def _make_daily(trade_date: str, tv: float) -> dict:
    return {
        "trade_date": trade_date,
        "status": "success",
        "total_value_after": tv,
        "cash_after": tv,
        "market_value_after": 0.0,
        "order_count": 0,
        "filled_count": 0,
    }


class FakeBacktestRunner:
    """Replaces BacktestRunner.run_range with deterministic output."""

    def __init__(self, *, mode=None, artifact_mode=None, execution_price_mode=None):
        self._mode = mode
        self._artifact_mode = artifact_mode
        self._execution_price_mode = execution_price_mode

    def run_range(self, strategy, spec, *,
                  start_date, end_date, initial_capital, rebalance_freq, output_dir):
        daily = [
            _make_daily("2026-05-18", 996550.34),
            _make_daily("2026-05-19", 1006520.34),
            _make_daily("2026-05-20", 1017461.34),
            _make_daily("2026-05-21", 1008263.34),
            _make_daily("2026-05-22", 1019341.33),
        ]
        return BacktestRunResult(
            strategy_id="alpha_v1",
            backtest_id="test",
            start_date=start_date,
            end_date=end_date,
            mode="test",
            rebalance_freq=rebalance_freq or "daily",
            initial_capital=initial_capital,
            final_value=daily[-1]["total_value_after"],
            total_return=(daily[-1]["total_value_after"] - initial_capital) / initial_capital,
            status="completed",
            daily_summary=daily,
        )


class TestCheckEquivalence:
    """Integration-light tests for the equivalence check script."""

    def test_deterministic_self_check_passes(self, monkeypatch):
        """Two identical BacktestRunner runs should produce 0 diffs."""
        monkeypatch.setattr(BacktestRunner, "run_range",
                            FakeBacktestRunner().run_range)

        def fake_run(strategy_id, start, end, *, initial_capital, rebalance_freq, output_dir):
            runner = FakeBacktestRunner()
            result = runner.run_range(
                None, None,
                start_date=start, end_date=end,
                initial_capital=initial_capital,
                rebalance_freq=rebalance_freq,
                output_dir=output_dir,
            )
            return runner, result.daily_summary

        monkeypatch.setattr(check_module, "run_backtest", fake_run)

        import argparse
        args = argparse.Namespace(
            strategy="alpha_v1",
            start_date="2026-05-16",
            end_date="2026-05-22",
            initial_capital=1000000.0,
            rebalance_freq="weekly",
            baseline_dir=None,
            output_dir="/tmp/qsys_test_self_check",
        )
        exit_code = check_module.check(args)
        assert exit_code == 0, f"self-check failed with exit code {exit_code}"

    def test_baseline_comparison_detects_diff(self, monkeypatch, tmp_path):
        """Baseline with different total_value_after should report diffs."""
        monkeypatch.setattr(BacktestRunner, "run_range",
                            FakeBacktestRunner().run_range)

        def fake_run(strategy_id, start, end, *, initial_capital, rebalance_freq, output_dir):
            runner = FakeBacktestRunner()
            result = runner.run_range(
                None, None,
                start_date=start, end_date=end,
                initial_capital=initial_capital,
                rebalance_freq=rebalance_freq,
                output_dir=output_dir,
            )
            return runner, result.daily_summary

        monkeypatch.setattr(check_module, "run_backtest", fake_run)

        # Build baseline with deliberate mismatch on 2026-05-19
        def fake_load(baseline_dir):
            return {
                "2026-05-18": _make_daily("2026-05-18", 996550.34),
                "2026-05-19": _make_daily("2026-05-19", 999999.00),
                "2026-05-20": _make_daily("2026-05-20", 1017461.34),
                "2026-05-21": _make_daily("2026-05-21", 1008263.34),
                "2026-05-22": _make_daily("2026-05-22", 1019341.33),
            }

        monkeypatch.setattr(check_module, "load_baseline_daily", fake_load)

        import argparse
        args = argparse.Namespace(
            strategy="alpha_v1",
            start_date="2026-05-16",
            end_date="2026-05-22",
            initial_capital=1000000.0,
            rebalance_freq="weekly",
            baseline_dir="/tmp/fake_baseline",
            output_dir=str(tmp_path / "check_out"),
        )
        exit_code = check_module.check(args)
        assert exit_code == 1, "baseline comparison should detect diff"

    def test_result_json_written(self, monkeypatch, tmp_path):
        """Equivalence check should write equivalence_check.json."""
        monkeypatch.setattr(BacktestRunner, "run_range",
                            FakeBacktestRunner().run_range)

        def fake_run(strategy_id, start, end, *, initial_capital, rebalance_freq, output_dir):
            runner = FakeBacktestRunner()
            result = runner.run_range(
                None, None,
                start_date=start, end_date=end,
                initial_capital=initial_capital,
                rebalance_freq=rebalance_freq,
                output_dir=output_dir,
            )
            return runner, result.daily_summary

        monkeypatch.setattr(check_module, "run_backtest", fake_run)

        import argparse
        out_dir = tmp_path / "check_out"
        args = argparse.Namespace(
            strategy="alpha_v1",
            start_date="2026-05-16",
            end_date="2026-05-22",
            initial_capital=1000000.0,
            rebalance_freq="weekly",
            baseline_dir=None,
            output_dir=str(out_dir),
        )
        check_module.check(args)
        result_path = out_dir / "equivalence_check.json"
        assert result_path.exists()
        data = json.loads(result_path.read_text())
        assert data["status"] == "pass"
        assert data["strategy_id"] == "alpha_v1"
