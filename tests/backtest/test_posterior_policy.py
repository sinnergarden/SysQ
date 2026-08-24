from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.backtest.posterior_policy import (
    PosteriorPolicyConfig,
    PosteriorPolicyState,
    prepare_posterior_signal_views,
    run_posterior_policy_day,
)
from qsys.backtest.accounting import BacktestAccount, ValuationState
from qsys.backtest.strategy_runner import BacktestRunner
from qsys.signal.store import SignalStore


def _dates(count: int) -> list[str]:
    return pd.bdate_range("2026-06-01", periods=count).strftime("%Y-%m-%d").tolist()


def _save_signal(
    root: Path, dates: list[str], scores: list[dict[str, float]]
) -> None:
    rows = []
    for trade_date, day_scores in zip(dates, scores):
        data_date = (pd.Timestamp(trade_date) - pd.offsets.BDay(1)).strftime("%Y-%m-%d")
        for instrument, score in day_scores.items():
            rows.append(
                {
                    "trade_date": trade_date,
                    "data_date": data_date,
                    "instrument": instrument,
                    "signal_id": "posterior_sig",
                    "signal_run_id": "posterior_run",
                    "score": score,
                }
            )
    SignalStore(root).save_signal_run(
        "posterior_sig", "posterior_run", pd.DataFrame(rows), overwrite=True
    )


def _market(prices: dict[str, dict[str, dict[str, float]]], limit_down=None):
    limit_down = limit_down or set()

    def fetch(trade_date, instruments, price_col="close"):
        values = {
            instrument: float(
                prices.get(trade_date, {}).get(price_col, {}).get(instrument, 10.0)
            )
            for instrument in instruments
        }
        status = pd.DataFrame(
            {
                "is_suspended": 0,
                "is_limit_up": 0,
                "is_limit_down": [
                    (trade_date, instrument) in limit_down
                    for instrument in instruments
                ],
            },
            index=list(instruments),
        )
        return values, status

    return fetch


def _run(
    tmp_path: Path,
    dates: list[str],
    scores: list[dict[str, float]],
    *,
    prices=None,
    limit_down=None,
    output="posterior_bt",
    **kwargs,
):
    _save_signal(tmp_path, dates, scores)
    runner = BacktestRunner()
    params = {
        "signal_id": "posterior_sig",
        "signal_run_id": "posterior_run",
        "start_date": dates[0],
        "end_date": dates[-1],
        "research_root": str(tmp_path),
        "output_dir": tmp_path / output,
        "overwrite": True,
        "initial_capital": 100_000.0,
        "top_n": 2,
        "commission": 0.0,
        "stamp_duty": 0.0,
        "min_commission": 0.0,
        "slippage": 0.0,
        "rebalance_freq": "daily",
        "holding_policy": "posterior_confirmed",
        "score_delta_lookback": 1,
        "score_delta_history_days": 20,
        "score_delta_min_observations": 1000,
    }
    params.update(kwargs)
    price_fn = _market(prices or {}, limit_down=limit_down)
    with patch(
        "qsys.backtest.strategy_runner._resolve_trading_dates",
        lambda start, end: dates,
    ), patch("qsys.backtest.strategy_runner.fetch_market_snapshot", price_fn):
        return runner.run_from_signal_cache(**params)


def test_delta_threshold_is_strictly_prior() -> None:
    dates = _dates(3)
    frames = {
        dates[0]: pd.DataFrame({"instrument": ["A"], "score": [0.0]}),
        dates[1]: pd.DataFrame({"instrument": ["A"], "score": [1.0]}),
        dates[2]: pd.DataFrame({"instrument": ["A"], "score": [101.0]}),
    }
    config = PosteriorPolicyConfig(
        score_delta_lookback=1,
        score_delta_quantile=0.5,
        score_delta_history_days=2,
        score_delta_min_observations=1,
    )
    views = prepare_posterior_signal_views(
        frames, dates, score_column="score", config=config
    )
    assert views.deltas[dates[2]]["A"] == 100.0
    assert views.delta_thresholds[dates[2]] == 1.0
    assert views.delta_observations[dates[1]] == 0


