"""Tests for qsys.research.rolling_runner."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from qsys.research.rolling_runner import (
    FixtureSignalGenerator,
    MatrixJob,
    RollingResearchConfig,
    RollingResearchRunner,
    SignalTransformConfig,
    _calendar_backdate,
    _create_generator_from_config,
    apply_signal_transform,
    build_matrix_jobs,
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

    def test_data_date_is_prev_trading_day(self) -> None:
        """Monday trade_date -> previous Friday data_date, never weekend."""
        gen = FixtureSignalGenerator(n_instruments=3)
        # Monday 2026-06-15 with full trading calendar context
        mock_cal = [
            "2026-06-12",  # Friday
            "2026-06-15",  # Monday
            "2026-06-16",  # Tuesday
        ]
        with patch("qsys.data.calendar.get_trading_calendar", return_value=mock_cal):
            df = gen.generate(
                train_start="", train_end="",
                predict_start="2026-06-15", predict_end="2026-06-15",
                signal_id="s", signal_run_id="r",
            )
        # All data_date should be Friday 2026-06-12
        assert (df["data_date"] == "2026-06-12").all(), \
            f"Monday data_date should be Friday, got: {df['data_date'].unique()}"

    def test_passes_no_lookahead(self) -> None:
        """Generated fixture signal passes SignalStore no-lookahead validation."""
        from qsys.signal.store import _check_no_lookahead_on_frame
        gen = FixtureSignalGenerator(n_instruments=5)
        mock_cal = [
            "2026-06-12",  # Friday
            "2026-06-15",  # Monday
            "2026-06-16",  # Tuesday
        ]
        with patch("qsys.data.calendar.get_trading_calendar", return_value=mock_cal):
            df = gen.generate(
                train_start="", train_end="",
                predict_start="2026-06-15", predict_end="2026-06-16",
                signal_id="no_lookahead", signal_run_id="test",
            )
        # This should not raise ValueError
        _check_no_lookahead_on_frame(df)

    def test_fallback_monday_to_friday(self) -> None:
        """When qsys calendar is unavailable, fallback uses business days."""
        gen = FixtureSignalGenerator(n_instruments=3)
        # Monday 2026-06-15, monkeypatch get_trading_calendar to fail
        def _broken_cal(*args, **kwargs):
            raise RuntimeError("calendar unavailable")
        with patch("qsys.data.calendar.get_trading_calendar", side_effect=_broken_cal):
            df = gen.generate(
                train_start="", train_end="",
                predict_start="2026-06-15", predict_end="2026-06-16",
                signal_id="s", signal_run_id="r",
            )
        # Monday trade_date should have friday data_date in fallback too
        monday_rows = df[df["trade_date"] == "2026-06-15"]
        assert (monday_rows["data_date"] == "2026-06-12").all(), \
            f"Fallback Monday->Friday failed, got: {monday_rows['data_date'].unique()}"
        from qsys.signal.store import _check_no_lookahead_on_frame
        _check_no_lookahead_on_frame(df)

    def test_seed_controls_reproducibility(self) -> None:
        gen_a = FixtureSignalGenerator(n_instruments=3, seed=42)
        gen_b = FixtureSignalGenerator(n_instruments=3, seed=42)
        gen_c = FixtureSignalGenerator(n_instruments=3, seed=7)
        with patch("qsys.data.calendar.get_trading_calendar", return_value=["2026-06-15"]):
            df_a = gen_a.generate(train_start="", train_end="", predict_start="2026-06-15", predict_end="2026-06-15", signal_id="s", signal_run_id="r")
            df_b = gen_b.generate(train_start="", train_end="", predict_start="2026-06-15", predict_end="2026-06-15", signal_id="s", signal_run_id="r")
            df_c = gen_c.generate(train_start="", train_end="", predict_start="2026-06-15", predict_end="2026-06-15", signal_id="s", signal_run_id="r")
        pd.testing.assert_frame_equal(df_a, df_b)
        assert not df_a["score"].equals(df_c["score"])


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

    def test_v2_matrix_config(self) -> None:
        payload = {
            "experiment_id": "matrix_exp",
            "calendar": {"start_date": "2026-05-01", "end_date": "2026-05-31"},
            "generators": [
                {"generator_id": "gen_a", "type": "fixture", "params": {"n_instruments": 50, "seed": 1}},
                {"generator_id": "gen_b", "type": "fixture", "params": {"n_instruments": 50, "seed": 2}},
            ],
            "signal_transforms": [
                {"transform_id": "raw", "type": "identity"},
                {"transform_id": "z", "type": "daily_zscore"},
            ],
            "strategies": [
                {"strategy_id": "top20", "strategy_template_id": "rank_weight_top20", "top_n": 20},
            ],
            "labels": [{"label_id": "lbl1"}],
        }
        cfg = RollingResearchConfig.from_dict(payload)
        assert len(cfg.generators) == 2
        assert len(cfg.transforms) == 2
        assert len(cfg.strategies) == 1
        assert cfg.generators[0]["generator_id"] == "gen_a"


class TestCreateGeneratorFromConfig:
    def test_fixture_generator_default(self) -> None:
        gen = _create_generator_from_config({
            "generator_id": "g1", "type": "fixture",
        })
        assert isinstance(gen, FixtureSignalGenerator)
        assert gen._seed == 42
        assert gen._n_inst == 100

    def test_fixture_generator_with_params(self) -> None:
        gen = _create_generator_from_config({
            "generator_id": "g2", "type": "fixture",
            "params": {"n_instruments": 50, "seed": 7},
        })
        assert isinstance(gen, FixtureSignalGenerator)
        assert gen._seed == 7
        assert gen._n_inst == 50

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown generator type"):
            _create_generator_from_config({
                "generator_id": "bad", "type": "nope",
            })


class TestSignalTransforms:
    def test_identity_preserves_score(self) -> None:
        frame = pd.DataFrame({
            "trade_date": ["2026-05-04", "2026-05-04", "2026-05-05", "2026-05-05"],
            "score": [1.0, 2.0, 3.0, 4.0],
        })
        result = apply_signal_transform(frame, SignalTransformConfig(transform_id="raw", type="identity"))
        assert list(result["score"]) == [1.0, 2.0, 3.0, 4.0]
        assert list(result["score_raw"]) == [1.0, 2.0, 3.0, 4.0]
        assert list(result["transform_id"]) == ["raw"] * 4

    def test_daily_zscore_per_trade_date(self) -> None:
        frame = pd.DataFrame({
            "trade_date": ["2026-05-04", "2026-05-04", "2026-05-04",
                           "2026-05-05", "2026-05-05", "2026-05-05"],
            "score": [1.0, 2.0, 3.0, 10.0, 20.0, 30.0],
        })
        result = apply_signal_transform(frame, SignalTransformConfig(transform_id="z", type="daily_zscore"))
        # Per-date zscore: each date's scores should sum to ~0 (floating)
        for d in ["2026-05-04", "2026-05-05"]:
            day_scores = result[result["trade_date"] == d]["score"]
            assert abs(day_scores.mean()) < 1e-10, f"{d} mean not zero: {day_scores.mean()}"
            assert abs(day_scores.std(ddof=0) - 1.0) < 1e-10, f"{d} std not 1"

    def test_daily_zscore_zero_std(self) -> None:
        frame = pd.DataFrame({
            "trade_date": ["2026-05-04", "2026-05-04", "2026-05-04"],
            "score": [5.0, 5.0, 5.0],
        })
        result = apply_signal_transform(frame, SignalTransformConfig(transform_id="z", type="daily_zscore"))
        assert list(result["score"]) == [0.0, 0.0, 0.0]

    def test_daily_zscore_nan_std(self) -> None:
        frame = pd.DataFrame({
            "trade_date": ["2026-05-04", "2026-05-04"],
            "score": [float("nan"), float("nan")],
        })
        result = apply_signal_transform(frame, SignalTransformConfig(transform_id="z", type="daily_zscore"))
        assert list(result["score"]) == [0.0, 0.0]

    def test_unknown_type_raises(self) -> None:
        frame = pd.DataFrame({"trade_date": ["2026-05-04"], "score": [1.0]})
        with pytest.raises(ValueError, match="Unknown signal transform type"):
            apply_signal_transform(frame, SignalTransformConfig(transform_id="x", type="unknown"))


class TestBuildMatrixJobs:
    def test_2x2_expands_to_4_jobs(self) -> None:
        config = RollingResearchConfig(
            experiment_id="exp1",
            signal={"signal_id": "sig1"},
            calendar={"start_date": "2026-05-01", "end_date": "2026-05-31"},
            generators=[
                {"generator_id": "gen_a", "type": "fixture"},
                {"generator_id": "gen_b", "type": "fixture"},
            ],
            transforms=[
                {"transform_id": "raw", "type": "identity"},
                {"transform_id": "z", "type": "daily_zscore"},
            ],
            strategies=[
                {"strategy_id": "s1", "strategy_template_id": "t1", "top_n": 20},
                {"strategy_id": "s2", "strategy_template_id": "t2", "top_n": 50},
            ],
        )
        jobs = build_matrix_jobs(config)
        assert len(jobs) == 4  # 2 gen x 2 transform

        # Each job carries all strategies
        for job in jobs:
            assert len(job.strategy_configs) == 2

        gen_ids = {j.generator_id for j in jobs}
        tf_ids = {j.transform_id for j in jobs}
        assert gen_ids == {"gen_a", "gen_b"}
        assert tf_ids == {"raw", "z"}

    def test_signal_id_naming(self) -> None:
        config = RollingResearchConfig(
            experiment_id="mx",
            signal={"signal_id": "base_sig"},
            calendar={"start_date": "2026-05-01", "end_date": "2026-05-31"},
            generators=[{"generator_id": "gen_a", "type": "fixture"}],
            transforms=[{"transform_id": "raw", "type": "identity"}],
            strategies=[{"strategy_id": "s1", "strategy_template_id": "t1", "top_n": 20}],
        )
        jobs = build_matrix_jobs(config)
        assert len(jobs) == 1
        assert jobs[0].signal_id == "base_sig__gen_a__raw"
        assert jobs[0].signal_run_id == "rolling__mx__gen_a__raw__2026-05-01_2026-05-31"

    def test_v1_config_no_generators_returns_empty(self) -> None:
        config = RollingResearchConfig(
            experiment_id="v1",
            calendar={"start_date": "2026-05-01", "end_date": "2026-05-31"},
            signal={"signal_id": "sig1", "signal_run_id": "run1"},
        )
        jobs = build_matrix_jobs(config)
        assert jobs == []


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


# ── Matrix experiment tests ────────────────────────────────────────────


class TestMatrixExperiment:
    """Tests for v2 matrix experiment mode."""

    @pytest.fixture
    def matrix_config(self) -> RollingResearchConfig:
        return RollingResearchConfig(
            experiment_id="matrix_test",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "base_sig", "score_column": "score"},
            labels=[{"label_id": "l1"}],
            generators=[
                {"generator_id": "gen_a", "type": "fixture", "params": {"n_instruments": 5, "seed": 1}},
                {"generator_id": "gen_b", "type": "fixture", "params": {"n_instruments": 5, "seed": 2}},
            ],
            transforms=[
                {"transform_id": "raw", "type": "identity"},
                {"transform_id": "z", "type": "daily_zscore"},
            ],
            strategies=[
                {"strategy_id": "s20", "strategy_template_id": "t20", "top_n": 20},
                {"strategy_id": "s50", "strategy_template_id": "t50", "top_n": 50},
            ],
        )

    def test_matrix_expands_to_2x2x2_jobs(self, matrix_config) -> None:
        """2 generators × 2 transforms × 2 strategies = 4 jobs × 2 bt each."""
        jobs = build_matrix_jobs(matrix_config)
        assert len(jobs) == 4  # 2 gen × 2 transform
        for j in jobs:
            assert len(j.strategy_configs) == 2  # each job has 2 strategies

    def test_generator_called_per_window_not_per_strategy(self, matrix_config, tmp_path) -> None:
        """Generator is called once per window per generator (not per strategy).

        With 2 strategies, the generator should NOT be called 2× per window.
        """
        call_count: dict[str, int] = {"gen_a": 0, "gen_b": 0}

        class CountingGenerator:
            def __init__(self, gen_id: str):
                self._gen_id = gen_id
            def generate(self, **kwargs):
                call_count[self._gen_id] += 1
                return pd.DataFrame({
                    "trade_date": ["2026-05-12", "2026-05-12"],
                    "data_date": ["2026-05-08", "2026-05-08"],
                    "instrument": ["0000001.SZ", "0000002.SZ"],
                    "signal_id": [""] * 2,
                    "signal_run_id": [""] * 2,
                    "score": [0.5, -0.3],
                })

        bt_manifest = tmp_path / "bt_mtx" / "manifest.json"
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
            from qsys.research import rolling_runner as rr_mod
            orig_factory = rr_mod._create_generator_from_config

            def counting_factory(cfg):
                return CountingGenerator(cfg["generator_id"])
            rr_mod._create_generator_from_config = counting_factory

            try:
                result = runner.run(
                    config=matrix_config,
                    overwrite_signal=True, overwrite_eval=True,
                    overwrite_backtest=True, overwrite_experiment=True,
                )
            finally:
                rr_mod._create_generator_from_config = orig_factory

        # gen_a and gen_b called same number of times (per window)
        assert call_count["gen_a"] == call_count["gen_b"], "generators called different times"
        wc = result["window_count"]
        assert call_count["gen_a"] == wc, f"gen_a called {call_count['gen_a']} vs windows {wc}"
        # Not called per strategy: 2 strategies should NOT double the calls
        assert call_count["gen_a"] < wc * 2, "generator called per strategy (should not)"

    def test_each_generator_x_transform_saves_one_signal_run(self, matrix_config, tmp_path) -> None:
        """Each (generator, transform) pair produces one SignalRun."""
        bt_manifest = tmp_path / "bt_mtx2" / "manifest.json"
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
                config=matrix_config,
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        # 4 signal runs saved
        assert result["signal_run_count"] == 4

    def test_each_signal_run_is_evaluated(self, matrix_config, tmp_path) -> None:
        """Every saved SignalRun is evaluated against each label."""
        bt_manifest = tmp_path / "bt_mtx3" / "manifest.json"
        bt_manifest.parent.mkdir(parents=True, exist_ok=True)
        bt_manifest.write_text(json.dumps({"strategy_run_id": "sr1", "backtest_id": "bt1"}))

        eval_calls: list[tuple[str, str]] = []

        # Use a dict-level mock so we can track calls without self-binding issues
        from unittest.mock import MagicMock
        mock_evaluator = MagicMock()
        mock_evaluator.evaluate.return_value = None

        original_evaluate = None

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb, \
             patch("qsys.research.evaluation.SignalEvaluator") as me:
            mb.return_value.run_from_signal_cache.return_value = type("R", (), {
                "backtest_id": "bt1",
                "artifacts": {"manifest": str(bt_manifest)},
            })()
            me.return_value = mock_evaluator

            runner = RollingResearchRunner(str(tmp_path))
            runner.run(
                config=matrix_config,
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        # 4 signal runs × 1 label = 4 eval calls
        assert mock_evaluator.evaluate.call_count == 4
        signal_ids = {call[1]["signal_id"] for call in mock_evaluator.evaluate.call_args_list}
        assert len(signal_ids) == 4  # 4 distinct signal_ids

    def test_each_signal_x_strategy_backtest_is_run(self, matrix_config, tmp_path) -> None:
        """Each (signal, strategy) triggers one backtest."""
        bt_manifest = tmp_path / "bt_mtx4" / "manifest.json"
        bt_manifest.parent.mkdir(parents=True, exist_ok=True)
        bt_manifest.write_text(json.dumps({"strategy_run_id": "sr1", "backtest_id": "bt1"}))

        bt_call_count: list[dict] = []

        class TrackingBacktest:
            def run_from_signal_cache(self, **kw):
                bt_call_count.append({
                    "signal_id": kw["signal_id"],
                    "top_n": kw["top_n"],
                    "strategy_template_id": kw["strategy_template_id"],
                })
                return type("R", (), {
                    "backtest_id": "bt1",
                    "artifacts": {"manifest": str(bt_manifest)},
                })()

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me:
            me.return_value.evaluate.return_value = None
            runner = RollingResearchRunner(str(tmp_path))
            from qsys.backtest import strategy_runner as bt_mod
            orig_runner_cls = bt_mod.BacktestRunner
            bt_mod.BacktestRunner = lambda: TrackingBacktest()

            try:
                result = runner.run(
                    config=matrix_config,
                    overwrite_signal=True, overwrite_eval=True,
                    overwrite_backtest=True, overwrite_experiment=True,
                )
            finally:
                bt_mod.BacktestRunner = orig_runner_cls

        # 4 jobs × 2 strategies = 8 backtests
        assert result["backtest_count"] == 8
        assert len(bt_call_count) == 8

    def test_experiment_index_receives_all_refs(self, matrix_config, tmp_path) -> None:
        """ExperimentIndex receives signal/eval/backtest refs for every path."""
        bt_call_idx: list[int] = [0]

        def make_bt_manifest():
            idx = bt_call_idx[0]
            bt_call_idx[0] += 1
            bm = tmp_path / f"bt_mtx5_{idx}" / "manifest.json"
            bm.parent.mkdir(parents=True, exist_ok=True)
            bm.write_text(json.dumps({
                "strategy_run_id": f"sr_{idx}",
                "backtest_id": f"bt_{idx}",
            }))
            return type("R", (), {
                "backtest_id": f"bt_{idx}",
                "artifacts": {"manifest": str(bm)},
            })()

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me, \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb:
            me.return_value.evaluate.return_value = None
            mb.return_value.run_from_signal_cache.side_effect = lambda **kw: make_bt_manifest()

            runner = RollingResearchRunner(str(tmp_path))
            runner.run(
                config=matrix_config,
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        exp_dir = tmp_path / "experiments" / "matrix_test"

        # signal_run_refs.csv: 4 signal runs
        signal_refs = pd.read_csv(exp_dir / "signal_run_refs.csv")
        assert len(signal_refs) == 4

        # signal_eval_refs.csv: 4 signal runs × 1 label
        eval_refs = pd.read_csv(exp_dir / "signal_eval_refs.csv")
        assert len(eval_refs) == 4

        # backtest_refs.csv: 4 × 2 = 8 backtests
        bt_refs = pd.read_csv(exp_dir / "backtest_refs.csv")
        assert len(bt_refs) == 8

    def test_matrix_jobs_csv_written(self, matrix_config, tmp_path) -> None:
        """matrix_jobs.csv is created with correct columns."""
        bt_manifest = tmp_path / "bt_mtx6" / "manifest.json"
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
            runner.run(
                config=matrix_config,
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        exp_dir = tmp_path / "experiments" / "matrix_test"
        csv_path = exp_dir / "matrix_jobs.csv"
        assert csv_path.exists()

        df = pd.read_csv(csv_path)
        expected_cols = [
            "generator_id", "transform_id", "strategy_id",
            "signal_id", "signal_run_id",
            "strategy_template_id", "top_n",
            "backtest_id", "strategy_run_id", "status",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Missing column: {col}"

        assert len(df) == 8  # 4 jobs × 2 strategies

    def test_rolling_research_manifest_contains_matrix_metadata(self, matrix_config, tmp_path) -> None:
        """rolling_research_manifest.json contains mode='matrix'."""
        bt_manifest = tmp_path / "bt_mtx7" / "manifest.json"
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
            runner.run(
                config=matrix_config,
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        exp_dir = tmp_path / "experiments" / "matrix_test"
        mf_path = exp_dir / "rolling_research_manifest.json"
        assert mf_path.exists()

        manifest = json.loads(mf_path.read_text())
        assert manifest["mode"] == "matrix"
        assert manifest.get("matrix_purpose") == "framework_boundary_smoke"
        assert manifest["generator_count"] == 2
        assert manifest["transform_count"] == 2
        assert manifest["strategy_count"] == 2
        assert manifest["job_count"] == 4
        assert len(manifest["signal_runs"]) == 4
        assert len(manifest["backtest_refs"]) == 8

    def test_v1_config_still_works(self, tmp_path: Path) -> None:
        """Old v1 single-signal config still works unchanged."""
        config = RollingResearchConfig(
            experiment_id="e_v1_still",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "sig1", "signal_run_id": "run1"},
            labels=[{"label_id": "l1"}],
            backtests=[{"strategy_template_id": "bt1", "top_n": 5}],
        )
        bt_manifest = tmp_path / "bt_v1" / "manifest.json"
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
        assert "mode" not in result  # v1 has no mode

    def test_fail_fast_remains(self, matrix_config, tmp_path) -> None:
        """Matrix mode still fails fast on backtest errors."""
        bt_manifest = tmp_path / "bt_fail" / "manifest.json"
        bt_manifest.parent.mkdir(parents=True, exist_ok=True)
        bt_manifest.write_text(json.dumps({"strategy_run_id": "sr1", "backtest_id": "bt1"}))

        call_count = [0]

        class FailingBacktest:
            def run_from_signal_cache(self, **kw):
                call_count[0] += 1
                if call_count[0] >= 2:
                    raise RuntimeError("backtest failed on 2nd call")
                return type("R", (), {
                    "backtest_id": "bt1",
                    "artifacts": {"manifest": str(bt_manifest)},
                })()

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me:
            me.return_value.evaluate.return_value = None
            runner = RollingResearchRunner(str(tmp_path))
            from qsys.backtest import strategy_runner as bt_mod
            orig_cls = bt_mod.BacktestRunner
            bt_mod.BacktestRunner = lambda: FailingBacktest()

            try:
                with pytest.raises(RuntimeError, match="backtest failed"):
                    runner.run(
                        config=matrix_config,
                        overwrite_signal=True, overwrite_eval=True,
                        overwrite_backtest=True, overwrite_experiment=True,
                    )
            finally:
                bt_mod.BacktestRunner = orig_cls


# ── Generator tests ────────────────────────────────────────────────────


class TestGeneratorFactory:
    def test_fixture_generator(self) -> None:
        from qsys.research.rolling_runner import _create_generator_from_config, FixtureSignalGenerator
        gen = _create_generator_from_config({"generator_id": "g", "type": "fixture"})
        assert isinstance(gen, FixtureSignalGenerator)

    def test_alpha_v1_existing_generator(self) -> None:
        from qsys.research.rolling_runner import _create_generator_from_config
        from qsys.research.generators.alpha_v1_existing import AlphaV1ExistingGenerator
        gen = _create_generator_from_config({"generator_id": "g", "type": "alpha_v1_existing"})
        assert isinstance(gen, AlphaV1ExistingGenerator)

    def test_technical_composite_generator(self) -> None:
        from qsys.research.rolling_runner import _create_generator_from_config
        from qsys.research.generators.technical_composite import TechnicalCompositeV1Generator
        gen = _create_generator_from_config({
            "generator_id": "g", "type": "technical_composite",
            "params": {"momentum_short": 10, "momentum_long": 30},
        })
        assert isinstance(gen, TechnicalCompositeV1Generator)
        assert gen.momentum_short == 10
        assert gen.momentum_long == 30

    def test_unknown_raises(self) -> None:
        from qsys.research.rolling_runner import _create_generator_from_config
        with pytest.raises(ValueError, match="Unknown generator type"):
            _create_generator_from_config({"generator_id": "g", "type": "unknown"})


class TestTechnicalCompositeV1:
    def test_returns_valid_schema(self) -> None:
        from qsys.research.generators.technical_composite import TechnicalCompositeV1Generator

        # Fake OHLCV data
        dates = pd.bdate_range(start="2026-05-01", end="2026-05-22")
        rows = []
        for d in dates:
            for inst in ["000001.SZ", "000002.SZ", "000003.SZ"]:
                rows.append({
                    "trade_date": d.strftime("%Y-%m-%d"),
                    "instrument": inst,
                    "close": float(100 + np.random.randn() * 5),
                    "open": float(99 + np.random.randn() * 5),
                    "high": float(101 + np.random.randn() * 5),
                    "low": float(98 + np.random.randn() * 5),
                    "volume": float(1e6 + np.random.randn() * 1e5),
                })
        ohlcv = pd.DataFrame(rows)

        gen = TechnicalCompositeV1Generator(
            data_loader=lambda **kw: ohlcv,
            momentum_short=5, momentum_long=10,
            reversal_days=3, volatility_days=5,
            volume_short=3, volume_long=10,
        )
        result = gen.generate(
            train_start="2026-01-01", train_end="2026-04-30",
            predict_start="2026-05-15", predict_end="2026-05-22",
            signal_id="tech_comp", signal_run_id="run1",
        )
        assert len(result) > 0
        expected_cols = ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"]
        for col in expected_cols:
            assert col in result.columns
        assert result["signal_id"].iloc[0] == "tech_comp"

    def test_non_constant_score(self) -> None:
        from qsys.research.generators.technical_composite import TechnicalCompositeV1Generator

        rng = np.random.default_rng(42)
        dates = pd.bdate_range(start="2026-05-01", end="2026-05-22")
        rows = []
        for d in dates:
            for inst in ["A.SZ", "B.SZ", "C.SZ", "D.SZ", "E.SZ"]:
                trend = 100 + (d.day % 10) * 2
                rows.append({
                    "trade_date": d.strftime("%Y-%m-%d"),
                    "instrument": inst,
                    "close": trend + float(rng.normal(0, 3)),
                    "open": trend + float(rng.normal(0, 3)),
                    "high": trend + float(rng.normal(0, 3)) + 1,
                    "low": trend + float(rng.normal(0, 3)) - 1,
                    "volume": float(1e6 + rng.normal(0, 1e5)),
                })
        ohlcv = pd.DataFrame(rows)

        gen = TechnicalCompositeV1Generator(
            data_loader=lambda **kw: ohlcv,
            momentum_short=5, momentum_long=10,
            reversal_days=3, volatility_days=5,
            volume_short=3, volume_long=10,
        )
        result = gen.generate(
            train_start="2026-01-01", train_end="2026-04-30",
            predict_start="2026-05-18", predict_end="2026-05-22",
            signal_id="t", signal_run_id="r",
        )
        # Score should have variation (not all identical)
        assert result["score"].nunique() > 1, f"score is constant: {result['score'].unique()}"

    def test_data_date_before_trade_date(self) -> None:
        from qsys.research.generators.technical_composite import TechnicalCompositeV1Generator

        rng = np.random.default_rng(7)
        dates = pd.bdate_range(start="2026-05-01", end="2026-05-22")
        rows = []
        for d in dates:
            for inst in ["A.SZ", "B.SZ"]:
                rows.append({
                    "trade_date": d.strftime("%Y-%m-%d"),
                    "instrument": inst,
                    "close": float(100 + rng.normal(0, 5)),
                    "open": float(99 + rng.normal(0, 5)),
                    "high": float(101 + rng.normal(0, 5)),
                    "low": float(98 + rng.normal(0, 5)),
                    "volume": float(1e6 + rng.normal(0, 1e5)),
                })
        ohlcv = pd.DataFrame(rows)

        gen = TechnicalCompositeV1Generator(
            data_loader=lambda **kw: ohlcv,
            momentum_short=3, momentum_long=5,
            reversal_days=2, volatility_days=3,
            volume_short=2, volume_long=5,
        )
        result = gen.generate(
            train_start="2026-01-01", train_end="2026-04-30",
            predict_start="2026-05-18", predict_end="2026-05-22",
            signal_id="t", signal_run_id="r",
        )
        # Every data_date must be < trade_date
        for _, row in result.iterrows():
            assert row["data_date"] < row["trade_date"], \
                f"data_date {row['data_date']} >= trade_date {row['trade_date']}"

    def test_uses_only_data_up_to_data_date(self) -> None:
        """Generator should use data observable at each window's data_date."""
        from qsys.research.generators.technical_composite import TechnicalCompositeV1Generator

        # Provide limited data: only up to predict_start - 1
        dates = pd.bdate_range(start="2026-04-01", end="2026-04-15")
        rows = []
        for d in dates:
            for inst in ["A.SZ", "B.SZ"]:
                rows.append({
                    "trade_date": d.strftime("%Y-%m-%d"),
                    "instrument": inst,
                    "close": float(100.0),
                    "open": float(99.0),
                    "high": float(101.0),
                    "low": float(98.0),
                    "volume": float(1e6),
                })
        ohlcv = pd.DataFrame(rows)

        gen = TechnicalCompositeV1Generator(
            data_loader=lambda **kw: ohlcv,
            momentum_short=3, momentum_long=5,
            reversal_days=2, volatility_days=3,
            volume_short=2, volume_long=5,
        )

        # The predict window is in May, but data only goes to April 15
        # If the data_loader parameter is correct, this will complain "no data"
        # If it tries to fetch data after April, it will get empty rows
        with pytest.raises(RuntimeError, match=".*no data.*predict range.*"):
            gen.generate(
                train_start="2026-01-01", train_end="2026-04-14",
                predict_start="2026-05-18", predict_end="2026-05-22",
                signal_id="t", signal_run_id="r",
            )


