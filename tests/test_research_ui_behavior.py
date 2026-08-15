from __future__ import annotations

import datetime
import math

import pandas as pd
import pytest

from qsys.research_ui.behavior import derive_episodes, summarize_episodes


def _row(execution_id, date, seq, symbol, side, reason, qty, price, fee=0.0, status="filled"):
    return {
        "execution_id": execution_id, "trade_date": date, "sequence": seq,
        "instrument": symbol, "side": side, "trade_reason": reason,
        "filled_qty": qty, "deal_price": price, "total_fee": fee, "status": status,
    }


def _prices(dates):
    """dates: list of (date, open, high, low, close)."""
    return pd.DataFrame(
        [{"trade_date": d, "open": o, "high": h, "low": l, "close": c}
         for d, o, h, l, c in dates]
    )


def _weekdays(start: str, end: str) -> list[str]:
    """Mon–Fri dates between two ISO dates inclusive (trading-day stand-in)."""
    out = []
    day = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    while day <= last:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += datetime.timedelta(days=1)
    return out


def test_episode_simple_round_trip():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "hard_stop", 100, 12.0, fee=1.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-05", 10.3, 11.0, 10.0, 10.8),
        ("2021-02-01", 11.9, 12.5, 11.5, 12.0),
        ("2021-02-02", 12.0, 12.8, 11.8, 12.5),
    ])}
    calendar = _weekdays("2021-01-04", "2021-02-01")
    episodes = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["symbol"] == "600000.SH"
    assert ep["entry_date"] == "2021-01-04"
    assert ep["exit_date"] == "2021-02-01"
    assert ep["exit_reason"] == "hard_stop"
    # 21 trading days (weekdays 01-04 .. 02-01 inclusive), not the 2 exec dates.
    assert ep["holding_days"] == 21
    assert ep["realized_return"] == pytest.approx((1200 - 1) / (1000 + 1) - 1)
    assert ep["unrealized_return"] is None
    # excursion days: 01-04 (after buy), 01-05; 02-01 closes → not counted.
    avg_cost = (1000 + 1) / 100
    assert ep["MFE"] == pytest.approx(max(10.5 / avg_cost - 1, 11.0 / avg_cost - 1))
    assert ep["MAE"] == pytest.approx(min(9.5 / avg_cost - 1, 10.0 / avg_cost - 1))
    # post_exit: exit=02-01, +20/+60 both past data end → null
    assert ep["post_exit_return_20d"] is None
    assert ep["post_exit_return_60d"] is None


def test_episode_add_then_full_exit():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("b2", "2021-01-08", 0, "600000.SH", "buy", "top_n_entry", 50, 11.0, fee=0.5),
        _row("s1", "2021-01-12", 0, "600000.SH", "sell", "score_delta_exit", 150, 12.0, fee=1.5),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-12", 12.0, 12.5, 11.5, 12.0),
        ("2021-01-13", 12.0, 12.8, 11.8, 12.5),
        ("2021-01-14", 12.5, 13.0, 12.2, 12.9),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["exit_reason"] == "score_delta_exit"
    buys_cost = (100 * 10 + 1) + (50 * 11 + 0.5)
    sells_proc = 150 * 12 - 1.5
    assert ep["realized_return"] == pytest.approx(sells_proc / buys_cost - 1)
    assert ep["symbol"] == "600000.SH"


def test_episode_partial_sell_keeps_episode_open():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "score_delta_exit", 40, 11.0, fee=0.4),
        _row("s2", "2021-01-12", 0, "600000.SH", "sell", "hard_stop", 60, 9.0, fee=0.6),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-12", 9.0, 9.5, 8.5, 9.0),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["exit_reason"] == "hard_stop"  # closing sell wins
    assert ep["holding_days"] == 3
    sells_proc = (40 * 11 - 0.4) + (60 * 9 - 0.6)
    buys_cost = 100 * 10 + 1
    assert ep["realized_return"] == pytest.approx(sells_proc / buys_cost - 1)


