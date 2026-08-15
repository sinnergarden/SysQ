from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.backtest.posterior_policy import (
    PosteriorPolicyConfig,
    prepare_posterior_signal_views,
)
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


def test_top_n_must_be_positive(tmp_path: Path) -> None:
    dates = _dates(1)
    with pytest.raises(ValueError, match="top_n must be positive"):
        _run(tmp_path, dates, [{"A": 1.0}], top_n=0)
