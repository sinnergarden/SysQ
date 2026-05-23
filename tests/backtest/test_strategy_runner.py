"""Tests for BacktestRunner skeleton — boundary checks, mode validation, no IO."""

from __future__ import annotations

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
        runner = BacktestRunner(artifact_mode="full")
        assert runner.artifact_mode == "full"


# ── run_range validation ───────────────────────────────────────────────────────


class FakeSpec:
    def __init__(self, strategy_id: str = "test_strat"):
        self.strategy_id = strategy_id


class FakeStrategyWithHook:
    """A fake strategy that implements generate_predictions_for_date."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_predictions_for_date(self, date_str: str) -> dict | None:
        self.calls.append(date_str)
        return {"date": date_str, "score": 0.5}


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