def test_episode_sell_then_rebuy_starts_new_episode():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
        _row("b2", "2021-01-12", 0, "600000.SH", "buy", "top_n_entry", 100, 11.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 12.0, 12.5, 11.5, 12.0),
        ("2021-01-12", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-13", 11.0, 11.5, 10.5, 11.2),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 2
    assert episodes[0]["exit_reason"] == "winner_trailing"
    assert episodes[0]["exit_date"] == "2021-01-08"
    assert episodes[1]["entry_date"] == "2021-01-12"
    assert episodes[1]["exit_reason"] == "open"  # second still held


def test_episode_open_at_data_end_has_unrealized_and_open_reason():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("b2", "2021-01-08", 0, "600000.SH", "buy", "top_n_entry", 50, 11.0, fee=0.5),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-09", 11.2, 11.8, 10.8, 11.5),
    ])}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["exit_reason"] == "open"
    assert ep["exit_date"] == "2021-01-09"
    avg_cost = ((100 * 10 + 1) + (50 * 11 + 0.5)) / 150
    assert ep["unrealized_return"] == pytest.approx(11.5 / avg_cost - 1)
    assert ep["realized_return"] is None


def test_episode_scores_and_score_delta():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "score_delta_exit", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-02-01", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-02", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-03", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-04", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-05", 12.0, 12.5, 11.5, 12.0),
    ])}
    scores = pd.DataFrame({
        "trade_date": ["2021-01-04", "2021-02-01", "2021-01-05", "2021-02-01"],
        "instrument": ["600000.SH"] * 4,
        "score": [0.5, 0.2, 0.1, 0.3],
    })
    episodes = derive_episodes(rows, prices_by_symbol=prices, scores_frame=scores)
    ep = episodes[0]
    assert ep["entry_score"] == pytest.approx(0.5)
    # duplicate (2021-02-01, 600000.SH) → last wins in dict build: 0.3
    assert ep["exit_score"] == pytest.approx(0.3)
    assert ep["score_delta_20d"] is None  # calendar only has 2 distinct days
    assert ep["score_delta_5d"] is None


def test_episode_post_exit_returns_when_price_data_sufficient():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
    ]
    start = datetime.date(2021, 1, 4)
    dates = []
    for i in range(40):  # ~28 weekdays
        d = start + datetime.timedelta(days=i)
        if d.weekday() >= 5:
            continue
        dates.append((d.isoformat(), 10.0, 10.5, 9.5, 10.0 + i * 0.01))
    prices = {"600000.SH": _prices(dates)}
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    ep = episodes[0]
    # exit 2021-01-08 sits at weekday index 4; +20 lands mid-series, +60 is past the end.
    assert ep["post_exit_return_20d"] is not None
    assert ep["post_exit_return_60d"] is None


def test_holding_days_uses_trading_calendar_not_execution_dates():
    # Executions are sparse (weekly rebalance: only 2 fill dates), but the
    # trading calendar spans every weekday in between. holding_days must count
    # trading days, not distinct fill/price dates.
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "hard_stop", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-02-01", 11.9, 12.5, 11.5, 12.0),
    ])}
    calendar = _weekdays("2021-01-04", "2021-02-05")  # extends past exit
    ep = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)[0]
    assert ep["holding_days"] == 21  # weekdays 01-04..02-01, not the 2 fill dates


def test_open_episode_bounded_to_calendar_end_ignores_post_window_prices():
    # The backtest window ends 01-05, but price data extends to 01-07. The open
    # episode must finalize at the window edge and never read post-window
    # prices (a 13.5 high / 13.0 close would otherwise inflate MFE/unrealized).
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-05", 10.3, 11.0, 10.0, 10.8),
        ("2021-01-06", 10.8, 12.0, 10.5, 11.8),
        ("2021-01-07", 12.5, 13.5, 12.0, 13.0),
    ])}
    calendar = _weekdays("2021-01-04", "2021-01-05")
    ep = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)[0]
    assert ep["exit_reason"] == "open"
    assert ep["exit_date"] == "2021-01-05"  # bounded, not 01-07
    avg_cost = (1000 + 1) / 100
    assert ep["MFE"] == pytest.approx(11.0 / avg_cost - 1)  # only 01-04/01-05 highs
    assert ep["MAE"] == pytest.approx(min(9.5 / avg_cost - 1, 10.0 / avg_cost - 1))
    assert ep["unrealized_return"] == pytest.approx(10.8 / avg_cost - 1)  # 01-05 close
    assert ep["holding_days"] == 2  # calendar index 0..1