class TestAlphaV1ExistingGenerator:
    def test_mocked_returns_valid_schema(self) -> None:
        from qsys.research.generators.alpha_v1_existing import AlphaV1ExistingGenerator

        # Mock adapter factory
        class MockAdapter:
            def generate_predictions_for_date(self, trade_date, data_date=None):
                import numpy as np
                rng = np.random.default_rng(42)
                insts = [f"000{ii:04d}.SZ" for ii in range(5)]
                rows = []
                for i, inst in enumerate(insts):
                    rows.append({
                        "instrument": inst,
                        "score": float(rng.normal(0, 1)),
                    })
                return pd.DataFrame(rows)

        gen = AlphaV1ExistingGenerator(adapter_factory=lambda project_root=None: MockAdapter())
        with patch("qsys.data.calendar.get_trading_calendar",
                   return_value=["2026-05-18", "2026-05-19", "2026-05-20"]):
            result = gen.generate(
                train_start="2026-01-01", train_end="2026-05-15",
                predict_start="2026-05-18", predict_end="2026-05-20",
                signal_id="alpha_v1", signal_run_id="run1",
            )
        assert len(result) == 15  # 3 dates × 5 instruments
        expected_cols = ["trade_date", "data_date", "instrument", "signal_id", "signal_run_id", "score"]
        for col in expected_cols:
            assert col in result.columns

    def test_data_date_is_prev_trading_day(self) -> None:
        from qsys.research.generators.alpha_v1_existing import AlphaV1ExistingGenerator

        class MockAdapter:
            def generate_predictions_for_date(self, trade_date, data_date=None):
                return pd.DataFrame({
                    "instrument": ["000001.SZ"],
                    "score": [0.5],
                })

        gen = AlphaV1ExistingGenerator(adapter_factory=lambda project_root=None: MockAdapter())
        with patch("qsys.data.calendar.get_trading_calendar",
                   return_value=["2026-05-18", "2026-05-19"]):  # Tues, Wed
            result = gen.generate(
                train_start="", train_end="",
                predict_start="2026-05-18", predict_end="2026-05-19",
                signal_id="a", signal_run_id="r",
            )
        # data_date should be < trade_date
        for _, row in result.iterrows():
            assert row["data_date"] < row["trade_date"]


