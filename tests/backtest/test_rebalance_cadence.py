"""Unit tests for rebalance-cadence skip logic (weekly / N-trading-day)."""

import pandas as pd

from qsys.backtest.daily_kernel import should_skip_weekly_rebalance

# Trading calendar: 2021-01-04..2021-01-15 (10 trading days, Mon-Fri x2).
CAL = [
    "2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07", "2021-01-08",
    "2021-01-11", "2021-01-12", "2021-01-13", "2021-01-14", "2021-01-15",
]


def test_weekly_skips_same_iso_week():
    # Trades Mon, skips the rest of the week, trades next Mon.
    assert not should_skip_weekly_rebalance("weekly", "2021-01-04", None)
    assert should_skip_weekly_rebalance("weekly", "2021-01-05", "2021-01-04")
    assert should_skip_weekly_rebalance("weekly", "2021-01-08", "2021-01-05")
    assert not should_skip_weekly_rebalance("weekly", "2021-01-11", "2021-01-08")


def test_daily_never_skips():
    assert not should_skip_weekly_rebalance("daily", "2021-01-05", "2021-01-04")
    assert not should_skip_weekly_rebalance("daily", "2021-01-06", "2021-01-05")


def test_5d_cadence_every_five_trading_days():
    # First day always rebalances; then 4 skipped, 5th trading day rebalances.
    assert not should_skip_weekly_rebalance("5d", "2021-01-04", None,
                                            trading_dates=CAL, last_rebalance_date=None)
    assert should_skip_weekly_rebalance("5d", "2021-01-05", "2021-01-04",
                                        trading_dates=CAL, last_rebalance_date="2021-01-04")
    assert should_skip_weekly_rebalance("5d", "2021-01-08", "2021-01-07",
                                        trading_dates=CAL, last_rebalance_date="2021-01-04")
    # 5th trading day after 01-04 is 01-11 (4 skipped).
    assert not should_skip_weekly_rebalance("5d", "2021-01-11", "2021-01-08",
                                            trading_dates=CAL, last_rebalance_date="2021-01-04")
    # Next anchor moves to 01-11; 01-12..01-15 are only 1..4 trading days
    # after it, so they all skip (the 5th trading day after 01-11 would be
    # 01-18, outside this calendar).
    assert should_skip_weekly_rebalance("5d", "2021-01-12", "2021-01-11",
                                        trading_dates=CAL, last_rebalance_date="2021-01-11")
    assert should_skip_weekly_rebalance("5d", "2021-01-15", "2021-01-14",
                                        trading_dates=CAL, last_rebalance_date="2021-01-11")


def test_20d_cadence_skips_two_full_weeks():
    assert should_skip_weekly_rebalance("20d", "2021-01-15", "2021-01-14",
                                        trading_dates=CAL, last_rebalance_date="2021-01-04")


def test_1d_behaves_daily():
    assert not should_skip_weekly_rebalance("1d", "2021-01-05", "2021-01-04",
                                            trading_dates=CAL, last_rebalance_date="2021-01-04")


def test_nd_requires_calendar():
    import pytest
    with pytest.raises(ValueError):
        should_skip_weekly_rebalance("20d", "2021-01-15", "2021-01-14",
                                     last_rebalance_date="2021-01-04")


# ── Offset: phase shift of the "<n>d" cadence grid ─────────────────────────
# offset=k puts the FIRST rebalance on the k-th trading day (0-indexed), then
# every n trading days after that anchor.  offset=0 is the historical grid.


def test_offset_shifts_first_rebalance_and_keeps_grid():
    # "3d" cadence, offset=2 over the 10-day CAL: rebalances on idx 2, 5, 8.
    assert should_skip_weekly_rebalance("3d", CAL[0], None,
                                        trading_dates=CAL, last_rebalance_date=None, offset=2)
    assert should_skip_weekly_rebalance("3d", CAL[1], None,
                                        trading_dates=CAL, last_rebalance_date=None, offset=2)
    assert not should_skip_weekly_rebalance("3d", CAL[2], None,
                                            trading_dates=CAL, last_rebalance_date=None, offset=2)
    # Anchor at idx 2 (01-06); 3 trading days later is idx 5 (01-11).
    assert should_skip_weekly_rebalance("3d", CAL[3], CAL[2],
                                        trading_dates=CAL, last_rebalance_date=CAL[2], offset=2)
    assert should_skip_weekly_rebalance("3d", CAL[4], CAL[3],
                                        trading_dates=CAL, last_rebalance_date=CAL[2], offset=2)
    assert not should_skip_weekly_rebalance("3d", CAL[5], CAL[4],
                                            trading_dates=CAL, last_rebalance_date=CAL[2], offset=2)


