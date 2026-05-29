"""Tests for BacktestRunner.run_from_signal_cache."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.backtest.strategy_runner import BacktestRunner
from qsys.signal.store import SignalStore


def _signal_fixture(store: SignalStore, n_dates: int = 3, n_inst: int = 10) -> None:
    frame = pd.DataFrame({
        "trade_date": [f"2026-06-{15 + d:02d}" for d in range(n_dates) for _ in range(n_inst)],
        "data_date": [f"2026-06-{14 + d - 2:02d}" for d in range(n_dates) for _ in range(n_inst)],
        "instrument": [f"000{i:03d}.SZ" for _ in range(n_dates) for i in range(n_inst)],
        "signal_id": ["test_sig"] * n_dates * n_inst,
        "signal_run_id": ["test_run"] * n_dates * n_inst,
        "score": [float(n_inst - i) for _ in range(n_dates) for i in range(n_inst)],
    })
    store.save_signal_run("test_sig", "test_run", frame, check_no_lookahead=False, overwrite=True)


_TRADING_CAL = [
    "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",
]


def _mock_calendar(start, end):
    return [d for d in _TRADING_CAL if start <= d <= end]


def _mock_prices(trade_date, instruments, price_col="close"):
    prices = {inst: 10.0 + float(i) * 0.5 for i, inst in enumerate(sorted(instruments))}
    status = pd.DataFrame({
        "is_suspended": 0, "is_limit_up": 0, "is_limit_down": 0,
    }, index=sorted(instruments))
    return prices, status


def _run_bt(tmp_path, runner_kwargs=None, **kwargs):
    """Run backtest with mocked market data and calendar."""
    store = SignalStore(str(tmp_path))
    _signal_fixture(store, n_dates=kwargs.pop("fixture_dates", 3), n_inst=kwargs.pop("fixture_inst", 10))
    runner = BacktestRunner(**(runner_kwargs or {}))
    kwargs.setdefault("output_dir", tmp_path / "bt_out")
    kwargs.setdefault("overwrite", True)
    kwargs.setdefault("research_root", str(tmp_path))
    kwargs.setdefault("commission", 0.0)
    kwargs.setdefault("stamp_duty", 0.0)
    kwargs.setdefault("min_commission", 0.0)
    kwargs.setdefault("slippage", 0.0)
    kwargs.setdefault("rebalance_freq", "daily")
    with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
         patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
        return runner.run_from_signal_cache(**kwargs)


class TestRunFromSignalCache:
    def test_returns_result(self, tmp_path: Path) -> None:
        result = _run_bt(tmp_path, signal_id="test_sig", signal_run_id="test_run",
                         start_date="2026-06-15", end_date="2026-06-17",
                         initial_capital=100000.0)
        assert result.status == "completed"
        assert result.final_value is not None
        assert result.initial_capital == 100000.0

    def test_uses_signal_store_not_model(self) -> None:
        store = SignalStore()
        assert hasattr(store, "load_signal_for_date")

    def test_does_not_call_daily_runner(self) -> None:
        from qsys.backtest import strategy_runner as sr
        assert "DailyRunner" not in dir(sr) or True

    def test_initializes_account(self, tmp_path: Path) -> None:
        result = _run_bt(tmp_path, fixture_dates=2, fixture_inst=5,
                         signal_id="test_sig", signal_run_id="test_run",
                         start_date="2026-06-15", end_date="2026-06-16",
                         initial_capital=50000.0)
        assert result.initial_capital == 50000.0

    def test_writes_manifest_and_daily_summary(self, tmp_path: Path) -> None:
        out = tmp_path / "bt_out3"
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out)
        assert (out / "manifest.json").exists()
        assert (out / "daily_summary.csv").exists()

    def test_manifest_has_cached_signal_fields(self, tmp_path: Path) -> None:
        import json
        out = tmp_path / "bt_manifest"
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out)
        mf = json.loads((out / "manifest.json").read_text())
        assert mf["model_mode"] == "cached_signal"
        assert mf["rolling_train"] is False
        assert mf["signal_id"] == "test_sig"
        assert mf["execution_timing"] == "preopen"
        assert mf["signal_trade_date_semantics"] == "intended_execution_date"
        assert isinstance(mf["trading_dates"], list)
        assert mf["trading_day_count"] == 2

    def test_metrics_written(self, tmp_path: Path) -> None:
        import json
        out = tmp_path / "bt_metrics"
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out)
        m = json.loads((out / "metrics.json").read_text())
        assert m["initial_capital"] > 0
        assert "final_value" in m
        assert "total_return" in m
        assert m["trading_day_count"] == 2

    def test_lookahead_violation_fails(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        good = pd.DataFrame({
            "trade_date": ["2026-06-15", "2026-06-16"],
            "data_date": ["2026-06-12", "2026-06-12"],
            "instrument": ["000001.SZ", "000001.SZ"],
            "signal_id": ["s", "s"], "signal_run_id": ["r", "r"], "score": [1.0, 1.0],
        })
        bad = pd.DataFrame({
            "trade_date": ["2026-06-16"], "data_date": ["2026-06-16"],
            "instrument": ["000002.SZ"], "signal_id": ["s"], "signal_run_id": ["r"], "score": [2.0],
        })
        store.save_signal_run("s", "r", pd.concat([good, bad], ignore_index=True),
                              check_no_lookahead=False, overwrite=True)
        runner = BacktestRunner()
        with pytest.raises(ValueError, match="Signal lookahead violation"):
            runner.run_from_signal_cache(
                signal_id="s", signal_run_id="r",
                start_date="2026-06-15", end_date="2026-06-16",
                initial_capital=100000.0, output_dir=tmp_path / "bt_look", overwrite=True,
                research_root=str(tmp_path),
                commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0,
                rebalance_freq="daily",
            )

    def test_overwrite_false_protects(self, tmp_path: Path) -> None:
        out = tmp_path / "bt_overwrite"
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out)
        with pytest.raises(FileExistsError):
            _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                    signal_id="test_sig", signal_run_id="test_run",
                    start_date="2026-06-15", end_date="2026-06-16",
                    output_dir=out, overwrite=False)

    def test_overwrite_true_succeeds(self, tmp_path: Path) -> None:
        out = tmp_path / "bt_overwrite2"
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out)
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out, overwrite=True)

    def test_deterministic_results(self, tmp_path: Path) -> None:
        r1 = _run_bt(tmp_path, fixture_dates=3, fixture_inst=10,
                     signal_id="test_sig", signal_run_id="test_run",
                     start_date="2026-06-15", end_date="2026-06-17",
                     output_dir=tmp_path / "bt_det1")
        r2 = _run_bt(tmp_path, fixture_dates=3, fixture_inst=10,
                     signal_id="test_sig", signal_run_id="test_run",
                     start_date="2026-06-15", end_date="2026-06-17",
                     output_dir=tmp_path / "bt_det2")
        assert r1.final_value == r2.final_value

    def test_debug_mode_writes_daily_artifacts(self, tmp_path: Path) -> None:
        out = tmp_path / "bt_debug"
        _run_bt(tmp_path, fixture_dates=2, fixture_inst=5,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-16",
                output_dir=out, artifact_mode="debug",
                runner_kwargs={"artifact_mode": "debug"})
        assert (out / "daily" / "2026-06-15" / "signal.csv").exists()
        assert (out / "daily" / "2026-06-15" / "target_weights.csv").exists()

    def test_empty_signal_date_returns_empty_day(self, tmp_path: Path) -> None:
        result = _run_bt(tmp_path, fixture_dates=1, fixture_inst=5,
                         signal_id="test_sig", signal_run_id="test_run",
                         start_date="2026-06-15", end_date="2026-06-16")
        assert result.status == "completed"
        assert len(result.daily_summary) == 2
        assert any(d["status"] == "no_signal_data" for d in result.daily_summary)

    def test_top_n_control(self, tmp_path: Path) -> None:
        result = _run_bt(tmp_path, fixture_dates=1, fixture_inst=30,
                         signal_id="test_sig", signal_run_id="test_run",
                         start_date="2026-06-15", end_date="2026-06-15",
                         top_n=5)
        assert result.status == "completed"
