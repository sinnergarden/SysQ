"""Posterior-confirmed holding policy for cached-signal research backtests.

The policy deliberately separates stock selection from position exits:
absolute score selects new names, while realised P&L and point-in-time score
delta manage existing positions.  All decisions for an execution-date open
use only signals certified for that execution date and the previous completed
session's close.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from qsys.backtest._execution import execute_trade_day
from qsys.ops.plan_builder import build_order_intents
from qsys.trader.account import Account


@dataclass(frozen=True)
class PosteriorPolicyConfig:
    score_delta_lookback: int = 20
    score_delta_quantile: float = 0.10
    score_delta_history_days: int = 504
    score_delta_min_observations: int = 500
    posterior_stop_loss: float = 0.09
    winner_activation_return: float = 0.20
    winner_trailing_stop: float = 0.125
    stale_after_days: int = 20
    stale_max_return: float = 0.03
    replacement_rank_gap: int = 20

    def validate(self) -> None:
        if self.score_delta_lookback < 1:
            raise ValueError("score_delta_lookback must be positive")
        if not 0.0 < self.score_delta_quantile < 1.0:
            raise ValueError("score_delta_quantile must be within (0, 1)")
        if self.score_delta_history_days < 1:
            raise ValueError("score_delta_history_days must be positive")
        if self.score_delta_min_observations < 1:
            raise ValueError("score_delta_min_observations must be positive")
        for name, value in (
            ("posterior_stop_loss", self.posterior_stop_loss),
            ("winner_activation_return", self.winner_activation_return),
            ("winner_trailing_stop", self.winner_trailing_stop),
        ):
            if not 0.0 < value < 1.0:
                raise ValueError(f"{name} must be within (0, 1)")
        if self.stale_after_days < 1:
            raise ValueError("stale_after_days must be positive")
        if self.replacement_rank_gap < 1:
            raise ValueError("replacement_rank_gap must be positive")

    def to_manifest(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PosteriorPolicyState:
    entry_index: dict[str, int] = field(default_factory=dict)
    previous_close: dict[str, float] = field(default_factory=dict)
    peak_close: dict[str, float] = field(default_factory=dict)
    winner_active: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class PosteriorSignalViews:
    scores: dict[str, dict[str, float]]
    ranks: dict[str, dict[str, int]]
    deltas: dict[str, dict[str, float]]
    delta_thresholds: dict[str, float | None]
    delta_observations: dict[str, int]


def prepare_posterior_signal_views(
    signal_by_date: dict[str, pd.DataFrame],
    trading_dates: list[str],
    *,
    score_column: str,
    config: PosteriorPolicyConfig,
) -> PosteriorSignalViews:
    """Build deterministic score/delta views with strictly prior thresholds."""
    config.validate()
    scores: dict[str, dict[str, float]] = {}
    ranks: dict[str, dict[str, int]] = {}
    deltas: dict[str, dict[str, float]] = {}

    for trade_date in trading_dates:
        frame = signal_by_date.get(trade_date, pd.DataFrame())
        if frame.empty:
            scores[trade_date] = {}
            ranks[trade_date] = {}
            continue
        usable = frame[["instrument", score_column]].copy()
        usable["instrument"] = usable["instrument"].astype(str)
        usable[score_column] = pd.to_numeric(usable[score_column], errors="coerce")
        usable = usable.dropna(subset=[score_column]).sort_values(
            [score_column, "instrument"],
            ascending=[False, True],
            kind="mergesort",
        )
        # Duplicate instrument rows make both rank and delta ambiguous.
        if usable["instrument"].duplicated().any():
            raise ValueError(f"duplicate signal instruments for {trade_date}")
        ordered = usable.set_index("instrument")[score_column]
        scores[trade_date] = {str(k): float(v) for k, v in ordered.items()}
        ranks[trade_date] = {
            str(instrument): rank
            for rank, instrument in enumerate(ordered.index, start=1)
        }

    lookback = config.score_delta_lookback
    for index, trade_date in enumerate(trading_dates):
        if index < lookback:
            deltas[trade_date] = {}
            continue
        current = scores[trade_date]
        previous = scores[trading_dates[index - lookback]]
        common = sorted(set(current).intersection(previous))
        deltas[trade_date] = {
            instrument: current[instrument] - previous[instrument]
            for instrument in common
        }

    thresholds: dict[str, float | None] = {}
    observations: dict[str, int] = {}
    for index, trade_date in enumerate(trading_dates):
        # End at index-1: the current day's deltas never influence their own
        # threshold.  This is the core PIT contract for the exit rule.
        start = max(0, index - config.score_delta_history_days)
        prior_arrays = [
            np.fromiter(deltas[trading_dates[j]].values(), dtype=float)
            for j in range(start, index)
            if deltas[trading_dates[j]]
        ]
        pooled = np.concatenate(prior_arrays) if prior_arrays else np.array([], dtype=float)
        pooled = pooled[np.isfinite(pooled)]
        observations[trade_date] = int(pooled.size)
        thresholds[trade_date] = (
            float(np.quantile(pooled, config.score_delta_quantile))
            if pooled.size >= config.score_delta_min_observations
            else None
        )

    return PosteriorSignalViews(
        scores=scores,
        ranks=ranks,
        deltas=deltas,
        delta_thresholds=thresholds,
        delta_observations=observations,
    )


def _current_weight_targets(
    account: Account,
    exec_prices: dict[str, float],
    keep: set[str],
) -> dict[str, float]:
    total_equity = float(account.get_total_equity(exec_prices))
    if total_equity <= 0:
        return {instrument: 0.0 for instrument in keep}
    return {
        instrument: (
            account.positions[instrument].total_amount
            * float(exec_prices.get(instrument, 0.0) or 0.0)
            / total_equity
        )
        for instrument in sorted(keep)
        if instrument in account.positions
    }


def run_posterior_policy_day(
    *,
    account: Account,
    state: PosteriorPolicyState,
    config: PosteriorPolicyConfig,
    views: PosteriorSignalViews,
    day_signal: pd.DataFrame,
    trade_date: str,
    trading_index: int,
    is_rebalance: bool,
    top_n: int,
    commission: float,
    stamp_duty: float,
    min_commission: float,
    slippage: float,
    execution_price_mode: str,
    market_snapshot_fn: Any,
    execution_collector: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], pd.DataFrame, list[dict[str, Any]]]:
    """Advance the posterior policy by one execution date."""
    if day_signal.empty and "instrument" not in day_signal.columns:
        day_signal = pd.DataFrame(columns=["instrument", "score"])
    score_map = views.scores.get(trade_date, {})
    rank_map = views.ranks.get(trade_date, {})
    delta_map = views.deltas.get(trade_date, {})
    delta_threshold = views.delta_thresholds.get(trade_date)

    ranked = sorted(score_map, key=lambda x: (rank_map[x], x))
    top_candidates = ranked[:top_n]
    instruments = sorted(set(account.positions).union(top_candidates))
    if not instruments:
        result = {
            "trade_date": trade_date,
            "execution_price_mode": execution_price_mode,
            "cash_before": float(account.cash),
            "market_value_before": 0.0,
            "total_value_before": float(account.cash),
            "cash_after": float(account.cash),
            "market_value_after": 0.0,
            "total_value_after": float(account.cash),
            "order_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "filled_count": 0,
            "rejected_count": 0,
            "turnover": 0.0,
            "position_count": 0,
            "status": "no_signal_data",
        }
        _add_policy_audit(result, {}, set(), delta_threshold, views, trade_date)
        return result, pd.DataFrame(), []

    if execution_price_mode != "open":
        raise ValueError("posterior_confirmed requires open execution")
    exec_prices, market_status = market_snapshot_fn(
        trade_date, instruments, price_col="open"
    )
    mtm_prices, _ = market_snapshot_fn(
        trade_date, instruments, price_col="close"
    )

    exit_reasons: dict[str, str] = {}
    for instrument, position in sorted(account.positions.items()):
        previous_close = state.previous_close.get(instrument)
        if previous_close is None or position.avg_cost <= 0:
            continue
        pnl = previous_close / position.avg_cost - 1.0
        if pnl <= -config.posterior_stop_loss:
            exit_reasons[instrument] = "hard_stop"
            continue
        peak = state.peak_close.get(instrument, previous_close)
        if (
            instrument in state.winner_active
            and peak > 0
            and previous_close / peak - 1.0 <= -config.winner_trailing_stop
        ):
            exit_reasons[instrument] = "winner_trailing"
            continue
        delta = delta_map.get(instrument)
        if (
            delta_threshold is not None
            and delta is not None
            and np.isfinite(delta)
            and delta < delta_threshold
        ):
            exit_reasons[instrument] = "score_delta"

    reserved_replacements: list[tuple[str, str]] = []
    if is_rebalance and score_map:
        available = [
            instrument for instrument in top_candidates
            if instrument not in account.positions
        ]
        stale_positions = []
        for instrument, position in sorted(account.positions.items()):
            if instrument in exit_reasons:
                continue
            previous_close = state.previous_close.get(instrument)
            entered = state.entry_index.get(instrument)
            held_days = trading_index - entered if entered is not None else -1
            if previous_close is None or position.avg_cost <= 0:
                continue
            pnl = previous_close / position.avg_cost - 1.0
            if held_days >= config.stale_after_days and pnl <= config.stale_max_return:
                stale_positions.append(instrument)
        stale_positions.sort(key=lambda x: (-rank_map.get(x, 10**9), x))
        for held in stale_positions:
            if not available:
                break
            candidate = available[0]
            held_rank = rank_map.get(held)
            candidate_rank = rank_map.get(candidate)
            if (
                held_rank is None
                or candidate_rank is None
                or held_rank - candidate_rank < config.replacement_rank_gap
            ):
                continue
            exit_reasons[held] = "stale_replacement"
            reserved_replacements.append((held, candidate))
            available.pop(0)

    keep = set(account.positions).difference(exit_reasons)
    sell_targets = _current_weight_targets(account, exec_prices, keep)
    for instrument in exit_reasons:
        sell_targets[instrument] = 0.0

    sell_orders, _, _, _, _, _ = build_order_intents(
        account, day_signal, sell_targets, exec_prices, trade_date
    )
    sell_orders = [order for order in sell_orders if order["side"] == "sell"]
    for order in sell_orders:
        order["execution_phase"] = "exit"
        order["trade_reason"] = exit_reasons.get(
            str(order.get("symbol") or ""), "posterior_exit"
        )
    before = set(account.positions)
    sell_result = execute_trade_day(
        account,
        sell_orders,
        exec_prices,
        market_status,
        mtm_prices,
        trade_date,
        commission=commission,
        stamp_duty=stamp_duty,
        min_commission=min_commission,
        slippage=slippage,
        execution_price_mode=execution_price_mode,
        execution_collector=execution_collector,
    )
    after_sells = set(account.positions)
    actual_exits = before.difference(after_sells)

    entries: list[str] = []
    buy_orders: list[dict[str, Any]] = []
    buy_targets = _current_weight_targets(account, exec_prices, after_sells)
    if is_rebalance:
        desired_slots = max(top_n - len(after_sells), 0)
        realised_replacements = [
            candidate
            for held, candidate in reserved_replacements
            if held in actual_exits
        ]
        candidates = realised_replacements + [
            instrument for instrument in top_candidates
            if instrument not in after_sells
            and instrument not in realised_replacements
            and instrument not in actual_exits
        ]
        if desired_slots > 0:
            for instrument in candidates:
                if instrument in entries:
                    continue
                entries.append(instrument)
                if len(entries) >= desired_slots:
                    break
        for instrument in entries:
            buy_targets[instrument] = 1.0 / top_n
        generated, _, _, _, _, _ = build_order_intents(
            account, day_signal, buy_targets, exec_prices, trade_date
        )
        buy_orders = [order for order in generated if order["side"] == "buy"]
        replacement_entries = {candidate for _, candidate in reserved_replacements}
        for order in buy_orders:
            instrument = str(order.get("symbol") or "")
            order["execution_phase"] = "entry"
            order["trade_reason"] = (
                "stale_replacement_entry"
                if instrument in replacement_entries
                else "top_n_entry"
            )

    buy_result = execute_trade_day(
        account,
        buy_orders,
        exec_prices,
        market_status,
        mtm_prices,
        trade_date,
        commission=commission,
        stamp_duty=stamp_duty,
        min_commission=min_commission,
        slippage=slippage,
        execution_price_mode=execution_price_mode,
        execution_collector=execution_collector,
    )
    after = set(account.positions)
    actual_entries = after.difference(after_sells)
    result = _merge_execution_results(sell_result, buy_result)

    for instrument in actual_exits:
        state.entry_index.pop(instrument, None)
        state.previous_close.pop(instrument, None)
        state.peak_close.pop(instrument, None)
        state.winner_active.discard(instrument)
    for instrument in actual_entries:
        state.entry_index[instrument] = trading_index

    # Current close becomes visible only after today's open decisions and is
    # stored exclusively for the next trading day's decision.
    for instrument in after:
        close = float(mtm_prices.get(instrument, 0.0) or 0.0)
        if close <= 0:
            continue
        state.previous_close[instrument] = close
        state.peak_close[instrument] = max(state.peak_close.get(instrument, close), close)
        position = account.positions[instrument]
        if position.avg_cost > 0 and state.peak_close[instrument] / position.avg_cost - 1.0 >= config.winner_activation_return:
            state.winner_active.add(instrument)

    _add_policy_audit(
        result, exit_reasons, actual_exits, delta_threshold, views, trade_date,
        actual_entries=actual_entries,
    )
    target_weights = dict(sell_targets)
    target_weights.update(buy_targets)
    return result, pd.DataFrame(
        {
            "instrument": list(target_weights),
            "target_weight": list(target_weights.values()),
            "trade_date": trade_date,
        }
    ), sell_orders + buy_orders


def _merge_execution_results(
    sell_result: dict[str, Any], buy_result: dict[str, Any]
) -> dict[str, Any]:
    result = dict(sell_result)
    for key in (
        "cash_after", "market_value_after", "total_value_after",
        "position_count", "status",
    ):
        result[key] = buy_result[key]
    for key in (
        "order_count", "buy_count", "sell_count", "filled_count",
        "rejected_count", "turnover",
    ):
        result[key] = sell_result.get(key, 0) + buy_result.get(key, 0)
    return result


def _add_policy_audit(
    result: dict[str, Any],
    exit_reasons: dict[str, str],
    actual_exits: set[str],
    delta_threshold: float | None,
    views: PosteriorSignalViews,
    trade_date: str,
    *,
    actual_entries: set[str] | None = None,
) -> None:
    actual_entries = actual_entries or set()
    result.update(
        {
            "holding_policy": "posterior_confirmed",
            "policy_exit_count": len(actual_exits),
            "policy_entry_count": len(actual_entries),
            "hard_stop_exit_count": sum(exit_reasons.get(x) == "hard_stop" for x in actual_exits),
            "score_delta_exit_count": sum(exit_reasons.get(x) == "score_delta" for x in actual_exits),
            "winner_trailing_exit_count": sum(exit_reasons.get(x) == "winner_trailing" for x in actual_exits),
            "stale_replacement_exit_count": sum(exit_reasons.get(x) == "stale_replacement" for x in actual_exits),
            "score_delta_threshold": delta_threshold,
            "score_delta_observation_count": views.delta_observations.get(trade_date, 0),
        }
    )