def test_posterior_first_entry_missing_close_uses_prior_legal_stale_mark() -> None:
    trade_date = "2026-06-02"
    day_signal = pd.DataFrame({"instrument": ["A"], "score": [1.0]})
    views = prepare_posterior_signal_views(
        {trade_date: day_signal}, [trade_date], score_column="score",
        config=PosteriorPolicyConfig(),
    )

    class _Market:
        def latest_legal_close_before(self, date, instruments):
            assert date == trade_date and instruments == ["A"]
            return {"A": {"price": 9.0, "price_date": "2026-06-01"}}

        def snapshot(self, date, instruments, price_col="open"):
            return {"A": 10.0}, _status_frame(instruments)

        def observed_close(self, date, instruments):
            return {}

    def _status_frame(instruments):
        return pd.DataFrame(
            {"is_suspended": False, "is_limit_up": False, "is_limit_down": False},
            index=instruments,
        )

    account = BacktestAccount(10_000)
    account.start_day(trade_date)
    valuation = ValuationState()
    result, _, _ = run_posterior_policy_day(
        account=account,
        state=PosteriorPolicyState(),
        config=PosteriorPolicyConfig(),
        views=views,
        day_signal=day_signal,
        trade_date=trade_date,
        trading_index=0,
        is_rebalance=True,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        execution_price_mode="open",
        market_snapshot_fn=None,
        market_data=_Market(),
        valuation_state=valuation,
    )
    mark = valuation.mark_to_market(account, trade_date).iloc[0]
    assert result["filled_count"] == 1
    assert result["market_value_after"] == pytest.approx(9_000.0)
    assert mark["last_price"] == pytest.approx(9.0)
    assert bool(mark["stale_price"])


@pytest.mark.parametrize(
    "held_open,held_close", [(10.0, 10.0), (100.0, 1_000.0)]
)
def test_same_day_open_and_close_do_not_change_prior_equity_sizing(
    held_open, held_close
) -> None:
    prior_day, trade_date = "2026-06-01", "2026-06-02"
    signal = pd.DataFrame(
        {"instrument": ["A", "B"], "score": [2.0, 1.0]}
    )
    config = PosteriorPolicyConfig()
    views = prepare_posterior_signal_views(
        {trade_date: signal}, [trade_date], score_column="score", config=config
    )
    account = BacktestAccount(2_000.0)
    account.start_day(prior_day)
    account.update_after_deal("B", 100, 10.0, 0.0, "buy")
    account.start_day(trade_date)
    valuation = ValuationState()
    valuation.update({"B": 10.0}, prior_day)
    state = PosteriorPolicyState(
        entry_index={"B": 0}, previous_close={"B": 10.0},
        peak_close={"B": 10.0},
    )

    class _Market:
        def latest_legal_close_before(self, date, instruments):
            assert instruments == ["A"]
            return {"A": {"price": 10.0, "price_date": prior_day}}

        def snapshot(self, date, instruments, price_col="open"):
            return {"A": 10.0, "B": held_open}, pd.DataFrame(
                {"is_suspended": False, "is_limit_up": False, "is_limit_down": False},
                index=instruments,
            )

        def observed_close(self, date, instruments):
            return {"A": 10.0, "B": held_close}

    result, _, orders = run_posterior_policy_day(
        account=account,
        state=state,
        config=config,
        views=views,
        day_signal=signal,
        trade_date=trade_date,
        trading_index=1,
        is_rebalance=True,
        top_n=2,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        execution_price_mode="open",
        market_snapshot_fn=None,
        market_data=_Market(),
        valuation_state=valuation,
    )
    a_buys = [order for order in orders if order["symbol"] == "A"]
    assert a_buys[0]["amount"] == 100
    assert not [order for order in orders if order["symbol"] == "B"]
    assert result["total_value_before"] == pytest.approx(2_000.0)


def test_non_rebalance_requests_market_data_for_holdings_only() -> None:
    prior_day, trade_date = "2026-06-01", "2026-06-02"
    signal = pd.DataFrame(
        {"instrument": ["B", "A"], "score": [2.0, 1.0]}
    )
    config = PosteriorPolicyConfig()
    views = prepare_posterior_signal_views(
        {trade_date: signal}, [trade_date], score_column="score", config=config
    )
    account = BacktestAccount(2_000.0)
    account.start_day(prior_day)
    account.update_after_deal("A", 100, 10.0, 0.0, "buy")
    account.start_day(trade_date)
    valuation = ValuationState()
    valuation.update({"A": 10.0}, prior_day)

    class _HeldOnlyMarket:
        def latest_legal_close_before(self, date, instruments):
            raise AssertionError("non-rebalance candidates must not be seeded")

        def snapshot(self, date, instruments, price_col="open"):
            assert instruments == ["A"]
            return {"A": 10.0}, pd.DataFrame(
                {"is_suspended": False, "is_limit_up": False, "is_limit_down": False},
                index=instruments,
            )

        def observed_close(self, date, instruments):
            assert instruments == ["A"]
            return {"A": 10.0}

    result, _, orders = run_posterior_policy_day(
        account=account,
        state=PosteriorPolicyState(
            entry_index={"A": 0}, previous_close={"A": 10.0},
            peak_close={"A": 10.0},
        ),
        config=config,
        views=views,
        day_signal=signal,
        trade_date=trade_date,
        trading_index=1,
        is_rebalance=False,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        execution_price_mode="open",
        market_snapshot_fn=None,
        market_data=_HeldOnlyMarket(),
        valuation_state=valuation,
    )
    assert result["position_count"] == 1
    assert orders == []