def test_offset_zero_is_historical_grid():
    # offset=0: first day rebalances (idx 0), then every n trading days.
    assert not should_skip_weekly_rebalance("3d", CAL[0], None,
                                            trading_dates=CAL, last_rebalance_date=None, offset=0)
    assert should_skip_weekly_rebalance("3d", CAL[1], CAL[0],
                                        trading_dates=CAL, last_rebalance_date=CAL[0], offset=0)
    assert not should_skip_weekly_rebalance("3d", CAL[3], CAL[2],
                                            trading_dates=CAL, last_rebalance_date=CAL[0], offset=0)


def test_offset_defaults_to_zero():
    # Callers that omit offset keep the pre-offset behaviour (backward compat).
    assert not should_skip_weekly_rebalance("3d", CAL[0], None,
                                            trading_dates=CAL, last_rebalance_date=None)


def test_negative_offset_rejected():
    import pytest
    with pytest.raises(ValueError, match="offset"):
        should_skip_weekly_rebalance("3d", CAL[0], None,
                                     trading_dates=CAL, last_rebalance_date=None, offset=-1)


# ── Integration: posterior_confirmed + "<n>d" cadence ───────────────────────
# Regression: the posterior branch of run_from_signal_cache previously never
# passed trading_dates/last_rebalance_date to should_skip_weekly_rebalance, so
# any "<n>d" cadence silently degraded to DAILY refresh (the anchor defaulted
# to None and the N-day branch returns "never skip").  These tests pin the
# cadence through observable order counts: on a skip day the account must not
# refresh (rank_exit / refill), so order_count must be 0.


def _rotating_signal_store(tmp_path) -> None:
    """10 instruments, 5 dates; top-5 rotates so rank_exit fires every refresh."""
    from qsys.signal.store import SignalStore

    store = SignalStore(str(tmp_path))
    n_dates, n_inst = 5, 10
    frames = []
    for d in range(n_dates):
        date = f"2026-06-{15 + d:02d}"
        scores = [(n_inst - i + d) % n_inst for i in range(n_inst)]
        frames.append(pd.DataFrame({
            "trade_date": [date] * n_inst,
            "data_date": [f"2026-06-{13 + d:02d}"] * n_inst,
            "instrument": [f"000{i:03d}.SZ" for i in range(n_inst)],
            "signal_id": ["rot"] * n_inst,
            "signal_run_id": ["rot_run"] * n_inst,
            "score": scores,
        }))
    store.save_signal_run(
        "rot", "rot_run", pd.concat(frames, ignore_index=True),
        check_no_lookahead=False, overwrite=True,
    )


def _run_posterior_cadence(tmp_path, rebalance_freq: str, offset: int = 0):
    """Run the rotating-signal backtest under a posterior_confirmed skeleton with
    only rank_exit enabled (all other exit rules dead) at the given cadence."""
    from unittest.mock import patch

    from qsys.backtest.strategy_runner import BacktestRunner

    _rotating_signal_store(tmp_path)
    trading_days = [
        "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",
    ]

    def _mock_calendar(start, end):
        return [d for d in trading_days if start <= d <= end]

    def _mock_prices(trade_date, instruments, price_col="close"):
        prices = {inst: 10.0 + float(i) * 0.5
                  for i, inst in enumerate(sorted(instruments))}
        status = pd.DataFrame({
            "is_suspended": 0, "is_limit_up": 0, "is_limit_down": 0,
        }, index=sorted(instruments))
        return prices, status

    with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
         patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
        return BacktestRunner().run_from_signal_cache(
            signal_id="rot",
            signal_run_id="rot_run",
            start_date="2026-06-15",
            end_date="2026-06-19",
            research_root=str(tmp_path),
            output_dir=tmp_path / "bt_rot",
            overwrite=True,
            commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0,
            top_n=5,
            holding_policy="posterior_confirmed",
            rebalance_freq=rebalance_freq,
            rebalance_offset=offset,
            rank_exit=True,
            # Dead rules (same dummy thresholds as the E1 ablation baseline).
            score_delta_min_observations=1_000_000_000,
            posterior_stop_loss=0.999,
            winner_activation_return=0.9999,
            winner_trailing_stop=0.999,
            stale_after_days=10000,
            replacement_rank_gap=1000000,
        )