def test_same_day_close_then_reopen_skips_entry_day_excursion():
    # 01-04: buy → sell → buy again. The day's high 12.0 / low 8.0 may precede
    # the reopened position, so excursion for the reopened episode starts the
    # following day. The same-day-open/closed episodes get no excursion at all.
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-04", 1, "600000.SH", "sell", "hard_stop", 100, 10.0),
        _row("b2", "2021-01-04", 2, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s2", "2021-01-05", 0, "600000.SH", "sell", "hard_stop", 100, 11.0),
        _row("b3", "2021-01-05", 1, "600000.SH", "buy", "top_n_entry", 100, 11.0),
        _row("s3", "2021-01-08", 0, "600000.SH", "sell", "hard_stop", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 12.0, 8.0, 10.0),
        ("2021-01-05", 11.0, 11.5, 10.5, 11.0),
        ("2021-01-06", 11.0, 11.5, 10.5, 11.2),
        ("2021-01-07", 11.2, 11.8, 10.8, 11.5),
        ("2021-01-08", 12.0, 12.5, 11.5, 12.0),
    ])}
    calendar = _weekdays("2021-01-04", "2021-01-08")
    episodes = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)
    assert len(episodes) == 3
    # Same-day open/close episodes never get an excursion row.
    assert episodes[0]["MFE"] is None
    assert episodes[0]["MAE"] is None
    assert episodes[1]["MFE"] is None
    assert episodes[1]["MAE"] is None
    # Reopened episode: cost basis 11.0 (b3 @01-05), excursion starts 01-06;
    # 01-04 high 12.0/low 8.0 and the 01-05 reopen day are all excluded.
    third = episodes[2]
    assert third["MFE"] == pytest.approx(11.8 / 11.0 - 1)  # not 12.0/11-1
    assert third["MAE"] == pytest.approx(10.5 / 11.0 - 1)  # not 8.0/11-1


def test_scores_frame_without_score_column_does_not_crash():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "score_delta_exit", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 12.0, 12.5, 11.5, 12.0),
    ])}
    # No "score" and no "score_raw" column → per-cell KeyError must be swallowed.
    scores = pd.DataFrame({
        "trade_date": ["2021-01-04", "2021-01-08"],
        "instrument": ["600000.SH", "600000.SH"],
        "prediction": [0.5, 0.3],
    })
    episodes = derive_episodes(rows, prices_by_symbol=prices, scores_frame=scores)
    assert len(episodes) == 1
    assert episodes[0]["entry_score"] is None
    assert episodes[0]["exit_score"] is None


def test_summarize_episodes_groups_by_exit_reason():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "hard_stop", 100, 12.0),
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 100, 20.0),
        _row("s2", "2021-01-08", 0, "600001.SH", "sell", "hard_stop", 100, 18.0),
        _row("b3", "2021-01-04", 0, "600002.SH", "buy", "top_n_entry", 100, 30.0),
    ]
    prices = {
        "600000.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 12, 12.5, 11.5, 12)]),
        "600001.SH": _prices([("2021-01-04", 20, 20.5, 19.5, 20.2), ("2021-01-08", 18, 18.5, 17.5, 18)]),
        "600002.SH": _prices([("2021-01-04", 30, 30.5, 29.5, 30.2), ("2021-01-08", 30, 30.5, 29.5, 30.2)]),
    }
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    summary = summarize_episodes(episodes)
    assert summary["total_episodes"] == 3
    assert summary["closed_episodes"] == 2
    assert summary["open_episodes"] == 1
    assert summary["win_rate"] == pytest.approx(0.5)
    reasons = {r["exit_reason"]: r for r in summary["by_exit_reason"]}
    assert set(reasons) == {"hard_stop"}
    assert reasons["hard_stop"]["count"] == 2


def test_summarize_episodes_median_handles_even_count():
    rows = []
    for i, (symbol, ret_price) in enumerate([("600001.SH", 10.0), ("600002.SH", 12.0)]):
        rows.append(_row(f"b{i}", "2021-01-04", 0, symbol, "buy", "top_n_entry", 100, 10.0))
        rows.append(_row(f"s{i}", "2021-01-08", 0, symbol, "sell", "hard_stop", 100, ret_price))
    prices = {
        "600001.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 10, 10.5, 9.5, 10.2)]),
        "600002.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 12, 12.5, 11.5, 12.2)]),
    }
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices))
    # returns 0.0 and 0.2 → true median 0.1 (upper-middle would be 0.2)
    assert summary["median_return"] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# P0 / P1 correctness & diagnostics scenarios
# ---------------------------------------------------------------------------


def _assert_no_non_finite(ep: dict) -> None:
    for v in ep.values():
        if isinstance(v, float):
            assert math.isfinite(v), f"non-finite value {v!r} leaked into episode {ep['symbol']}"


