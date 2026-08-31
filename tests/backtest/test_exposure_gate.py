"""Exposure-gate tests for the posterior_confirmed holding policy.

The gate is a precomputed point-in-time schedule (trade_date -> bool) that, when
active on a day, scales all target weights to ``exposure_gate_scale`` of equity
(kept positions proportionally, new entries at scale/top_n).  Observed through
exposure = market_value / total_value on flat mock prices.

Key properties under test:
  - G0 (no schedule) leaves the baseline unchanged (100% exposure).
  - A gated day targets scale ≈ exposure_gate_scale of equity.
  - Consecutive gated days do NOT compound downward (book stays at scale).
  - Entry sizing on a gated rebalance uses scale/top_n.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.backtest.posterior_policy import PosteriorPolicyConfig
from qsys.backtest.strategy_runner import BacktestRunner
from qsys.signal.store import SignalStore

_TRADING_CAL = [
    "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",
]


def _mock_calendar(start, end):
    return [d for d in _TRADING_CAL if start <= d <= end]


def _mock_prices(trade_date, instruments, price_col="close"):
    prices = {inst: 10.0 + float(i) * 0.5
              for i, inst in enumerate(sorted(instruments))}
    status = pd.DataFrame({
        "is_suspended": 0, "is_limit_up": 0, "is_limit_down": 0,
    }, index=sorted(instruments))
    return prices, status


def _signal(store: SignalStore, *, rotate: bool = False) -> None:
    """10 instruments over 5 dates.  rotate=False keeps a fixed top-5 (so no
    rank_exit ever fires); rotate=True shifts top-5 each day."""
    n_dates, n_inst = 5, 10
    frames = []
    for d in range(n_dates):
        date = f"2026-06-{15 + d:02d}"
        scores = (
            [(n_inst - i + d) % n_inst for i in range(n_inst)]
            if rotate
            else [float(n_inst - i) for i in range(n_inst)]
        )
        frames.append(pd.DataFrame({
            "trade_date": [date] * n_inst,
            "data_date": [f"2026-06-{13 + d:02d}"] * n_inst,
            "instrument": [f"000{i:03d}.SZ" for i in range(n_inst)],
            "signal_id": ["gate_sig"] * n_inst,
            "signal_run_id": ["gate_run"] * n_inst,
            "score": scores,
        }))
    store.save_signal_run(
        "gate_sig", "gate_run", pd.concat(frames, ignore_index=True),
        check_no_lookahead=False, overwrite=True,
    )


def _run(tmp_path, *, schedule: dict[str, bool] | None = None,
         gate_mode: str = "market_risk", gate_scale: float = 0.5,
         rebalance_freq: str = "daily", rotate: bool = False,
         top_n: int = 5, initial_capital: float = 100_000.0,
         holding_policy: str = "posterior_confirmed"):
    store = SignalStore(str(tmp_path))
    _signal(store, rotate=rotate)
    with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
         patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
        return BacktestRunner().run_from_signal_cache(
            signal_id="gate_sig",
            signal_run_id="gate_run",
            start_date="2026-06-15",
            end_date="2026-06-19",
            research_root=str(tmp_path),
            output_dir=tmp_path / "bt_gate",
            overwrite=True,
            commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0,
            top_n=top_n,
            initial_capital=initial_capital,
            holding_policy=holding_policy,
            rebalance_freq=rebalance_freq,
            rank_exit=True,
            exposure_gate_mode=gate_mode,
            exposure_gate_scale=gate_scale,
            exposure_gate_schedule=schedule,
            # Dead rules (E1 skeleton).
            score_delta_min_observations=1_000_000_000,
            posterior_stop_loss=0.999,
            winner_activation_return=0.9999,
            winner_trailing_stop=0.999,
            stale_after_days=10000,
            replacement_rank_gap=1000000,
        )


def _exposure(result) -> list[float]:
    return [
        d["market_value_after"] / d["total_value_after"]
        for d in result.daily_summary
    ]


class TestExposureGate:
    def test_g0_no_schedule_is_full_exposure(self, tmp_path) -> None:
        result = _run(tmp_path, schedule=None)
        assert result.status == "completed"
        exp = _exposure(result)
        # Fixed top-5, daily rebalance: 5 positions at 1/5 each -> ~100%.
        assert exp[0] > 0.95
        assert all(e > 0.95 for e in exp)

    def test_gated_day_targets_half_exposure(self, tmp_path) -> None:
        # Gate active on every day.
        schedule = {d: True for d in _TRADING_CAL}
        result = _run(tmp_path, schedule=schedule)
        exp = _exposure(result)
        # 5 entries at 0.5/5 = 0.10 each -> ~50%.
        assert 0.42 < exp[0] < 0.58
        assert all(0.42 < e < 0.58 for e in exp)

    def test_consecutive_gated_days_do_not_compound(self, tmp_path) -> None:
        # Fixed top-5 (no rank_exit), gate active every day, daily rebalance.
        # After the day-1 cut, later gated rebalances must renormalise to ~50%,
        # not decay to 25% / 12.5% (a naive "multiply targets by 0.5" bug).
        schedule = {d: True for d in _TRADING_CAL}
        result = _run(tmp_path, schedule=schedule)
        exp = _exposure(result)
        assert all(0.42 < e < 0.58 for e in exp)
        # The last day must not be materially below the first.
        assert exp[-1] > exp[0] - 0.03

    def test_gated_entry_sizing_uses_scale_over_top_n(self, tmp_path) -> None:
        # Rotating top-5 so rank_exit + refill happen; gate active on day 1 only.
        # Entries on day 1 must be 0.5/5 each -> ~50%, and the half-size entries
        # persist (hold-drift) on the un-gated days that follow.
        schedule = {d: (d == "2026-06-15") for d in _TRADING_CAL}
        result = _run(tmp_path, schedule=schedule, rotate=True)
        exp = _exposure(result)
        assert 0.40 < exp[0] < 0.60

    def test_scale_parameter_is_used(self, tmp_path) -> None:
        schedule = {d: True for d in _TRADING_CAL}
        result = _run(tmp_path, schedule=schedule, gate_scale=0.7)
        exp = _exposure(result)
        assert 0.62 < exp[0] < 0.78

    def test_schedule_folded_into_backtest_hash(self, tmp_path) -> None:
        a = _run(tmp_path, schedule=None)
        b = _run(tmp_path, schedule={d: True for d in _TRADING_CAL})
        assert a.backtest_id != b.backtest_id

    def test_target_rebalance_gate_scales_absolute_targets(self, tmp_path) -> None:
        schedule = {d: True for d in _TRADING_CAL}
        result = _run(
            tmp_path,
            schedule=schedule,
            holding_policy="target_rebalance",
        )
        exp = _exposure(result)
        assert result.status == "completed"
        assert all(0.42 < value < 0.58 for value in exp)
        manifest = json.loads(
            (tmp_path / "bt_gate" / "manifest.json").read_text()
        )
        assert manifest["holding_policy"] == "target_rebalance"
        assert manifest["exposure_gate"]["mode"] == "market_risk"
        assert manifest["exposure_gate"]["gated_days"] == len(_TRADING_CAL)

    def test_target_rebalance_ungated_dates_stay_fully_invested(self, tmp_path) -> None:
        schedule = {d: False for d in _TRADING_CAL}
        result = _run(
            tmp_path,
            schedule=schedule,
            holding_policy="target_rebalance",
        )
        assert all(value > 0.95 for value in _exposure(result))

    def test_schedule_requires_non_none_mode(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="requires exposure_gate_mode"):
            _run(
                tmp_path,
                schedule={d: True for d in _TRADING_CAL},
                gate_mode="none",
                holding_policy="target_rebalance",
            )

    def test_config_validates_mode_and_scale(self) -> None:
        with pytest.raises(ValueError, match="exposure_gate_mode"):
            PosteriorPolicyConfig(exposure_gate_mode="nope").validate()
        with pytest.raises(ValueError, match="exposure_gate_scale"):
            PosteriorPolicyConfig(exposure_gate_mode="market_risk",
                                  exposure_gate_scale=1.5).validate()
        PosteriorPolicyConfig(exposure_gate_mode="either",
                              exposure_gate_scale=0.5).validate()

    def test_invalid_schedule_shape_rejected(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="exposure_gate_schedule"):
            _run(tmp_path, schedule={"2026-06-15": "yes"})

    def test_gate_defaults_dropped_from_config_manifest(self) -> None:
        m = PosteriorPolicyConfig(
            exposure_gate_mode="none", exposure_gate_scale=0.5
        ).to_manifest()
        assert "exposure_gate_mode" not in m
        assert "exposure_gate_scale" not in m
        gated = PosteriorPolicyConfig(
            exposure_gate_mode="market_risk", exposure_gate_scale=0.5
        ).to_manifest()
        assert gated["exposure_gate_mode"] == "market_risk"
        assert gated["exposure_gate_scale"] == 0.5

    def test_g0_backtest_id_matches_pre_gate_config(self, tmp_path) -> None:
        """Exposure-gate defaults (none/0.5) must not change the E1 baseline
        backtest id (to_manifest drops the defaults, keeping hashes stable)."""
        with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
             patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
            store = SignalStore(str(tmp_path))
            _signal(store)
            result = BacktestRunner().run_from_signal_cache(
                signal_id="gate_sig",
                signal_run_id="gate_run",
                start_date="2026-06-15",
                end_date="2026-06-19",
                research_root=str(tmp_path),
                output_dir=tmp_path / "bt_g0a",
                overwrite=True,
                commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0,
                top_n=5, initial_capital=100_000.0,
                holding_policy="posterior_confirmed",
                rebalance_freq="daily",
                rank_exit=True,
                score_delta_min_observations=1_000_000_000,
                posterior_stop_loss=0.999,
                winner_activation_return=0.9999,
                winner_trailing_stop=0.999,
                stale_after_days=10000,
                replacement_rank_gap=1000000,
                # Explicit gate defaults -> same as baseline.
                exposure_gate_mode="none",
                exposure_gate_scale=0.5,
            )
        assert result.backtest_id == _run(
            tmp_path, schedule=None, gate_mode="none"
        ).backtest_id
