"""Tests for qsys.research.rolling_runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.research.rolling_runner import (
    FixtureSignalGenerator,
    RollingResearchConfig,
    RollingResearchRunner,
    _calendar_backdate,
    build_rolling_windows,
)


_MOCK_CAL = [f"2026-04-{d:02d}" for d in range(1, 31)] + \
            [f"2026-05-{d:02d}" for d in range(1, 32)]


class TestCalendarBackdate:
    def test_returns_date_before_start(self) -> None:
        result = _calendar_backdate("2026-05-01", 10)
        assert result < "2026-05-01"


class TestBuildRollingWindows:
    def test_basic_windows(self) -> None:
        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL):
            windows = build_rolling_windows(
                "2026-05-06", "2026-05-31",
                train_window_days=5, predict_window_days=5, step_days=5,
            )
            assert len(windows) >= 1
            assert windows[0].predict_start >= "2026-05-06"
            assert windows[0].train_end < windows[0].predict_start

    def test_large_train_window(self) -> None:
        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL):
            windows = build_rolling_windows(
                "2026-05-10", "2026-05-15",
                train_window_days=20, predict_window_days=2, step_days=2,
            )
            assert len(windows) >= 1
            assert windows[0].train_end < windows[0].predict_start

    def test_no_calendar_raises(self) -> None:
        with patch("qsys.data.calendar.get_trading_calendar", return_value=[]):
            with pytest.raises(ValueError, match="No trading dates"):
                build_rolling_windows("2026-05-01", "2026-05-31")


class TestFixtureSignalGenerator:
    def test_generates_valid_frame(self) -> None:
        gen = FixtureSignalGenerator(n_instruments=5)
        with patch("qsys.data.calendar.get_trading_calendar", return_value=["2026-06-15", "2026-06-16"]):
            df = gen.generate(
                train_start="2026-01-01", train_end="2026-06-14",
                predict_start="2026-06-15", predict_end="2026-06-16",
                signal_id="test", signal_run_id="test_run",
            )
            assert len(df) == 10
            assert list(df.columns[:6]) == ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"]
            assert df["signal_id"].iloc[0] == "test"


class TestRollingResearchConfig:
    def test_from_yaml(self, tmp_path: Path) -> None:
        yaml_path = tmp_path / "config.yaml"
        yaml_path.write_text("""
experiment_id: exp1
calendar:
  start_date: "2026-05-01"
  end_date: "2026-05-31"
  train_window_days: 252
  predict_window_days: 5
  step_days: 5
signal:
  signal_id: sig1
  signal_run_id: run1
labels:
  - label_id: lbl1
backtests:
  - strategy_template_id: bt1
    top_n: 20