def test_closed_episode_cashflow_and_pnl_fields():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "hard_stop", 100, 12.0, fee=1.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-02-01", 11.9, 12.5, 11.5, 12.0),
    ])}
    ep = derive_episodes(rows, prices_by_symbol=prices)[0]
    # P0.1 — cashflow / PnL fields replace the popped internal accumulators.
    assert ep["total_buy_cashflow"] == pytest.approx(1001.0)      # 100*10 + 1 fee
    assert ep["total_sell_proceeds"] == pytest.approx(1199.0)     # 100*12 - 1 fee
    assert ep["episode_pnl"] == pytest.approx(198.0)
    assert ep["gross_buy_return"] == pytest.approx(198.0 / 1001.0)
    assert ep["realized_return"] == ep["gross_buy_return"]
    assert "buy_cost" not in ep and "sell_proceeds" not in ep
    # P0.2 — closed episodes: episode_end == exit, no valuation date.
    assert ep["episode_end_date"] == "2021-02-01"
    assert ep["valuation_date"] is None
    _assert_no_non_finite(ep)


def test_open_episode_end_vs_valuation_and_holding_to_window_end():
    # Backtest window runs to 01-08; the symbol's last bar is 01-05.  The open
    # episode ends at the window edge but is valued at its last bar, and
    # holding_days counts to the window end, not the last price date.
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0, fee=1.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-05", 10.3, 11.0, 10.0, 10.8),
    ])}
    calendar = _weekdays("2021-01-04", "2021-01-08")
    ep = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)[0]
    assert ep["exit_reason"] == "open"
    assert ep["episode_end_date"] == "2021-01-08"
    assert ep["valuation_date"] == "2021-01-05"
    assert ep["exit_date"] == "2021-01-05"  # backward-compat: last price date
    avg_cost = 10.01
    assert ep["unrealized_return"] == pytest.approx(10.8 / avg_cost - 1)
    assert ep["episode_pnl"] == pytest.approx(100 * 10.8 - 1001.0)
    assert ep["gross_buy_return"] == pytest.approx((100 * 10.8 - 1001.0) / 1001.0)
    assert ep["realized_return"] is None
    assert ep["holding_days"] == 5  # 01-04 .. 01-08, to window end
    _assert_no_non_finite(ep)


def test_open_episode_valued_at_last_available_close_when_prices_end_early():
    # Symbol suspended after 01-08; window ends 01-15.  valuation must use the
    # 01-08 close, and holding_days still counts to the window end.
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 11.0, 11.5, 10.5, 11.0),
    ])}
    calendar = _weekdays("2021-01-04", "2021-01-15")
    ep = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)[0]
    assert ep["episode_end_date"] == "2021-01-15"
    assert ep["valuation_date"] == "2021-01-08"
    assert ep["unrealized_return"] == pytest.approx(11.0 / 10.0 - 1)
    assert ep["episode_pnl"] == pytest.approx(100 * 11.0 - 1000.0)
    assert ep["holding_days"] == 10  # 01-04 .. 01-15 weekdays
    _assert_no_non_finite(ep)


def test_non_finite_prices_normalized_to_none():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "hard_stop", 100, 12.0),
    ]
    prices = {"600000.SH": pd.DataFrame([
        {"trade_date": "2021-01-04", "open": 10.0, "high": float("nan"), "low": 9.5, "close": 10.2},
        {"trade_date": "2021-01-08", "open": 12.0, "high": float("inf"), "low": 11.5, "close": 12.0},
    ])}
    ep = derive_episodes(rows, prices_by_symbol=prices)[0]
    assert ep["MFE"] is None        # NaN/inf highs normalized away
    assert ep["MAE"] == pytest.approx(9.5 / 10.0 - 1)
    assert ep["realized_return"] == pytest.approx(0.2)
    _assert_no_non_finite(ep)


def test_post_exit_uses_market_calendar_not_symbol_price_dates():
    # The symbol has only 3 bars; exit is 01-08 and 02-05 is exactly 20 market
    # days later.  Market-calendar semantics must find the 02-05 bar even
    # though it is not 20 *price* rows after the exit bar.
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-05", 13.0, 13.5, 12.5, 13.2),  # calendar[exit_idx + 20]
    ])}
    calendar = _weekdays("2021-01-04", "2021-02-12")
    ep = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)[0]
    assert ep["post_exit_return_20d"] == pytest.approx(13.2 / 12.0 - 1)
    assert ep["post_exit_return_60d"] is None


