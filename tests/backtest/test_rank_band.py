"""Rank-hysteresis band tests for the posterior_confirmed holding policy.

The band (rank_exit + rank_exit_hold_top) is the event-driven alternative to a
fixed cadence: evaluated weekly, entries come from the current top-5, a held
name is kept while its current rank is <= hold_top (10), and exits only when it
falls to rank > hold_top; slots refill from the current top-5 to exactly top_n
holdings, hold drift, all four exit rules disabled.

Key properties under test:
  - A name inside the band (rank 6..10) is KEPT even though it dropped out of
    the entry top-5 (hysteresis: no turnover, no refill).
  - A name outside the band (rank > 10) is SOLD and refilled from the current
    top-5; the book returns to exactly 5 holdings.
  - Evaluation happens only on weekly rebalance days; skip days trade nothing.
  - rank_exit_hold_top default (None) is the plain top-5 dropout, excluded from
    the manifest so pre-band backtest hashes stay stable.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from qsys.backtest.posterior_policy import PosteriorPolicyConfig
from qsys.backtest.strategy_runner import BacktestRunner
from qsys.signal.store import SignalStore

# Two ISO weeks (eval days are the two Mondays: 06-15 and 06-22).
_CAL = [
    "2026-06-15", "2026-06-16", "2026-06-17", "2026-06-18", "2026-06-19",
    "2026-06-22", "2026-06-23", "2026-06-24", "2026-06-25", "2026-06-26",
]
_EVAL = {"2026-06-15", "2026-06-22"}


def _mock_calendar(start, end):
    return [d for d in _CAL if start <= d <= end]


def _mock_prices(trade_date, instruments, price_col="close"):
    prices = {inst: 10.0 + float(i) * 0.5
              for i, inst in enumerate(sorted(instruments))}
    status = pd.DataFrame({
        "is_suspended": 0, "is_limit_up": 0, "is_limit_down": 0,
    }, index=sorted(instruments))
    return prices, status


def _band_signal(store: SignalStore, *, drop_out_of_band: bool) -> None:
    """12 instruments; day 1 top-5 = {0000..0004}.

    Day 2 (06-22) scores:
      - 0000..0003 stay ranks 1-4 (kept).
      - 0009 rises to rank 5 (top-5 entry candidate).
      - 0004 is rank 6 (still inside the hold band) when drop_out_of_band is
        False, or rank 12 (> band) when True.
    """
    insts = [f"000{i:03d}.SZ" for i in range(12)]
    day1 = {insts[0]: 100.0, insts[1]: 90.0, insts[2]: 80.0, insts[3]: 70.0,
            insts[4]: 60.0, insts[5]: 50.0, insts[6]: 40.0, insts[7]: 30.0,
            insts[8]: 20.0, insts[9]: 10.0, insts[10]: 8.0, insts[11]: 6.0}
    if drop_out_of_band:
        # 0004 falls to rank 12 (behind 0009 and two new names).
        day2 = {insts[0]: 100.0, insts[1]: 90.0, insts[2]: 80.0, insts[3]: 70.0,
                insts[9]: 60.0, insts[5]: 55.0, insts[6]: 50.0, insts[7]: 45.0,
                insts[8]: 40.0, insts[10]: 38.0, insts[11]: 36.0, insts[4]: 35.0}
    else:
        # 0004 stays rank 6 (inside the band), 0009 rises to rank 5.
        day2 = {insts[0]: 100.0, insts[1]: 90.0, insts[2]: 80.0, insts[3]: 70.0,
                insts[9]: 60.0, insts[4]: 55.0, insts[5]: 50.0, insts[6]: 45.0,
                insts[7]: 40.0, insts[8]: 35.0, insts[10]: 30.0, insts[11]: 25.0}
    frames = []
    for d, scores in (("2026-06-15", day1), ("2026-06-22", day2)):
        for inst, score in scores.items():
            frames.append(pd.DataFrame({
                "trade_date": [d], "data_date": ["2026-06-13" if d == "2026-06-15" else "2026-06-19"],
                "instrument": [inst], "signal_id": ["band_sig"],
                "signal_run_id": ["band_run"], "score": [score],
            }))
    store.save_signal_run(
        "band_sig", "band_run", pd.concat(frames, ignore_index=True),
        check_no_lookahead=False, overwrite=True,
    )


def _run_band(tmp_path, *, drop_out_of_band: bool, hold_top: int | None = 10):
    store = SignalStore(str(tmp_path))
    _band_signal(store, drop_out_of_band=drop_out_of_band)
    with patch("qsys.backtest.strategy_runner.fetch_market_snapshot", _mock_prices), \
         patch("qsys.backtest.strategy_runner._resolve_trading_dates", _mock_calendar):
        return BacktestRunner().run_from_signal_cache(
            signal_id="band_sig",
            signal_run_id="band_run",
            start_date="2026-06-15",
            end_date="2026-06-26",
            research_root=str(tmp_path),
            output_dir=tmp_path / "bt_band",
            overwrite=True,
            commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0,
            top_n=5, initial_capital=100_000.0,
            holding_policy="posterior_confirmed",
            rebalance_freq="weekly",
            rank_exit=True,
            rank_exit_hold_top=hold_top,
            # Dead rules (E1 skeleton).
            score_delta_min_observations=1_000_000_000,
            posterior_stop_loss=0.999,
            winner_activation_return=0.9999,
            winner_trailing_stop=0.999,
            stale_after_days=10000,
            replacement_rank_gap=1000000,
        )


class TestRankBand:
    def test_hysteresis_keeps_name_inside_band_no_turnover(self, tmp_path) -> None:
        """0004 drops to rank 6 (inside the hold band): NOT sold on the next
        weekly eval.  The 0009 name at rank 5 gets no slot (book is full), so
        the eval day carries zero orders — the hysteresis effect."""
        result = _run_band(tmp_path, drop_out_of_band=False)
        assert result.status == "completed"
        rows = {d["trade_date"]: d for d in result.daily_summary}
        # Day 1 (06-15) is the first weekly eval: initial entry.
        assert rows["2026-06-15"]["order_count"] > 0
        assert rows["2026-06-15"]["position_count"] == 5
        # Skip days (06-16..06-19): no trading.
        for d in _CAL[1:5]:
            assert rows[d]["order_count"] == 0, f"unexpected trade on {d}"
        # Day 2 weekly eval (06-22): band keeps 0004 -> no turnover at all.
        assert rows["2026-06-22"]["order_count"] == 0
        assert rows["2026-06-22"]["position_count"] == 5
        # Portfolio composition unchanged: 0000..0004 still held.
        sells = [e for e in _execs(result) if e["trade_date"] == "2026-06-22" and e["side"] == "sell"]
        buys = [e for e in _execs(result) if e["trade_date"] == "2026-06-22" and e["side"] == "buy"]
        assert sells == [] and buys == []

    def test_rank_above_band_is_sold_and_refilled_to_five(self, tmp_path) -> None:
        """0004 falls to rank 12 (> 10): SOLD on the 06-22 eval; 0009 (rank 5)
        refills the slot -> still exactly 5 holdings."""
        result = _run_band(tmp_path, drop_out_of_band=True)
        assert result.status == "completed"
        rows = {d["trade_date"]: d for d in result.daily_summary}
        assert rows["2026-06-15"]["position_count"] == 5
        execs = _execs(result)
        sells_0622 = [e for e in execs if e["trade_date"] == "2026-06-22" and e["side"] == "sell"]
        buys_0622 = [e for e in execs if e["trade_date"] == "2026-06-22" and e["side"] == "buy"]
        assert len(sells_0622) == 1 and sells_0622[0]["instrument"] == "000004.SZ"
        assert len(buys_0622) == 1 and buys_0622[0]["instrument"] == "000009.SZ"
        assert rows["2026-06-22"]["position_count"] == 5

    def test_weekly_evaluation_only_on_rebalance_days(self, tmp_path) -> None:
        """rebalance_due / is_rebalance flags are True only on the two Monday
        eval days; the band does no signal work between evals."""
        result = _run_band(tmp_path, drop_out_of_band=True)
        rows = result.daily_summary
        for d in rows:
            assert bool(d["rebalance_due"]) == (d["trade_date"] in _EVAL)
            assert bool(d["is_rebalance"]) == (d["trade_date"] in _EVAL)

    def test_config_validates_hold_top_and_drops_default(self) -> None:
        PosteriorPolicyConfig(rank_exit=True, rank_exit_hold_top=10).validate()
        with pytest.raises(ValueError, match="rank_exit_hold_top"):
            PosteriorPolicyConfig(rank_exit=True, rank_exit_hold_top=0).validate()
        # Default (None) is excluded from the manifest -> plain rank_exit hashes
        # stay stable; a set band is recorded.
        m_default = PosteriorPolicyConfig(rank_exit=True).to_manifest()
        assert "rank_exit_hold_top" not in m_default
        m_band = PosteriorPolicyConfig(rank_exit=True, rank_exit_hold_top=10).to_manifest()
        assert m_band["rank_exit_hold_top"] == 10

    def test_band_backtest_id_differs_from_plain_rank_exit(self, tmp_path) -> None:
        plain = _run_band(tmp_path, drop_out_of_band=True, hold_top=None)
        band = _run_band(tmp_path, drop_out_of_band=True, hold_top=10)
        assert plain.backtest_id != band.backtest_id


def _execs(result) -> list[dict]:
    import json
    import pandas as pd
    from pathlib import Path
    out = Path(result.artifacts["manifest"]).parent / "executions.csv"
    df = pd.read_csv(out)
    return df.to_dict(orient="records")