# ── Signal Combination tests ───────────────────────────────────────────


def _make_signal_store_frame(
    trade_dates: list[str],
    instruments: list[str],
    signal_id: str,
    signal_run_id: str,
    score_mean: float = 0.0,
) -> pd.DataFrame:
    from datetime import datetime, timedelta

    def _prev_bday(td: str) -> str:
        dt = datetime.strptime(td, "%Y-%m-%d")
        prev = dt - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        return prev.strftime("%Y-%m-%d")

    rows = []
    for td in trade_dates:
        dd = _prev_bday(td)
        for inst in instruments:
            rows.append({
                "trade_date": td,
                "data_date": dd,
                "instrument": inst,
                "signal_id": signal_id,
                "signal_run_id": signal_run_id,
                "score": float(np.random.default_rng().normal(score_mean, 1)),
            })
    return pd.DataFrame(rows)


class TestSignalCombine:
    @pytest.fixture
    def signal_store_and_paths(self, tmp_path):
        from qsys.signal.store import SignalStore
        from qsys.research.paths import ResearchPaths
        store = SignalStore(str(tmp_path))
        paths = ResearchPaths(str(tmp_path))
        return store, paths

    def _save_dummy(self, store: SignalStore, sig_id: str, run_id: str,
                    trade_dates, instruments, score_mean=0.0):
        df = _make_signal_store_frame(trade_dates, instruments, sig_id, run_id, score_mean)
        store.save_signal_run(sig_id, run_id, df, overwrite=True)
        return df

    def test_linear_blend(self, tmp_path):
        from qsys.research.signal_combine import CombineSpec, CombineInput, combine_signals
        from qsys.signal.store import SignalStore
        from qsys.research.paths import ResearchPaths

        store = SignalStore(str(tmp_path))
        paths = ResearchPaths(str(tmp_path))
        dates = ["2026-05-18", "2026-05-19"]
        insts = ["A.SZ", "B.SZ"]

        df_a = self._save_dummy(store, "sig_a", "run_a", dates, insts, 0.1)
        df_b = self._save_dummy(store, "sig_b", "run_b", dates, insts, 0.2)

        spec = CombineSpec(
            combine_id="blend_test",
            combine_type="linear_blend",
            inputs=[
                CombineInput(source_signal_id="sig_a", source_signal_run_id="run_a", weight=0.7),
                CombineInput(source_signal_id="sig_b", source_signal_run_id="run_b", weight=0.3),
            ],
        )
        result = combine_signals(
            spec,
            output_signal_id="combined_sig",
            output_signal_run_id="combined_run",
            signal_store=store,
            research_paths=paths,
            overwrite=True,
        )
        assert len(result) > 0
        assert result["signal_id"].iloc[0] == "combined_sig"
        assert result["signal_run_id"].iloc[0] == "combined_run"
        # Verify SignalRun was saved
        loaded = store.load_signal_run("combined_sig", "combined_run")
        assert len(loaded) == len(result)

    def test_equal_weight(self, tmp_path):
        from qsys.research.signal_combine import CombineSpec, CombineInput, combine_signals
        from qsys.signal.store import SignalStore
        from qsys.research.paths import ResearchPaths
        store = SignalStore(str(tmp_path))
        paths = ResearchPaths(str(tmp_path))
        dates = ["2026-05-18"]
        insts = ["A.SZ"]

        df_a = self._save_dummy(store, "sig_a", "run_a", dates, insts, 0.5)
        df_b = self._save_dummy(store, "sig_b", "run_b", dates, insts, -0.3)

        spec = CombineSpec(
            combine_id="eq_test",
            combine_type="equal_weight",
            inputs=[
                CombineInput(source_signal_id="sig_a", source_signal_run_id="run_a", weight=1.0),
                CombineInput(source_signal_id="sig_b", source_signal_run_id="run_b", weight=1.0),
            ],
        )
        result = combine_signals(
            spec,
            output_signal_id="eq_sig",
            output_signal_run_id="eq_run",
            signal_store=store,
            research_paths=paths,
            overwrite=True,
        )
        assert len(result) > 0

    def test_combination_manifest_written(self, tmp_path):
        from qsys.research.signal_combine import CombineSpec, CombineInput, combine_signals
        from qsys.signal.store import SignalStore
        from qsys.research.paths import ResearchPaths

        store = SignalStore(str(tmp_path))
        paths = ResearchPaths(str(tmp_path))
        dates = ["2026-05-18"]
        insts = ["A.SZ", "B.SZ"]

        self._save_dummy(store, "sig_a", "run_a", dates, insts)
        self._save_dummy(store, "sig_b", "run_b", dates, insts)

        spec = CombineSpec(
            combine_id="manifest_test",
            combine_type="linear_blend",
            inputs=[
                CombineInput(source_signal_id="sig_a", source_signal_run_id="run_a", weight=0.6),
                CombineInput(source_signal_id="sig_b", source_signal_run_id="run_b", weight=0.4),
            ],
        )
        combine_signals(
            spec,
            output_signal_id="m_sig",
            output_signal_run_id="m_run",
            signal_store=store,
            research_paths=paths,
            overwrite=True,
        )

        # Check combination_manifest.json
        sig_dir = paths.signal_dir("m_sig", "m_run")
        mf_path = sig_dir / "combination_manifest.json"
        assert mf_path.exists()
        import json
        mf = json.loads(mf_path.read_text())
        assert mf["combine_id"] == "manifest_test"
        assert mf["combine_type"] == "linear_blend"
        assert len(mf["inputs"]) == 2
        assert mf["inputs"][0]["weight"] == 0.6
        assert mf["inputs"][1]["weight"] == 0.4
        assert mf["output_signal_id"] == "m_sig"

    def test_combined_passes_no_lookahead(self, tmp_path):
        from qsys.research.signal_combine import CombineSpec, CombineInput, combine_signals
        from qsys.signal.store import SignalStore, _check_no_lookahead_on_frame
        from qsys.research.paths import ResearchPaths

        store = SignalStore(str(tmp_path))
        paths = ResearchPaths(str(tmp_path))

        # Save frames with proper data_date for no-lookahead
        # 2026-05-18 is Monday -> data_date should be 2026-05-15 (Friday)
        dates = ["2026-05-18", "2026-05-19"]
        insts = ["A.SZ"]
        rows_a = []
        rows_b = []
        for i, td in enumerate(dates):
            dd = "2026-05-15" if td == "2026-05-18" else "2026-05-18"
            for inst in insts:
                rows_a.append({"trade_date": td, "data_date": dd, "instrument": inst,
                               "signal_id": "sig_a", "signal_run_id": "run_a", "score": 0.5})
                rows_b.append({"trade_date": td, "data_date": dd, "instrument": inst,
                               "signal_id": "sig_b", "signal_run_id": "run_b", "score": -0.3})
        store.save_signal_run("sig_a", "run_a", pd.DataFrame(rows_a), overwrite=True)
        store.save_signal_run("sig_b", "run_b", pd.DataFrame(rows_b), overwrite=True)

        spec = CombineSpec(
            combine_id="nl_test",
            combine_type="equal_weight",
            inputs=[
                CombineInput(source_signal_id="sig_a", source_signal_run_id="run_a", weight=1.0),
                CombineInput(source_signal_id="sig_b", source_signal_run_id="run_b", weight=1.0),
            ],
        )
        result = combine_signals(
            spec,
            output_signal_id="nl_sig",
            output_signal_run_id="nl_run",
            signal_store=store,
            research_paths=paths,
            overwrite=True,
        )
        # Should not raise
        _check_no_lookahead_on_frame(result)

    def test_build_combine_spec_from_config(self):
        from qsys.research.signal_combine import build_combine_spec_from_config

        config = {
            "combine_id": "blend_test",
            "type": "linear_blend",
            "inputs": [
                {"source_generator_id": "gen_a", "source_transform_id": "raw", "weight": 0.7},
                {"source_generator_id": "gen_b", "source_transform_id": "z", "weight": 0.3},
            ],
        }
        signal_id_map = {"gen_a__raw": "sig_a__raw", "gen_b__z": "sig_b__z"}
        signal_run_id_map = {"gen_a__raw": "run_a__raw", "gen_b__z": "run_b__z"}

        spec = build_combine_spec_from_config(config, signal_id_map, signal_run_id_map)
        assert spec.combine_id == "blend_test"
        assert spec.combine_type == "linear_blend"
        assert len(spec.inputs) == 2
        assert spec.inputs[0].source_signal_id == "sig_a__raw"
        assert spec.inputs[0].source_signal_run_id == "run_a__raw"
        assert spec.inputs[0].weight == 0.7

    def test_build_cross_signal_index(self, tmp_path):
        from qsys.research.signal_combine import (
            CombineSpec, CombineInput, build_cross_signal_index,
        )
        from qsys.research.paths import ResearchPaths

        paths = ResearchPaths(str(tmp_path))
        spec = CombineSpec(
            combine_id="test",
            combine_type="linear_blend",
            inputs=[CombineInput(source_signal_id="a", source_signal_run_id="r1", weight=0.7)],
        )
        df = build_cross_signal_index(
            [spec], ["out_sig"], ["out_run"],
            paths, "exp_test",
        )
        assert len(df) == 1
        assert df.iloc[0]["combine_id"] == "test"

        # Check file written
        exp_dir = paths.experiment_dir("exp_test")
        csv_path = exp_dir / "cross_signal_index.csv"
        assert csv_path.exists()


