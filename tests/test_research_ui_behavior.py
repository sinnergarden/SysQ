from __future__ import annotations

import datetime

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
    episodes = derive_episodes(rows, prices_by_symbol=prices)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["symbol"] == "600000.SH"
    assert ep["entry_date"] == "2021-01-04"
    assert ep["exit_date"] == "2021-02-01"
    assert ep["exit_reason"] == "hard_stop"
    assert ep["holding_days"] == 2  # 01-04, 02-01
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
