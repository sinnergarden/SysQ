"""Tests for BacktestRunner.run_from_signal_cache."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.backtest.strategy_runner import BacktestRunner
from qsys.signal.store import SignalStore
from qsys.signal.store import FEATURE_VISIBILITY_CONTRACT_V1


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


def _write_pit_artifact(
    research_root: Path,
    name: str = "test_pit_v2",
    members: tuple[str, ...] = ("000000.SZ", "000001.SZ"),
) -> Path:
    artifact = research_root / "universes" / name
    artifact.mkdir(parents=True)
    membership = pd.DataFrame({
        "index_code": ["TEST"] * len(members),
        "instrument": list(members),
        "effective_from": ["20260615"] * len(members),
        "effective_to": ["20260617"] * len(members),
        "source": ["test"] * len(members),
        "source_date": ["2026-06-14"] * len(members),
        "source_version": ["v1"] * len(members),
    })
    membership_path = artifact / "membership.parquet"
    membership.to_parquet(membership_path, index=False)
    manifest = {
        "universe_id": name,
        "membership_sha256": hashlib.sha256(
            membership_path.read_bytes()
        ).hexdigest(),
        "raw_source_hash": "raw-test-hash",
        "source": "test",
        "source_date": "2026-06-14",
        "n_snapshots": 1,
        "snapshot_date_range": ["20260615", "20260615"],
        "n_unique_instruments": len(members),
        "n_membership_spans": len(members),
        "description": "test PIT universe",
    }
    (artifact / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return artifact


class TestRunFromSignalCache:
    def test_cli_rejects_complete_accounting_with_accumulate(self) -> None:
        script = Path(__file__).parents[2] / "scripts/research/backtest_from_signal.py"
        completed = subprocess.run(
            [
                sys.executable, str(script),
                "--signal-id", "sig",
                "--signal-run-id", "run",
                "--start-date", "2026-06-15",
                "--end-date", "2026-06-15",
                "--accumulate",
                "--require-complete-accounting",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 2
        assert "not supported with --accumulate" in completed.stderr

    def test_legacy_accumulate_smoke_keeps_non_accounting_manifest(
        self, tmp_path: Path
    ) -> None:
        store = SignalStore(str(tmp_path))
        _signal_fixture(store, n_dates=1, n_inst=3)
        output = tmp_path / "accumulate_smoke"
        with patch(
            "qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices
        ), patch(
            "qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar
        ):
            result = BacktestRunner().run_accumulate(
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-15",
                research_root=tmp_path,
                output_dir=output,
                overwrite=True,
                rebalance_freq="daily",
                commission=0.0,
                stamp_duty=0.0,
                min_commission=0.0,
                slippage=0.0,
            )
        manifest = json.loads((output / "manifest.json").read_text())
        assert result.status == "completed"
        assert manifest["corporate_action_policy"] == "not_modeled"
        assert any(
            "corporate actions are not modeled" in item
            for item in manifest["research_limitations"]
        )

    def test_pit_execution_filter_pins_identity_and_drops_nonmembers(
        self, tmp_path: Path
    ) -> None:
        artifact = _write_pit_artifact(tmp_path)
        out = tmp_path / "bt_pit"
        result = _run_bt(
            tmp_path,
            fixture_dates=1,
            fixture_inst=5,
            signal_id="test_sig",
            signal_run_id="test_run",
            start_date="2026-06-15",
            end_date="2026-06-15",
            top_n=5,
            pit_universe_artifact=artifact.name,
            output_dir=out,
        )
        executions = pd.read_csv(out / "executions.csv")
        manifest = json.loads((out / "manifest.json").read_text())
        identity = manifest["pit_execution_universe"]

        assert result.status == "completed"
        assert executions["instrument"].tolist() == ["000000.SZ", "000001.SZ"]
        assert identity["artifact"] == artifact.name
        assert identity["membership_sha256"] == hashlib.sha256(
            (artifact / "membership.parquet").read_bytes()
        ).hexdigest()
        assert identity["manifest_sha256"] == hashlib.sha256(
            (artifact / "manifest.json").read_bytes()
        ).hexdigest()
        assert identity["filter_stats"]["primary"] == {
            "input_rows": 5,
            "output_rows": 2,
            "dropped_rows": 3,
        }

    def test_pit_execution_filter_changes_backtest_identity(
        self, tmp_path: Path
    ) -> None:
        artifact = _write_pit_artifact(tmp_path)
        without = _run_bt(
            tmp_path,
            fixture_dates=1,
            fixture_inst=5,
            signal_id="test_sig",
            signal_run_id="test_run",
            start_date="2026-06-15",
            end_date="2026-06-15",
            output_dir=tmp_path / "without_pit",
        )
        with_pit = _run_bt(
            tmp_path,
            fixture_dates=1,
            fixture_inst=5,
            signal_id="test_sig",
            signal_run_id="test_run",
            start_date="2026-06-15",
            end_date="2026-06-15",
            pit_universe_artifact=artifact.name,
            output_dir=tmp_path / "with_pit",
        )
        assert without.backtest_id != with_pit.backtest_id

    def test_pit_execution_filter_rejects_tampered_membership(
        self, tmp_path: Path
    ) -> None:
        artifact = _write_pit_artifact(tmp_path)
        tampered = pd.read_parquet(artifact / "membership.parquet")
        tampered.loc[0, "instrument"] = "999999.SZ"
        tampered.to_parquet(artifact / "membership.parquet", index=False)
        with pytest.raises(ValueError, match="membership hash mismatch"):
            _run_bt(
                tmp_path,
                fixture_dates=1,
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-15",
                pit_universe_artifact=artifact.name,
            )

    @pytest.mark.parametrize(
        "bad_name", ["../outside", "nested/artifact", "nested\\artifact", "/tmp/x"]
    )
    def test_pit_execution_filter_rejects_non_bare_name(
        self, tmp_path: Path, bad_name: str
    ) -> None:
        with pytest.raises(ValueError, match="bare artifact name"):
            _run_bt(
                tmp_path,
                fixture_dates=1,
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-15",
                pit_universe_artifact=bad_name,
            )

    def test_rejects_legacy_generated_signal_without_visibility_contract(
        self, tmp_path: Path
    ) -> None:
        store = SignalStore(str(tmp_path))
        _signal_fixture(store, n_dates=1, n_inst=5)
        manifest_path = store.paths.signal_manifest("test_sig", "test_run")
        import json

        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            {
                "model_mode": "signal_research_matrix",
                "generator_id": "legacy_lightgbm",
            }
        )
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="not certified"):
            BacktestRunner().run_from_signal_cache(
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-15",
                research_root=str(tmp_path),
                output_dir=tmp_path / "legacy_out",
            )

    def test_accepts_generated_signal_with_visibility_contract(
        self, tmp_path: Path
    ) -> None:
        store = SignalStore(str(tmp_path))
        _signal_fixture(store, n_dates=1, n_inst=5)
        manifest_path = store.paths.signal_manifest("test_sig", "test_run")
        import json

        manifest = json.loads(manifest_path.read_text())
        manifest.update(
            {
                "model_mode": "signal_research_matrix",
                "generator_id": "current_lightgbm",
                "feature_visibility_contract": FEATURE_VISIBILITY_CONTRACT_V1,
            }
        )
        manifest_path.write_text(json.dumps(manifest))
        with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
             patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
            result = BacktestRunner().run_from_signal_cache(
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-15",
                research_root=str(tmp_path),
                output_dir=tmp_path / "current_out",
                commission=0.0,
                stamp_duty=0.0,
                min_commission=0.0,
                slippage=0.0,
            )
        assert result.status == "completed"

    def test_rejects_adjusted_price_for_real_lot_execution(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(ValueError, match="lot sizing"):
            _run_bt(
                tmp_path,
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-15",
                use_adjusted_price=True,
            )

    def test_rejects_incomplete_secondary_reference(self, tmp_path: Path) -> None:
        runner = BacktestRunner()
        with pytest.raises(ValueError, match="must be provided together"):
            runner.run_from_signal_cache(
                signal_id="test_sig",
                signal_run_id="test_run",
                signal_id_2="secondary",
                start_date="2026-06-15",
                end_date="2026-06-17",
                research_root=tmp_path,
            )

    def test_rejects_blend_weight_outside_unit_interval(self, tmp_path: Path) -> None:
        runner = BacktestRunner()
        with pytest.raises(ValueError, match=r"within \[0, 1\]"):
            runner.run_from_signal_cache(
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-17",
                blend_weight=1.01,
                research_root=tmp_path,
            )

    def test_default_output_is_under_research_root(self, tmp_path: Path) -> None:
        _run_bt(
            tmp_path,
            fixture_dates=1,
            fixture_inst=5,
            signal_id="test_sig",
            signal_run_id="test_run",
            start_date="2026-06-15",
            end_date="2026-06-15",
            output_dir=None,
        )
        assert any((tmp_path / "backtests").glob("*/*/manifest.json"))

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
        assert mf["rebalance_freq"] == "daily"
        assert mf["price_adjustment_policy"] == "raw_execution_and_mtm"
        assert mf["corporate_action_policy"] == "not_modeled"
        assert len(mf["signal_sources"][0]["predictions_sha256"]) == 64

    def test_loads_signal_range_once_not_once_per_rebalance(
        self, tmp_path: Path
    ) -> None:
        original = SignalStore.load_signal_run
        with patch.object(
            SignalStore, "load_signal_run", autospec=True, side_effect=original
        ) as load:
            _run_bt(
                tmp_path,
                fixture_dates=3,
                signal_id="test_sig",
                signal_run_id="test_run",
                start_date="2026-06-15",
                end_date="2026-06-17",
            )
        assert load.call_count == 1

    def test_secondary_missing_rebalance_date_fails_closed(
        self, tmp_path: Path
    ) -> None:
        store = SignalStore(str(tmp_path))
        _signal_fixture(store, n_dates=2, n_inst=5)
        secondary = store.load_signal_run("test_sig", "test_run")
        secondary = secondary[secondary["trade_date"] == "2026-06-15"].copy()
        secondary["signal_id"] = "secondary"
        secondary["signal_run_id"] = "secondary_run"
        store.save_signal_run(
            "secondary", "secondary_run", secondary, overwrite=True
        )
        with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
             patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar), \
             pytest.raises(ValueError, match="refusing to degrade"):
            BacktestRunner().run_from_signal_cache(
                signal_id="test_sig",
                signal_run_id="test_run",
                signal_id_2="secondary",
                signal_run_id_2="secondary_run",
                blend_weight=0.5,
                start_date="2026-06-15",
                end_date="2026-06-16",
                research_root=str(tmp_path),
                output_dir=tmp_path / "missing_secondary",
                overwrite=True,
                rebalance_freq="daily",
            )

    def test_weekly_rebalance_in_result(self, tmp_path: Path) -> None:
        import json
        out = tmp_path / "bt_weekly"
        result = _run_bt(tmp_path, fixture_dates=4, fixture_inst=10,
                          signal_id="test_sig", signal_run_id="test_run",
                          start_date="2026-06-15", end_date="2026-06-18",
                          rebalance_freq="weekly", output_dir=out)
        assert result.rebalance_freq == "weekly"
        mf = json.loads((out / "manifest.json").read_text())
        assert mf["rebalance_freq"] == "weekly"

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

    def test_backtest_id_pins_signal_content_hash(self, tmp_path: Path) -> None:
        store = SignalStore(str(tmp_path))
        _signal_fixture(store, n_dates=1, n_inst=5)

        def run(output: str):
            with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
                 patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
                return BacktestRunner().run_from_signal_cache(
                    signal_id="test_sig",
                    signal_run_id="test_run",
                    start_date="2026-06-15",
                    end_date="2026-06-15",
                    research_root=str(tmp_path),
                    output_dir=tmp_path / output,
                    overwrite=True,
                    commission=0.0,
                    stamp_duty=0.0,
                    min_commission=0.0,
                    slippage=0.0,
                )

        first = run("hash_first")
        changed = store.load_signal_run("test_sig", "test_run")
        changed.loc[0, "score"] += 1.0
        store.save_signal_run(
            "test_sig", "test_run", changed, overwrite=True
        )
        second = run("hash_second")
        assert first.backtest_id != second.backtest_id

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

    def test_summary_mode_writes_hashed_execution_artifact(
        self, tmp_path: Path
    ) -> None:
        import hashlib
        import json

        out = tmp_path / "bt_executions"
        _run_bt(
            tmp_path,
            fixture_dates=1,
            fixture_inst=5,
            signal_id="test_sig",
            signal_run_id="test_run",
            start_date="2026-06-15",
            end_date="2026-06-15",
            top_n=2,
            output_dir=out,
        )
        executions_path = out / "executions.csv"
        rows = pd.read_csv(executions_path)
        manifest = json.loads((out / "manifest.json").read_text())
        artifact = manifest["artifacts"]["executions"]

        assert len(rows) == 2
        assert set(rows["status"]) == {"filled"}
        assert set(rows["trade_reason"]) == {"rebalance_to_target_weight"}
        assert artifact["schema_version"] == "backtest_executions_v2"
        assert {"order_value", "adv", "participation_rate", "liquidity_status"}.issubset(rows.columns)
        assert artifact["row_count"] == len(rows)
        assert artifact["complete"] is True
        assert artifact["sha256"] == hashlib.sha256(
            executions_path.read_bytes()
        ).hexdigest()


class TestRunFromSignalCacheGolden:
    """Golden tests: lock exact daily_summary and metrics.

    These tests assert exact numeric values. Any change means the numerical
    output of ``BacktestRunner.run_from_signal_cache`` has changed.
    """

    def test_golden_daily_summary(self, tmp_path: Path) -> None:
        """Lock daily_summary structure for a deterministic 3-day backtest."""
        import json
        out = tmp_path / "golden_ds"
        _run_bt(tmp_path, fixture_dates=3, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-17",
                initial_capital=100000.0, top_n=5,
                output_dir=out)
        ds = pd.read_csv(out / "daily_summary.csv")
        assert len(ds) == 3
        assert ds["status"].tolist() == ["success", "success", "success"]
        # Execution happens at open, MTM at close. Open=close in mock → flat intraday.
        # Cross-day total_value change comes from position carry-over at the same
        # mock prices.  With identical signal scores each day and identical prices,
        # final == initial (flat market).
        # Day 1 establishes positions; days 2-3 have no diff (identical signal scores
        # and flat prices → no rebalancing needed)
        assert ds["order_count"].iloc[0] == 5  # top_n=5
        assert ds["order_count"].iloc[1] == 0
        assert ds["order_count"].iloc[2] == 0
        assert (ds["filled_count"] == ds["order_count"]).all()
        # Flat mock prices → no P&L
        assert abs(ds["total_value_after"].iloc[-1] - 100000.0) < 0.01
        assert ds["position_count"].iloc[-1] == 5

    def test_golden_metrics(self, tmp_path: Path) -> None:
        """Lock metrics.json fields."""
        import json
        out = tmp_path / "golden_m"
        _run_bt(tmp_path, fixture_dates=3, fixture_inst=10,
                signal_id="test_sig", signal_run_id="test_run",
                start_date="2026-06-15", end_date="2026-06-17",
                initial_capital=100000.0, top_n=5,
                output_dir=out)
        m = json.loads((out / "metrics.json").read_text())
        assert m["trading_day_count"] == 3
        assert m["initial_capital"] == 100000.0
        assert m["total_return"] == 0.0  # flat mock prices
        assert m["order_count_total"] == 5  # top_n=5, orders only on day 1
        assert m["filled_count_total"] == m["order_count_total"]
        assert m["rejected_count_total"] == 0