def test_post_exit_none_when_symbol_missing_on_calendar_target_day():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
    ]
    # No bar on calendar[exit_idx+20] → post_exit_20d stays None (delisted).
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-01-08", 12.0, 12.5, 11.5, 12.0),
    ])}
    calendar = _weekdays("2021-01-04", "2021-02-12")
    ep = derive_episodes(rows, prices_by_symbol=prices, calendar=calendar)[0]
    assert ep["post_exit_return_20d"] is None
    assert ep["post_exit_return_60d"] is None


def test_summary_excludes_none_and_reports_counts():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "hard_stop", 100, 12.0),
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 100, 20.0),
        _row("s2", "2021-01-08", 0, "600001.SH", "sell", "hard_stop", 100, 18.0),
        _row("b3", "2021-01-04", 0, "600002.SH", "buy", "top_n_entry", 100, 30.0),  # open, no realized
    ]
    prices = {
        "600000.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 12, 12.5, 11.5, 12)]),
        "600001.SH": _prices([("2021-01-04", 20, 20.5, 19.5, 20.2), ("2021-01-08", 18, 18.5, 17.5, 18)]),
        "600002.SH": _prices([("2021-01-04", 30, 30.5, 29.5, 30.2)]),
    }
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices))
    assert summary["return_count"] == 2
    assert summary["win_rate"] == pytest.approx(0.5)
    hs = next(r for r in summary["by_exit_reason"] if r["exit_reason"] == "hard_stop")
    assert hs["count"] == 2
    assert hs["return_count"] == 2
    assert hs["mfe_count"] == 2
    assert hs["mae_count"] == 2
    assert hs["median_mfe"] == pytest.approx(0.0375)
    assert hs["median_mae"] == pytest.approx(-0.0375)


def test_capture_giveback_recovery_metrics():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 11.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 12.0, 8.0, 10.2),   # MFE 0.2, MAE -0.2
        ("2021-01-08", 11.0, 11.5, 10.5, 11.0),  # exit @ 11.0
    ])}
    ep = derive_episodes(rows, prices_by_symbol=prices)[0]
    assert ep["MFE"] == pytest.approx(0.2)
    assert ep["MAE"] == pytest.approx(-0.2)
    assert ep["realized_return"] == pytest.approx(0.1)
    assert ep["capture_ratio"] == pytest.approx(0.1 / 0.2)
    # P1.1: giveback is split into an absolute return (MFE − final) and a ratio (1 − capture).
    assert ep["giveback_return"] == pytest.approx(0.2 - 0.1)
    assert ep["giveback_ratio"] == pytest.approx(1 - 0.1 / 0.2)
    assert ep["recovery_from_mae"] == pytest.approx(1.1 / 0.8 - 1)


def test_capture_ratio_distribution():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 15.0),
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s2", "2021-01-08", 0, "600001.SH", "sell", "winner_trailing", 100, 14.5),
    ]
    prices = {
        "600000.SH": _prices([("2021-01-04", 10, 20, 5, 10.2), ("2021-01-08", 15, 15.5, 14.5, 15)]),   # MFE 1.0 → capture 0.5
        "600001.SH": _prices([("2021-01-04", 10, 15, 5, 10.2), ("2021-01-08", 14.5, 15, 14, 14.5)]),   # MFE 0.5 → capture 0.9
    }
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices))
    # P2.1: winners-by-capture buckets renamed Capture Ratio Distribution.
    buckets = {b["bucket"]: b for b in summary["capture_ratio_distribution"]}
    assert buckets["40-80%"]["count"] == 1
    assert buckets["80-100%"]["count"] == 1
    assert buckets["0-10%"]["count"] == 0
    assert buckets["give_back_loss"]["count"] == 0
    assert buckets["over_100%"]["count"] == 0


