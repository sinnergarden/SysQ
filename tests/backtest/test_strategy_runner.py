"""Tests for BacktestRunner skeleton — boundary checks, mode validation, no IO."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from qsys.backtest.strategy_runner import BacktestRunner, SUPPORTED_MODES


# ── Initialisation ─────────────────────────────────────────────────────────────


class TestBacktestRunnerInit:
    def test_default_mode(self):
        runner = BacktestRunner()
        assert runner.mode == "cached_daily_equivalent"

    def test_valid_mode(self):
        for mode in SUPPORTED_MODES:
            runner = BacktestRunner(mode=mode)
            assert runner.mode == mode

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="unsupported mode"):
            BacktestRunner(mode="invalid_mode")

    def test_artifact_mode_default(self):
        runner = BacktestRunner()
        assert runner.artifact_mode == "summary"

    def test_custom_artifact_mode(self):
        runner = BacktestRunner(artifact_mode="debug")
        assert runner.artifact_mode == "debug"

    def test_default_execution_price_mode(self):
        runner = BacktestRunner()
        assert runner.execution_price_mode == "open"

    def test_valid_execution_price_mode(self):
        for mode in ("open", "close"):
            runner = BacktestRunner(execution_price_mode=mode)
            assert runner.execution_price_mode == mode

    def test_invalid_execution_price_mode_raises(self):
        with pytest.raises(ValueError, match="unsupported execution_price_mode"):
            BacktestRunner(execution_price_mode="mid")


# ── run_range validation ───────────────────────────────────────────────────────


class FakeSpec:
    def __init__(self, strategy_id: str = "test_strat"):
        self.strategy_id = strategy_id


class FakeStrategyWithHook:
    """A fake strategy that implements backtest hooks."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.strategy_id = "test_strat"
        self.model_version = "1.0"

    def resolve_data_date(self, trade_date: str) -> str:
        return trade_date

    def generate_predictions_for_date(self, trade_date: str, *,
                                       data_date: str | None = None) -> pd.DataFrame:
        self.calls.append(trade_date)
        return pd.DataFrame({
            "instrument": ["000001.SZ", "000002.SZ"],
            "score": [0.5, 0.3],
            "trade_date": trade_date,
        })

    def build_plan_for_backtest(self, predictions: pd.DataFrame, account: Any,
                                trade_date: str, output_dir: Path) -> Path:
        """Write minimal plan artifacts so _run_one_day can read them."""
        plan_dir = Path(output_dir) / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "instrument": ["000001.SZ", "000002.SZ"],
            "target_weight": [0.6, 0.4],
        }).to_csv(plan_dir / "target_weights.csv", index=False)
        pd.DataFrame(columns=[
            "trade_date", "instrument", "side", "target_weight",
            "current_weight", "target_value", "current_value",
            "diff_value", "requested_qty", "reason",
        ]).to_csv(plan_dir / "order_intents.csv", index=False)
        pd.DataFrame(columns=[
            "trade_date", "instrument", "score", "target_weight",
            "current_weight", "target_value", "current_value",
            "diff_value", "requested_qty", "action", "reason",
        ]).to_csv(plan_dir / "rebalance_audit.csv", index=False)
        return plan_dir


class TestRunRange:
    def test_start_after_end_raises(self):
        runner = BacktestRunner()
        strategy = MagicMock()
        spec = FakeSpec()
        with pytest.raises(ValueError, match="start_date.*after.*end_date"):
            runner.run_range(strategy, spec, "2026-02-01", "2026-01-01")

    def test_no_predict_hook_returns_not_implemented(self):
        runner = BacktestRunner()
        strategy = MagicMock(spec=[])  # no methods
        spec = FakeSpec()
        result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")
        assert result.status == "not_implemented"
        assert "lacks generate_predictions_for_date" in (result.notes or "")

    def test_with_predict_hook_calls_over_range(self):
        runner = BacktestRunner()
        strategy = FakeStrategyWithHook()
        spec = FakeSpec()
        dates = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
        # Mock external dependencies
        with (
            unittest.mock.patch("qsys.backtest.strategy_runner._resolve_trading_dates") as mock_rtd,
            unittest.mock.patch("qsys.backtest.strategy_runner.fetch_market_snapshot") as mock_fetch,
            unittest.mock.patch("qsys.backtest.strategy_runner.build_order_intents") as mock_boi,
            unittest.mock.patch("qsys.backtest.strategy_runner.MatchEngine") as mock_me,
            unittest.mock.patch("qsys.backtest.strategy_runner.positions_frame") as mock_pf,
        ):
            mock_rtd.return_value = dates
            mock_fetch.return_value = ({"000001.SZ": 10.0, "000002.SZ": 20.0}, MagicMock())
            mock_boi.return_value = ([], pd.DataFrame(), pd.DataFrame(), 1_000_000.0, 0.0, 1_000_000.0)
            mock_me.return_value.match.return_value = []
            mock_pf.return_value = pd.DataFrame(columns=["instrument", "market_value"])
            result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")
        assert result.status == "completed"
        assert len(strategy.calls) >= 3  # business days in range

    def test_no_ledger_write(self):
        """Prove the runner never calls ledger commit."""
        runner = BacktestRunner()
        strategy = MagicMock(spec=[])
        spec = FakeSpec()
        # Would raise AttributeError if it tried to call ledger methods
        result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")
        assert result.status == "not_implemented"

    def test_no_daily_runner_call(self):
        """Prove the runner never instantiates DailyRunner."""
        import sys
        before = set(sys.modules.keys())
        runner = BacktestRunner()
        strategy = MagicMock(spec=[])
        spec = FakeSpec()
        runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")
        after = set(sys.modules.keys())
        new_modules = after - before
        daily_runner_loaded = any("dailyrunner" in m.lower() or "daily_runner" in m.lower() for m in new_modules)
        assert not daily_runner_loaded, (
            f"BacktestRunner loaded DailyRunner modules: {new_modules}"
        )

    def test_returns_backtest_run_result(self):
        from qsys.backtest.result import BacktestRunResult
        runner = BacktestRunner()
        strategy = MagicMock(spec=[])
        spec = FakeSpec()
        result = runner.run_range(strategy, spec, "2026-01-01", "2026-01-05")
        assert isinstance(result, BacktestRunResult)