def test_posterior_zero_sellable_exit_is_rejected_and_recorded() -> None:
    trade_date = "2026-06-02"
    account = BacktestAccount(1_000.0)
    account.start_day(trade_date)
    account.update_after_deal("A", 100, 10.0, 0.0, "buy")
    assert account.positions["A"].sellable_amount == 0
    valuation = ValuationState()
    valuation.update({"A": 10.0}, trade_date)
    signal = pd.DataFrame({"instrument": ["B"], "score": [2.0]})
    config = PosteriorPolicyConfig(
        rank_exit=True,
        posterior_stop_loss=0.999,
        winner_activation_return=0.999,
        winner_trailing_stop=0.999,
    )
    views = prepare_posterior_signal_views(
        {trade_date: signal}, [trade_date], score_column="score",
        config=config,
    )

    class _Market:
        def latest_legal_close_before(self, date, instruments):
            assert date == trade_date and instruments == ["B"]
            return {"B": {"price": 10.0, "price_date": "2026-06-01"}}

        def snapshot(self, date, instruments, price_col="open"):
            return {instrument: 10.0 for instrument in instruments}, pd.DataFrame(
                {
                    "is_suspended": False,
                    "is_limit_up": False,
                    "is_limit_down": False,
                },
                index=instruments,
            )

        def observed_close(self, date, instruments):
            return {instrument: 10.0 for instrument in instruments}

    collector: list[dict] = []
    result, _, orders = run_posterior_policy_day(
        account=account,
        state=PosteriorPolicyState(
            previous_close={"A": 10.0}, peak_close={"A": 10.0},
        ),
        config=config,
        views=views,
        day_signal=signal,
        trade_date=trade_date,
        trading_index=1,
        is_rebalance=True,
        top_n=1,
        commission=0.0,
        stamp_duty=0.0,
        min_commission=0.0,
        slippage=0.0,
        execution_price_mode="open",
        market_snapshot_fn=None,
        execution_collector=collector,
        market_data=_Market(),
        valuation_state=valuation,
    )
    assert [order for order in orders if order["symbol"] == "A"][0]["amount"] == 0
    assert result["rejected_count"] == 1
    assert result["filled_count"] == 0
    assert result["policy_exit_count"] == 0
    assert account.positions["A"].total_amount == 100
    assert collector[0]["status"] == "rejected"
    assert "T+1" in collector[0]["rejection_reason"]
    assert "zero sellable" in collector[0]["rejection_reason"]


def test_initial_entry_is_equal_weight_and_rank_drop_does_not_sell(tmp_path: Path) -> None:
    dates = _dates(2)
    result = _run(
        tmp_path,
        dates,
        [{"A": 3.0, "B": 2.0, "C": 1.0}, {"C": 3.0, "B": 2.0, "A": 1.0}],
        artifact_mode="debug",
    )
    assert result.daily_summary[0]["buy_count"] == 2
    assert result.daily_summary[0]["position_count"] == 2
    assert result.daily_summary[1]["sell_count"] == 0
    assert result.daily_summary[1]["policy_exit_count"] == 0
    assert result.daily_summary[1]["order_count"] == 0
    targets = pd.read_csv(
        tmp_path / "posterior_bt" / "daily" / dates[0] / "target_weights.csv"
    )
    assert sorted(targets["target_weight"].tolist()) == [0.5, 0.5]


def test_hard_stop_uses_previous_close_not_same_day_close(tmp_path: Path) -> None:
    dates = _dates(3)
    prices = {
        dates[0]: {"open": {"A": 10}, "close": {"A": 10}},
        dates[1]: {"open": {"A": 10}, "close": {"A": 8.5}},
        dates[2]: {"open": {"A": 8.5}, "close": {"A": 8.5}},
    }
    result = _run(
        tmp_path,
        dates,
        [{"A": 2.0}, {"A": 2.0}, {"A": 2.0}],
        prices=prices,
        top_n=1,
    )
    assert result.daily_summary[1]["hard_stop_exit_count"] == 0
    assert result.daily_summary[2]["hard_stop_exit_count"] == 1
    assert result.daily_summary[2]["position_count"] == 0


def test_score_delta_p10_exit(tmp_path: Path) -> None:
    dates = _dates(3)
    result = _run(
        tmp_path,
        dates,
        [
            {"A": 10.0, "B": 0.0},
            {"A": 11.0, "B": 1.0},
            {"A": 0.0, "B": 2.0},
        ],
        top_n=1,
        score_delta_min_observations=1,
        score_delta_quantile=0.10,
    )
    assert result.daily_summary[2]["score_delta_threshold"] == 1.0
    assert result.daily_summary[2]["score_delta_exit_count"] == 1


