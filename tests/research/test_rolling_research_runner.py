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