def test_holding_horizon_buckets():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 12.0),
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s2", "2021-01-29", 0, "600001.SH", "sell", "winner_trailing", 100, 12.0),
        _row("b3", "2021-01-04", 0, "600002.SH", "buy", "top_n_entry", 100, 10.0),  # stays open
    ]
    prices = {
        "600000.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 12, 12.5, 11.5, 12.0)]),
        "600001.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-29", 12, 12.5, 11.5, 12.0)]),
        "600002.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2)]),
    }
    calendar = _weekdays("2021-01-04", "2021-01-29")
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices, calendar=calendar))
    horizons = {h["bucket"]: h for h in summary["holding_horizons"]}
    assert horizons["0-10"]["count"] == 1
    assert horizons["11-20"]["count"] == 2
    assert horizons["0-10"]["exit_reasons"] == {"winner_trailing": 1}
    assert horizons["11-20"]["exit_reasons"] == {"winner_trailing": 1, "open": 1}
    # P2.2: each bucket also reports win_rate / median_return / median_mfe / median_mae.
    # 0-10 bucket = 600000.SH (exit 01-08): entry-day high 10.5 → MFE 0.05, return 0.2.
    assert horizons["0-10"]["win_rate"] == pytest.approx(1.0)
    assert horizons["0-10"]["median_return"] == pytest.approx(0.2)
    assert horizons["0-10"]["median_mfe"] == pytest.approx(0.05)
    assert horizons["0-10"]["median_mae"] == pytest.approx(-0.05)
    # 11-20 bucket = 600001.SH (realized 0.2) + 600002.SH (open → no realized return).
    assert horizons["11-20"]["win_rate"] == pytest.approx(1.0)
    assert horizons["11-20"]["median_return"] == pytest.approx(0.2)
    assert horizons["11-20"]["median_mfe"] == pytest.approx(0.05)


def test_hard_stop_false_stop_rate():
    rows = []
    for i, sym in enumerate(["600000.SH", "600001.SH", "600002.SH"]):
        rows.append(_row(f"b{i}", "2021-01-04", 0, sym, "buy", "top_n_entry", 100, 10.0))
        rows.append(_row(f"s{i}", "2021-01-08", 0, sym, "sell", "hard_stop", 100, 9.0))
    prices = {
        "600000.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 9, 9.5, 8.5, 9.0), ("2021-02-05", 10, 10.5, 9.5, 10.0)]),  # recovered
        "600001.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 9, 9.5, 8.5, 9.0), ("2021-02-05", 8, 8.5, 7.5, 8.0)]),   # kept falling
        "600002.SH": _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", 9, 9.5, 8.5, 9.0)]),                                  # no 20d bar
    }
    calendar = _weekdays("2021-01-04", "2021-02-12")
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices, calendar=calendar))
    sq = summary["stop_quality"]
    assert sq["hard_stop_count"] == 3
    assert sq["post_exit_20d_count"] == 2
    assert sq["false_stop_rate_20d"] == pytest.approx(0.5)  # 1 of 2 stopped exits recovered
    assert sq["false_stop_rate_60d"] is None


def test_pnl_concentration_top_shares_and_curve():
    rows = []
    prices = {}
    for i, (sym, exit_price) in enumerate([
        ("600004.SH", 20.0), ("600003.SH", 15.0), ("600002.SH", 12.0), ("600001.SH", 11.0),
    ]):
        rows.append(_row(f"b{i}", "2021-01-04", 0, sym, "buy", "top_n_entry", 100, 10.0))
        rows.append(_row(f"s{i}", "2021-01-08", 0, sym, "sell", "winner_trailing", 100, exit_price))
        prices[sym] = _prices([("2021-01-04", 10, 10.5, 9.5, 10.2), ("2021-01-08", exit_price, exit_price + 0.5, exit_price - 0.5, exit_price)])
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices))
    pc = summary["pnl_concentration"]
    assert pc["scope"] == "closed_realized"
    assert pc["n"] == 4
    pnls = sorted([100 * p - 1000 for p in (20, 15, 12, 11)], reverse=True)  # 1000, 500, 200, 100
    assert pc["total_pnl"] == pytest.approx(sum(pnls))
    assert pc["positive_pnl_total"] == pytest.approx(sum(pnls))
    # top 1%/5%/10% all select the single best episode (ceil(4*x) == 1).
    assert pc["top_1pct_share"] == pytest.approx(1000.0 / 1800.0)
    assert pc["top_5pct_share"] == pytest.approx(1000.0 / 1800.0)
    assert pc["top_10pct_share"] == pytest.approx(1000.0 / 1800.0)
    # P1.2: absolute top-N (fixed episode count) + pnl_ex_* (¥ left after removing top ranks).
    assert pc["top_1_episode_share"] == pytest.approx(1000.0 / 1800.0)
    assert pc["top_5_episode_share"] == pytest.approx(1.0)          # top 5 of 4 = everything
    assert pc["pnl_ex_top1"] == pytest.approx(1800.0 - 1000.0)      # 800
    assert pc["pnl_ex_top5"] == pytest.approx(0.0)                  # top 5 of 4 = everything
    assert pc["pnl_ex_top10pct"] == pytest.approx(1800.0 - 1000.0)  # top10pct_k = 1
    assert pc["share_denominator"] == "positive_pnl_total"
    assert pc["curve_points"] == 4
    curve = pc["cumulative_curve"]
    assert len(curve) == 4
    assert curve[0]["rank"] == 1 and curve[0]["pnl"] == pytest.approx(1000.0)
    assert curve[-1]["share_of_positive"] == pytest.approx(1.0)
    # P0.4: the last cumulative always equals total_pnl (full curve, not truncated).
    assert curve[-1]["cumulative"] == pytest.approx(pc["total_pnl"])