def test_posterior_2d_cadence_refreshes_on_schedule(tmp_path):
    """2d cadence: refresh on 06-15/17/19, no orders on 06-16/18.

    If the posterior branch forgot the anchor this would be daily refresh and
    06-16/18 would carry rank_exit + refill orders.
    """
    result = _run_posterior_cadence(tmp_path, "2d")
    assert result.status == "completed"
    orders = [d["order_count"] for d in result.daily_summary]
    assert orders[0] > 0   # 06-15 initial entry
    assert orders[1] == 0  # 06-16 skip
    assert orders[2] > 0   # 06-17 refresh
    assert orders[3] == 0  # 06-18 skip
    assert orders[4] > 0   # 06-19 refresh


def test_posterior_5d_cadence_refreshes_once(tmp_path):
    """5d cadence over a 5-day window: only the first day refreshes; the 5th
    trading day after the anchor is outside the window."""
    result = _run_posterior_cadence(tmp_path, "5d")
    assert result.status == "completed"
    orders = [d["order_count"] for d in result.daily_summary]
    assert orders[0] > 0
    assert orders[1] == 0
    assert orders[2] == 0
    assert orders[3] == 0
    assert orders[4] == 0  # 06-19 is the 4th trading day after 06-15 → skip


def test_posterior_daily_cadence_refreshes_every_day(tmp_path):
    """Control: daily cadence refreshes every day (rank_exit fires daily)."""
    result = _run_posterior_cadence(tmp_path, "daily")
    assert result.status == "completed"
    orders = [d["order_count"] for d in result.daily_summary]
    assert all(o > 0 for o in orders)


def test_posterior_offset_phase_shifts_cadence_grid(tmp_path):
    """offset=1 with 2d cadence: first rebalance on the 2nd trading day, then
    every 2 trading days (idx 1, 3).  idx 0 is before the phase-shifted grid."""
    result = _run_posterior_cadence(tmp_path, "2d", offset=1)
    assert result.status == "completed"
    orders = [d["order_count"] for d in result.daily_summary]
    assert orders[0] == 0  # idx 0 before the offset grid
    assert orders[1] > 0   # idx 1: first rebalance
    assert orders[2] == 0
    assert orders[3] > 0   # idx 3: 2 trading days after anchor
    assert orders[4] == 0


def test_daily_summary_carries_rebalance_flags(tmp_path):
    """Every posterior daily_summary row records rebalance_due (schedule) and
    is_rebalance (execution).  On a 2d cadence the grid days carry both True;
    skip days carry both False.  The flags are execution truth, not an
    entry-count proxy."""
    result = _run_posterior_cadence(tmp_path, "2d")
    rows = result.daily_summary
    assert len(rows) == 5
    for i, d in enumerate(rows):
        assert "rebalance_due" in d and "is_rebalance" in d
        expect_reb = (i in (0, 2, 4))  # 2d cadence over 5 days: idx 0, 2, 4
        assert bool(d["rebalance_due"]) == expect_reb
        assert bool(d["is_rebalance"]) == expect_reb
    # Execution truth is independent of entry count: on a 2d cadence the
    # rebalance days may have entries, but the flags must not depend on it.
    reb_rows = [d for d in rows if d["is_rebalance"]]
    assert all(d["rebalance_due"] for d in reb_rows)