class TestMatrixWithCombinations:
    def test_matrix_with_combination(self, tmp_path) -> None:
        """Matrix runner includes combined signals in eval/backtest/index."""
        from qsys.research.signal_combine import build_cross_signal_index
        matrix_config = RollingResearchConfig(
            experiment_id="matrix_comb",
            calendar={"start_date": "2026-05-08", "end_date": "2026-05-25", "train_window_days": 5},
            signal={"signal_id": "base", "score_column": "score"},
            labels=[{"label_id": "l1"}],
            generators=[
                {"generator_id": "gen_a", "type": "fixture", "params": {"n_instruments": 3, "seed": 1}},
                {"generator_id": "gen_b", "type": "fixture", "params": {"n_instruments": 3, "seed": 2}},
            ],
            transforms=[
                {"transform_id": "raw", "type": "identity"},
            ],
            strategies=[
                {"strategy_id": "s20", "strategy_template_id": "t20", "top_n": 20},
            ],
            signal_combinations=[
                {
                    "combine_id": "blend_test",
                    "type": "linear_blend",
                    "inputs": [
                        {"source_generator_id": "gen_a", "source_transform_id": "raw", "weight": 0.7},
                        {"source_generator_id": "gen_b", "source_transform_id": "raw", "weight": 0.3},
                    ],
                },
            ],
        )
        bt_call_idx: list[int] = [0]

        def make_bt():
            idx = bt_call_idx[0]
            bt_call_idx[0] += 1
            bm = tmp_path / f"bt_comb_{idx}" / "manifest.json"
            bm.parent.mkdir(parents=True, exist_ok=True)
            bm.write_text(json.dumps({"strategy_run_id": f"sr_{idx}", "backtest_id": f"bt_{idx}"}))
            return type("R", (), {"backtest_id": f"bt_{idx}", "artifacts": {"manifest": str(bm)}})()

        with patch("qsys.data.calendar.get_trading_calendar", return_value=_MOCK_CAL), \
             patch("qsys.research.evaluation.SignalEvaluator") as me, \
             patch("qsys.backtest.strategy_runner.BacktestRunner") as mb:
            me.return_value.evaluate.return_value = None
            mb.return_value.run_from_signal_cache.side_effect = lambda **kw: make_bt()

            runner = RollingResearchRunner(str(tmp_path))
            result = runner.run(
                config=matrix_config,
                overwrite_signal=True, overwrite_eval=True,
                overwrite_backtest=True, overwrite_experiment=True,
            )

        assert result["status"] == "passed"
        assert result["combination_count"] == 1
        assert result["combined_signal_run_count"] == 1

        exp_dir = tmp_path / "experiments" / "matrix_comb"

        # cross_signal_index.csv should exist
        assert (exp_dir / "cross_signal_index.csv").exists()

        # Combined signal appears in backtest_refs
        bt_refs = pd.read_csv(exp_dir / "backtest_refs.csv")
        # 2 base signal jobs * 1 strategy + 1 combined * 1 strategy = 3 backtests
        assert len(bt_refs) == 3

        # Combined signal appears in signal_run_refs
        signal_refs = pd.read_csv(exp_dir / "signal_run_refs.csv")
        # 2 base jobs + 1 combined = 3 signal runs
        assert len(signal_refs) == 3