def test_by_exit_reason_extended_aggregates():
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "winner_trailing", 100, 11.0),
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s2", "2021-01-08", 0, "600001.SH", "sell", "winner_trailing", 100, 10.5),
    ]
    prices = {
        "600000.SH": _prices([
            ("2021-01-04", 10, 12.0, 8.0, 10.2),
            ("2021-01-08", 11, 11.5, 10.5, 11.0),
            ("2021-02-05", 13, 13.5, 12.5, 13.0),
        ]),
        "600001.SH": _prices([
            ("2021-01-04", 10, 11.0, 9.0, 10.2),
            ("2021-01-08", 10.5, 11.0, 10.0, 10.5),
            ("2021-02-05", 10, 10.5, 9.5, 10.0),
        ]),
    }
    calendar = _weekdays("2021-01-04", "2021-02-12")
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices, calendar=calendar))
    wt = next(r for r in summary["by_exit_reason"] if r["exit_reason"] == "winner_trailing")
    assert wt["count"] == 2
    assert wt["return_count"] == 2
    assert wt["median_mfe"] == pytest.approx((0.2 + 0.1) / 2)
    assert wt["median_mae"] == pytest.approx((-0.2 + -0.1) / 2)
    assert wt["median_capture"] == pytest.approx(0.5)          # 0.1/0.2 and 0.05/0.1
    # P1.1: giveback split into return (MFE − final) and ratio (1 − capture).
    assert wt["median_giveback_return"] == pytest.approx(0.075)  # (0.2−0.1 and 0.1−0.05) / 2
    assert wt["avg_giveback_return"] == pytest.approx(0.075)
    assert wt["median_giveback_ratio"] == pytest.approx(0.5)
    assert wt["avg_giveback_ratio"] == pytest.approx(0.5)
    assert wt["post_exit_20d_count"] == 2
    assert wt["avg_post_exit_20d"] == pytest.approx(((13.0 / 11.0 - 1) + (10.0 / 10.5 - 1)) / 2)


def test_score_map_skips_non_finite_scores():
    # P0.1 — the score map is finite-normalized: +inf / -inf / NaN scores are
    # dropped (entry_score stays None) and never leak into episode JSON.
    rows = [
        _row("b1", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),
        _row("s1", "2021-02-01", 0, "600000.SH", "sell", "score_delta_exit", 100, 12.0),
    ]
    prices = {"600000.SH": _prices([
        ("2021-01-04", 10.0, 10.5, 9.5, 10.2),
        ("2021-02-01", 12.0, 12.5, 11.5, 12.0),
        ("2021-02-02", 12.0, 12.5, 11.5, 12.0),
    ])}
    scores = pd.DataFrame({
        "trade_date": ["2021-01-04", "2021-02-01", "2021-01-05", "2021-02-01"],
        "instrument": ["600000.SH"] * 4,
        "score": [float("inf"), float("-inf"), float("nan"), 0.3],
    })
    ep = derive_episodes(rows, prices_by_symbol=prices, scores_frame=scores)[0]
    assert ep["entry_score"] is None                      # (2021-01-04, …) score was +inf
    assert ep["exit_score"] == pytest.approx(0.3)         # (2021-02-01, …) last finite = 0.3
    _assert_no_non_finite(ep)