def test_rank_exit_sells_dropouts_and_refills_from_top_n(tmp_path: Path) -> None:
    dates = _dates(2)
    result = _run(
        tmp_path,
        dates,
        [
            {"A": 3.0, "B": 2.0, "C": 1.0},
            {"C": 3.0, "B": 2.0, "A": 1.0},
        ],
        top_n=2,
        rank_exit=True,
        # Disable all four exit rules so rank_exit is the only exit path.
        posterior_stop_loss=0.999,
        score_delta_min_observations=10**9,
        winner_activation_return=0.9999,
        winner_trailing_stop=0.999,
        stale_after_days=10000,
        replacement_rank_gap=10**6,
    )
    assert result.daily_summary[0]["buy_count"] == 2  # A, B equal-weight
    assert result.daily_summary[1]["sell_count"] == 1  # A dropped out of top2
    assert result.daily_summary[1]["rank_exit_exit_count"] == 1
    assert result.daily_summary[1]["hard_stop_exit_count"] == 0
    assert result.daily_summary[1]["score_delta_exit_count"] == 0
    assert result.daily_summary[1]["winner_trailing_exit_count"] == 0
    assert result.daily_summary[1]["stale_replacement_exit_count"] == 0
    assert result.daily_summary[1]["buy_count"] == 1  # refill with C
    assert result.daily_summary[1]["position_count"] == 2


def test_winner_must_activate_before_trailing_exit(tmp_path: Path) -> None:
    dates = _dates(3)
    prices = {
        dates[0]: {"open": {"A": 10}, "close": {"A": 12.5}},
        dates[1]: {"open": {"A": 12.5}, "close": {"A": 10.5}},
        dates[2]: {"open": {"A": 10.5}, "close": {"A": 10.5}},
    }
    result = _run(
        tmp_path,
        dates,
        [{"A": 2.0}] * 3,
        prices=prices,
        top_n=1,
    )
    assert result.daily_summary[1]["winner_trailing_exit_count"] == 0
    assert result.daily_summary[2]["winner_trailing_exit_count"] == 1


def test_rejected_exit_keeps_state_and_does_not_overfill(tmp_path: Path) -> None:
    dates = _dates(3)
    prices = {
        dates[0]: {"open": {"A": 10}, "close": {"A": 10}},
        dates[1]: {"open": {"A": 10}, "close": {"A": 8.5}},
        dates[2]: {
            "open": {"A": 8.5, "B": 10},
            "close": {"A": 8.5, "B": 10},
        },
    }
    result = _run(
        tmp_path,
        dates,
        [{"A": 2.0, "B": 1.0}] * 2 + [{"B": 2.0, "A": 1.0}],
        prices=prices,
        limit_down={(dates[2], "A")},
        top_n=1,
    )
    day = result.daily_summary[2]
    assert day["rejected_count"] == 1
    assert day["policy_exit_count"] == 0
    assert day["policy_entry_count"] == 0
    assert day["position_count"] == 1


def test_stale_replacement_requires_rank_gap(tmp_path: Path) -> None:
    dates = _dates(2)
    instruments = [f"S{i:02d}" for i in range(22)]
    first = {instrument: float(22 - i) for i, instrument in enumerate(instruments)}
    second_order = instruments[1:] + instruments[:1]
    second = {instrument: float(22 - i) for i, instrument in enumerate(second_order)}
    result = _run(
        tmp_path,
        dates,
        [first, second],
        top_n=1,
        stale_after_days=1,
        replacement_rank_gap=20,
    )
    assert result.daily_summary[1]["stale_replacement_exit_count"] == 1
    assert result.daily_summary[1]["policy_entry_count"] == 1
    assert result.daily_summary[1]["position_count"] == 1


def test_policy_parameters_are_hashed_and_manifested(tmp_path: Path) -> None:
    dates = _dates(2)
    scores = [{"A": 2.0, "B": 1.0}] * 2
    first = _run(tmp_path, dates, scores, output="first", score_delta_quantile=0.10)
    second = _run(tmp_path, dates, scores, output="second", score_delta_quantile=0.20)
    assert first.backtest_id != second.backtest_id
    manifest = json.loads((tmp_path / "second" / "manifest.json").read_text())
    assert manifest["holding_policy"] == "posterior_confirmed"
    assert manifest["posterior_policy"]["score_delta_quantile"] == 0.20
    assert manifest["posterior_policy_contract"]["price_decision"] == "previous_completed_close"
    executions = pd.read_csv(tmp_path / "second" / "executions.csv")
    assert set(executions["execution_phase"]) == {"entry"}
    assert set(executions["trade_reason"]) == {"top_n_entry"}


def test_top_n_must_be_positive(tmp_path: Path) -> None:
    dates = _dates(1)
    with pytest.raises(ValueError, match="top_n must be positive"):
        _run(tmp_path, dates, [{"A": 1.0}], top_n=0)