# ── DuckDB query helper test ───────────────────────────────────────────


class TestQueryExperimentDuckDB:
    def test_duckdb_works_on_fixture_experiment(self, tmp_path) -> None:
        """DuckDB query helper runs on a fixture experiment directory."""
        import subprocess, sys

        # Create a minimal experiment with CSV files
        exp_dir = tmp_path / "experiments" / "duckdb_test"
        exp_dir.mkdir(parents=True, exist_ok=True)

        # signal_eval_index.csv
        pd.DataFrame({
            "signal_id": ["sig1", "sig2"],
            "signal_run_id": ["run1", "run2"],
            "label_id": ["l1", "l1"],
            "rank_icir": [0.5, -0.2],
        }).to_csv(exp_dir / "signal_eval_index.csv", index=False)

        # backtest_index.csv
        pd.DataFrame({
            "signal_id": ["sig1", "sig2"],
            "signal_run_id": ["run1", "run2"],
            "strategy_template_id": ["t20", "t20"],
            "total_return": [0.15, -0.05],
            "final_value": [1150000, 950000],
            "trading_day_count": [20, 20],
        }).to_csv(exp_dir / "backtest_index.csv", index=False)

        # Run the query helper
        script = str(Path(__file__).resolve().parents[2] / "scripts" / "research" / "query_experiment_duckdb.py")
        result = subprocess.run(
            [sys.executable, script, "--experiment-dir", str(exp_dir)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
        assert "sig1" in result.stdout
        assert "total_return" in result.stdout

    def test_duckdb_custom_sql(self, tmp_path) -> None:
        """Custom SQL query works."""
        import subprocess, sys

        exp_dir = tmp_path / "experiments" / "duckdb_custom"
        exp_dir.mkdir(parents=True, exist_ok=True)

        pd.DataFrame({
            "signal_id": ["sig1", "sig2"],
            "rank_icir": [0.5, -0.2],
        }).to_csv(exp_dir / "signal_eval_index.csv", index=False)

        script = str(Path(__file__).resolve().parents[2] / "scripts" / "research" / "query_experiment_duckdb.py")
        result = subprocess.run(
            [sys.executable, script, "--experiment-dir", str(exp_dir), "--sql", "SELECT * FROM signal_eval_index"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "sig1" in result.stdout