def test_malformed_executions_dropped_and_fee_normalized():
    # P0.1 — NaN/±inf/zero qty or price drops the fill outright (it never opens
    # or closes an episode); NaN/missing fee reads as zero per the execution
    # contract (a bad fee must not poison an episode).
    rows = [
        _row("b0", "2021-01-04", 0, "600000.SH", "buy", "top_n_entry", 100, 10.0),            # valid open
        _row("b1", "2021-01-04", 1, "600000.SH", "buy", "top_n_entry", float("nan"), 10.0),   # bad qty → dropped
        _row("s1", "2021-01-08", 0, "600000.SH", "sell", "hard_stop", 100, float("inf")),     # bad price → dropped
        _row("s2", "2021-01-08", 1, "600000.SH", "sell", "hard_stop", 100, 11.0, fee=float("nan")),  # fee NaN → 0
        _row("b2", "2021-01-04", 0, "600001.SH", "buy", "top_n_entry", 0, 10.0),              # zero qty → dropped, no episode
    ]
    prices = {
        "600000.SH": _prices([("2021-01-04", 10.0, 10.5, 9.5, 10.2), ("2021-01-08", 11.0, 11.5, 10.5, 11.0)]),
        "600001.SH": _prices([("2021-01-04", 10.0, 10.5, 9.5, 10.2)]),
    }
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert [e["symbol"] for e in episodes] == ["600000.SH"]   # the zero-qty buy never opens
    ep = episodes[0]
    assert ep["episode_pnl"] == pytest.approx(1100.0 - 1000.0)  # NaN fee → 0
    _assert_no_non_finite(ep)


def test_pnl_concentration_downsampled_curve_preserves_tail():
    # P0.4 — the full curve is computed first; the rendered list is downsampled
    # to at most CURVE_CAP=500 points, but first/last are always kept and the
    # last cumulative always equals total_pnl.
    rows = []
    for i in range(600):
        sym = f"{600000 + i:06d}.SH"
        rows.append(_row(f"b{i}", "2021-01-04", 0, sym, "buy", "top_n_entry", 100, 10.0))
        rows.append(_row(f"s{i}", "2021-01-08", 0, sym, "sell", "winner_trailing", 100, 11.0))
    shared = _prices([("2021-01-04", 10.0, 10.5, 9.5, 10.2), ("2021-01-08", 11.0, 11.5, 10.5, 11.0)])
    prices = {f"{600000 + i:06d}.SH": shared for i in range(600)}
    pc = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices))["pnl_concentration"]
    assert pc["curve_points"] == 600
    curve = pc["cumulative_curve"]
    assert len(curve) == 500
    assert curve[0]["rank"] == 1
    assert curve[-1]["rank"] == 600
    assert curve[-1]["cumulative"] == pytest.approx(pc["total_pnl"])
    assert curve[-1]["cumulative"] == pytest.approx(600 * 100.0)


def test_mfe_distribution_buckets():
    # P2.1 — real MFE buckets 10-20% / 20-40% / 40-80% / 80%+, each with
    # count + median MFE / final return / giveback return / capture ratio / holding.
    rows = []
    prices = {}
    for i, (sym, high) in enumerate([
        ("600004.SH", 11.0),  # MFE 0.10 → 10-20%
        ("600003.SH", 12.0),  # MFE 0.20 → 20-40%
        ("600002.SH", 16.0),  # MFE 0.60 → 40-80%
        ("600001.SH", 20.0),  # MFE 1.00 → 80%+
    ]):
        exit_price = 10.0 + (high - 10.0) * 0.5
        rows.append(_row(f"b{i}", "2021-01-04", 0, sym, "buy", "top_n_entry", 100, 10.0))
        rows.append(_row(f"s{i}", "2021-01-08", 0, sym, "sell", "winner_trailing", 100, exit_price))
        prices[sym] = _prices([
            ("2021-01-04", 10.0, high, 5.0, 10.2),
            ("2021-01-08", exit_price, exit_price + 0.5, exit_price - 0.5, exit_price),
        ])
    calendar = _weekdays("2021-01-04", "2021-01-08")
    summary = summarize_episodes(derive_episodes(rows, prices_by_symbol=prices, calendar=calendar))
    dist = {b["bucket"]: b for b in summary["mfe_distribution"]}
    assert dist["10-20%"]["count"] == 1
    assert dist["20-40%"]["count"] == 1
    assert dist["40-80%"]["count"] == 1
    assert dist["80%+"]["count"] == 1
    assert dist["10-20%"]["median_mfe"] == pytest.approx(0.10)
    # exit at half the peak → capture 0.5, giveback_return = MFE − final.
    assert dist["40-80%"]["median_capture_ratio"] == pytest.approx(0.5)
    assert dist["40-80%"]["median_giveback_return"] == pytest.approx(0.60 - 0.30)
    assert dist["80%+"]["median_final_return"] == pytest.approx(0.50)
    assert dist["80%+"]["median_holding_days"] == pytest.approx(5)