""")
        cfg = RollingResearchConfig.from_file(yaml_path)
        assert cfg.experiment_id == "exp1"
        assert cfg.signal["signal_id"] == "sig1"
        assert cfg.labels[0]["label_id"] == "lbl1"

    def test_minimal_defaults(self) -> None:
        cfg = RollingResearchConfig.from_dict({"experiment_id": "e1"})
        assert cfg.experiment_id == "e1"
        assert cfg.title is None


class TestRollingResearchRunner:
    def test_windows_built_and_signal_saved(self, tmp_path: Path) -> None:
        config = RollingResearchConfig(
            experiment_id="e1",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "sig1", "signal_run_id": "run1"},
            labels=[{"label_id": "l1"}],
            backtests=[{"strategy_template_id": "bt1", "top_n": 5}],
        )
        bt_manifest = tmp_path / "bt_full" / "manifest.json"
        bt_manifest.parent.mkdir(parents=True, exist_ok=True)
        bt_manifest.write_text(json.dumps({"strategy_run_id": "sr1", "backtest_id": "bt1"}))

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me, \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb:
            me.return_value.evaluate.return_value = None
            mb.return_value.run_from_signal_cache.return_value = type("R", (), {
                "backtest_id": "bt1",
                "artifacts": {"manifest": str(bt_manifest)},
            })()

            runner = RollingResearchRunner(str(tmp_path))
            result = runner.run(
                config=config,
                signal_generator=FixtureSignalGenerator(n_instruments=3),
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        assert result["status"] == "passed"
        assert result["signal_id"] == "sig1"
        assert result["window_count"] >= 1

        from qsys.signal.store import SignalStore
        store = SignalStore(str(tmp_path))
        sig = store.load_signal_run("sig1", "run1")
        assert len(sig) > 0

        exp_dir = tmp_path / "experiments" / "e1"
        assert (exp_dir / "rolling_windows.csv").exists()
        assert (exp_dir / "rolling_research_manifest.json").exists()
        assert (exp_dir / "manifest.json").exists()
        assert (exp_dir / "signal_run_refs.csv").exists()

    def test_eval_failure_raises(self, tmp_path: Path) -> None:
        config = RollingResearchConfig(
            experiment_id="e3",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "s1", "signal_run_id": "r1"},
            labels=[{"label_id": "l1"}],
        )
        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me:
            me.return_value.evaluate.side_effect = RuntimeError("eval failed")
            runner = RollingResearchRunner(str(tmp_path))
            with pytest.raises(RuntimeError, match="eval failed"):
                runner.run(config=config, signal_generator=FixtureSignalGenerator(),
                           overwrite_signal=True, overwrite_eval=True)

    def test_backtest_missing_manifest_raises(self, tmp_path: Path) -> None:
        config = RollingResearchConfig(
            experiment_id="e4",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "s1", "signal_run_id": "r1"},
            backtests=[{"strategy_template_id": "bt1"}],
        )
        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me, \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb:
            me.return_value.evaluate.return_value = None
            mb.return_value.run_from_signal_cache.return_value = type("R", (), {
                "backtest_id": "bt1", "artifacts": {},
            })()
            runner = RollingResearchRunner(str(tmp_path))
            with pytest.raises(RuntimeError, match="missing artifacts"):
                runner.run(config=config, signal_generator=FixtureSignalGenerator(),
                           overwrite_signal=True, overwrite_eval=True)

    def test_backtest_distinct_manifest_ids(self, tmp_path: Path) -> None:
        bt_manifest = tmp_path / "bt_distinct" / "manifest.json"
        bt_manifest.parent.mkdir(parents=True, exist_ok=True)
        bt_manifest.write_text(json.dumps({"strategy_run_id": "real_sr1", "backtest_id": "real_bt1"}))
        config = RollingResearchConfig(
            experiment_id="e5",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "s1", "signal_run_id": "r1"},
            backtests=[{"strategy_template_id": "bt1"}],
        )
        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me, \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb:
            me.return_value.evaluate.return_value = None
            mb.return_value.run_from_signal_cache.return_value = type("R", (), {
                "backtest_id": "wrong_bt",
                "artifacts": {"manifest": str(bt_manifest)},
            })()
            runner = RollingResearchRunner(str(tmp_path))
            result = runner.run(config=config, signal_generator=FixtureSignalGenerator(),
                                overwrite_signal=True, overwrite_eval=True,
                                overwrite_backtest=True, overwrite_experiment=True)
        assert result["status"] == "passed"
        exp_dir = tmp_path / "experiments" / "e5"
        bt_refs = pd.read_csv(exp_dir / "backtest_refs.csv")
        assert bt_refs.iloc[0]["strategy_run_id"] == "real_sr1"
        assert bt_refs.iloc[0]["backtest_id"] == "real_bt1"

    def test_overwrite_signal_protection(self, tmp_path: Path) -> None:
        from qsys.signal.store import SignalStore
        store = SignalStore(str(tmp_path))
        store.save_signal_run("s1", "r1",
            pd.DataFrame({"trade_date": ["2026-05-18"], "data_date": ["2026-05-15"],
                          "instrument": ["000001.SZ"], "signal_id": ["s1"], "signal_run_id": ["r1"], "score": [0.5]}),
            overwrite=True)

        config = RollingResearchConfig(
            experiment_id="e2",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "s1", "signal_run_id": "r1"},
        )

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me, \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb:
            me.return_value.evaluate.return_value = None
            mb.return_value.run_from_signal_cache.side_effect = lambda **kw: type("R", (), {"backtest_id": "b", "artifacts": {}})()

            with pytest.raises(Exception):
                runner = RollingResearchRunner(str(tmp_path))
                runner.run(config=config, overwrite_signal=False)
