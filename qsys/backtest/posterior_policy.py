"""Posterior-confirmed holding policy for cached-signal research backtests.

The policy deliberately separates stock selection from position exits:
absolute score selects new names, while realised P&L and point-in-time score
delta manage existing positions.  All decisions for an execution-date open
use only signals certified for that execution date and the previous completed
session's close.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

import numpy as np
import pandas as pd

from qsys.backtest._execution import execute_trade_day
from qsys.ops.plan_builder import build_order_intents
from qsys.trader.account import Account
from qsys.backtest.accounting import BacktestAccount, ValuationState


class _ValuationPlanningAccount:
    """Planning-only account view with valuation equity and open prices.

    ``build_order_intents`` accepts an Account-shaped object and uses the
    supplied prices for executable quantities.  Its equity calculation must
    nevertheless use the last legal close, not today's open.  This proxy
    keeps that distinction local to planning and never mutates the account.
    """

    def __init__(self, account: Account, valuation_prices: Mapping[str, float]):
        self._account = account
        self.positions = account.positions
        self.cash = account.cash
        self.frozen_cash = getattr(account, "frozen_cash", 0.0)
        self._valuation_prices = dict(valuation_prices)

    def get_total_equity(self, _prices: Mapping[str, float]) -> float:
        return float(self._account.get_total_equity(self._valuation_prices))

    def get_market_value(self, _prices: Mapping[str, float]) -> float:
        return float(self._account.get_market_value(self._valuation_prices))


def build_valuation_execution_orders(
    account: Account,
    target_weights: Mapping[str, float],
    valuation_prices: Mapping[str, float],
    execution_prices: Mapping[str, float],
    *,
    total_equity: float | None = None,
) -> list[dict[str, Any]]:
    """Create lots with prior-close valuation and current-open execution.

    Target equity and every held position's current value use the last legal
    pre-open valuation.  Only the conversion from a dollar difference to
    requested shares uses today's legal execution price.  This prevents an
    open gap in a held name from changing total equity or creating a spurious
    keep-position rebalance.
    """
    planning_equity = (
        float(account.get_total_equity(valuation_prices))
        if total_equity is None else float(total_equity)
    )
    orders: list[dict[str, Any]] = []
    for instrument in sorted(set(account.positions) | set(target_weights)):
        execution_price = float(execution_prices.get(instrument, 0.0) or 0.0)
        if not np.isfinite(execution_price) or execution_price <= 0:
            continue
        position = account.positions.get(instrument)
        quantity = int(position.total_amount) if position is not None else 0
        if position is not None:
            valuation_price = float(valuation_prices.get(instrument, 0.0) or 0.0)
            if not np.isfinite(valuation_price) or valuation_price <= 0:
                raise ValueError(
                    f"missing prior legal valuation for held instrument {instrument}"
                )
            current_value = quantity * valuation_price
        else:
            current_value = 0.0
        target_value = planning_equity * float(target_weights.get(instrument, 0.0))
        raw_amount = (target_value - current_value) / execution_price
        amount = abs(int(raw_amount / 100) * 100)
        if amount <= 0:
            continue
        orders.append({
            "symbol": instrument,
            "amount": amount,
            "side": "buy" if raw_amount > 0 else "sell",
            "price": execution_price,
            "order_type": "market",
        })
    orders.sort(key=lambda order: 0 if order["side"] == "sell" else 1)
    return orders


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
    rank_exit: bool = False
    # Rank-hysteresis band: when rank_exit is enabled AND rank_exit_hold_top is
    # set (>= top_n), a held name is NOT sold while its current rank is
    # <= rank_exit_hold_top (wider band than the entry top_n).  It exits only
    # when it falls to rank > rank_exit_hold_top; slots are refilled from the
    # current top_n at equal weight.  Keep semantics are otherwise identical to
    # plain rank_exit (hold drift, no reweight).  None = plain top_n dropout.
    rank_exit_hold_top: int | None = None
    # Exposure gate: when active (per the precomputed PIT schedule passed to
    # run_posterior_policy_day), target weights are scaled to
    # exposure_gate_scale (e.g. 0.5 = 50% of equity invested).  The schedule
    # itself is data, not config; the mode string is recorded for provenance.
    exposure_gate_mode: str = "none"  # none | market_risk | model_health | either
    exposure_gate_scale: float = 0.5

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
        if self.rank_exit_hold_top is not None and self.rank_exit_hold_top < 1:
            raise ValueError("rank_exit_hold_top must be positive when set")
        if self.exposure_gate_mode not in {"none", "market_risk", "model_health", "either"}:
            raise ValueError(
                f"exposure_gate_mode must be one of "
                "{none, market_risk, model_health, either}, got "
                f"{self.exposure_gate_mode!r}"
            )
        if not 0.0 < self.exposure_gate_scale <= 1.0:
            raise ValueError(
                f"exposure_gate_scale must be within (0, 1], got "
                f"{self.exposure_gate_scale}"
            )

    def to_manifest(self) -> dict[str, Any]:
        manifest = asdict(self)
        # rank_exit=False is the behaviourally identical pre-existing config;
        # excluding the default keeps already-published backtest hashes stable.
        if not self.rank_exit:
            manifest.pop("rank_exit", None)
        # Same for the hysteresis band: None is the pre-existing plain rank_exit
        # behaviour, excluded so prior rank_exit hashes stay stable.
        if self.rank_exit_hold_top is None:
            manifest.pop("rank_exit_hold_top", None)
        # Same for the exposure gate: "none" is the pre-existing behaviour.
        if self.exposure_gate_mode == "none":
            manifest.pop("exposure_gate_mode", None)
            manifest.pop("exposure_gate_scale", None)
        return manifest


@dataclass
class PosteriorPolicyState:
    entry_index: dict[str, int] = field(default_factory=dict)
    previous_close: dict[str, float] = field(default_factory=dict)
    peak_close: dict[str, float] = field(default_factory=dict)
    # Gross cash entitlements accumulated since entry, expressed per current
    # share.  Adding this to raw close keeps hard-stop/winner references on a
    # total-return basis across ex-dividend dates without changing thresholds.
    cumulative_cash_per_current_share: dict[str, float] = field(
        default_factory=dict
    )
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
    valuation_prices: Mapping[str, float] | None = None,
    total_equity: float | None = None,
) -> dict[str, float]:
    mark_prices = dict(valuation_prices or exec_prices)
    equity = (
        float(account.get_total_equity(mark_prices))
        if total_equity is None else float(total_equity)
    )
    if equity <= 0:
        return {instrument: 0.0 for instrument in keep}
    return {
        instrument: (
            account.positions[instrument].total_amount
            * float(mark_prices.get(instrument, 0.0) or 0.0)
            / equity
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
    exposure_gate_schedule: dict[str, bool] | None = None,
    market_data: Any = None,
    valuation_state: ValuationState | None = None,
    adv_by_instrument: Mapping[str, float] | None = None,
    max_participation_rate: float | None = None,
    liquidity_gate_mode: str = "warning",
    adv_window: int = 20,
    adv_min_periods: int = 5,
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
    # New names can enter only on a rebalance date.  On intervening sessions
    # request market data solely for live holdings; ranking/exit diagnostics
    # still use the full cached signal views without loading candidate prices.
    instruments = sorted(
        set(account.positions).union(top_candidates if is_rebalance else [])
    )
    if not instruments:
        receivable = float(getattr(account, "total_receivable", 0.0))
        result = {
            "trade_date": trade_date,
            "execution_price_mode": execution_price_mode,
            "cash_before": float(account.cash),
            "market_value_before": 0.0,
            "total_value_before": float(account.cash) + receivable,
            "cash_after": float(account.cash),
            "market_value_after": 0.0,
            "total_value_after": float(account.cash) + receivable,
            "receivable_before": receivable,
            "receivable_after": receivable,
            "order_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "filled_count": 0,
            "rejected_count": 0,
            "turnover": 0.0,
            "position_count": 0,
            "status": "no_signal_data",
        }
        _add_policy_audit(
            result, {}, set(), delta_threshold, views, trade_date,
            is_rebalance=is_rebalance,
        )
        return result, pd.DataFrame(), []

    if execution_price_mode != "open":
        raise ValueError("posterior_confirmed requires open execution")
    if market_data is not None:
        if valuation_state is not None:
            unheld_candidates = [
                instrument for instrument in instruments
                if instrument not in account.positions
            ]
            if unheld_candidates:
                valuation_state.seed_asof(
                    market_data.latest_legal_close_before(
                        trade_date, unheld_candidates
                    ),
                    trade_date,
                )
        exec_prices, market_status = market_data.snapshot(
            trade_date, instruments, price_col="open"
        )
        mtm_prices = market_data.observed_close(trade_date, instruments)
        if max_participation_rate is not None and adv_by_instrument is None:
            adv_by_instrument, _ = market_data.adv_snapshot(
                trade_date, instruments, window=adv_window,
                min_periods=adv_min_periods,
            )
    else:
        exec_prices, market_status = market_snapshot_fn(
            trade_date, instruments, price_col="open"
        )
        mtm_prices, _ = market_snapshot_fn(
            trade_date, instruments, price_col="close"
        )
    planning_prices = {}
    if valuation_state is not None:
        planning_prices.update(valuation_state.prices)
    else:
        planning_prices.update(exec_prices)
    planning_total_equity = float(account.get_total_equity(planning_prices))
    planning_account = (
        _ValuationPlanningAccount(account, planning_prices)
        if valuation_state is not None else account
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

    # Pure score-refresh: on rebalance, a held name that has dropped out of the
    # current top_n is sold (its slot is refilled from top_candidates below).
    # Rank-hysteresis band: with rank_exit_hold_top set, keep while the current
    # rank is <= the (wider) hold band; a missing rank counts as rank > band.
    if config.rank_exit and is_rebalance:
        hold_top = config.rank_exit_hold_top
        for instrument in sorted(account.positions):
            if instrument in exit_reasons:
                continue
            if hold_top is not None:
                rank = rank_map.get(instrument)
                if rank is not None and rank <= hold_top:
                    continue  # still inside the hold band — keep
                exit_reasons[instrument] = "rank_exit"
            elif instrument not in top_candidates:
                exit_reasons[instrument] = "rank_exit"

    keep = set(account.positions).difference(exit_reasons)
    valuation_prices = planning_prices if valuation_state is not None else None
    sell_targets = _current_weight_targets(
        account, exec_prices, keep, valuation_prices,
        total_equity=planning_total_equity,
    )
    for instrument in exit_reasons:
        sell_targets[instrument] = 0.0

    # Exposure gate: when the PIT schedule marks this date active, scale the
    # kept book down to exposure_gate_scale of equity.  We normalise the kept
    # weights proportionally (preserving each position's drifted relative
    # size) to an absolute total of scale * len(kept)/top_n — NOT a repeated
    # "multiply by scale", which would compound down on consecutive gated
    # days.  With the book already at the gated size the factor is ~1.0.
    gate_active = bool(
        exposure_gate_schedule is not None
        and exposure_gate_schedule.get(trade_date, False)
    )
    if gate_active and config.exposure_gate_scale < 1.0:
        scale = config.exposure_gate_scale
        kept_weights = {
            instrument: w for instrument, w in sell_targets.items() if w > 0
        }
        total_kept = sum(kept_weights.values())
        if kept_weights and total_kept > 0:
            budget = scale * len(kept_weights) / top_n
            for instrument in kept_weights:
                sell_targets[instrument] = (
                    kept_weights[instrument] / total_kept * budget
                )

    if valuation_state is not None:
        sell_orders = build_valuation_execution_orders(
            account, sell_targets, planning_prices, exec_prices,
            total_equity=planning_total_equity,
        )
    else:
        sell_orders, _, _, _, _, _ = build_order_intents(
            planning_account, day_signal, sell_targets, exec_prices, trade_date
        )
    sell_orders = [order for order in sell_orders if order["side"] == "sell"]
    # A full policy exit is allowed to liquidate a residual odd-lot created
    # by a stock dividend/split.  Ordinary rebalancing remains lot-rounded.
    for order in sell_orders:
        instrument = str(order.get("symbol") or "")
        if instrument in exit_reasons and instrument in account.positions:
            order["amount"] = int(account.positions[instrument].sellable_amount)
            order["price"] = float(exec_prices.get(instrument, order.get("price", 0.0)))
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
        valuation_state=valuation_state,
        adv_by_instrument=adv_by_instrument,
        max_participation_rate=max_participation_rate,
        liquidity_gate_mode=liquidity_gate_mode,
    )
    after_sells = set(account.positions)
    actual_exits = before.difference(after_sells)

    entries: list[str] = []
    buy_orders: list[dict[str, Any]] = []
    buy_targets = _current_weight_targets(
        account, exec_prices, after_sells, valuation_prices,
        total_equity=planning_total_equity,
    )
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
        entry_weight = (
            config.exposure_gate_scale / top_n if gate_active else 1.0 / top_n
        )
        for instrument in entries:
            buy_targets[instrument] = entry_weight
        if valuation_state is not None:
            generated = build_valuation_execution_orders(
                account, buy_targets, planning_prices, exec_prices,
                total_equity=planning_total_equity,
            )
        else:
            generated, _, _, _, _, _ = build_order_intents(
                planning_account, day_signal, buy_targets, exec_prices, trade_date
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
        valuation_state=valuation_state,
        adv_by_instrument=adv_by_instrument,
        max_participation_rate=max_participation_rate,
        liquidity_gate_mode=liquidity_gate_mode,
    )
    after = set(account.positions)
    actual_entries = after.difference(after_sells)
    result = _merge_execution_results(sell_result, buy_result)

    for instrument in actual_exits:
        state.entry_index.pop(instrument, None)
        state.previous_close.pop(instrument, None)
        state.peak_close.pop(instrument, None)
        state.cumulative_cash_per_current_share.pop(instrument, None)
        state.winner_active.discard(instrument)
    for instrument in actual_entries:
        state.entry_index[instrument] = trading_index
        state.cumulative_cash_per_current_share[instrument] = 0.0

    # Current close becomes visible only after today's open decisions and is
    # stored exclusively for the next trading day's decision.
    for instrument in after:
        close = float(mtm_prices.get(instrument, 0.0) or 0.0)
        if close <= 0:
            continue
        total_return_close = close + state.cumulative_cash_per_current_share.get(
            instrument, 0.0
        )
        state.previous_close[instrument] = total_return_close
        state.peak_close[instrument] = max(
            state.peak_close.get(instrument, total_return_close),
            total_return_close,
        )
        position = account.positions[instrument]
        if position.avg_cost > 0 and state.peak_close[instrument] / position.avg_cost - 1.0 >= config.winner_activation_return:
            state.winner_active.add(instrument)

    _add_policy_audit(
        result, exit_reasons, actual_exits, delta_threshold, views, trade_date,
        actual_entries=actual_entries,
        is_rebalance=is_rebalance,
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
    is_rebalance: bool = False,
) -> None:
    actual_entries = actual_entries or set()
    result.update(
        {
            "holding_policy": "posterior_confirmed",
            # Cadence provenance on every daily_summary row: rebalance_due is the
            # schedule-level flag (this date is on the cadence grid);
            # is_rebalance is the execution-level truth (a rebalance actually
            # ran — schedule due AND the day carried score data).  Downstream
            # cohort/signal diagnostics must key on is_rebalance, never on
            # policy_entry_count > 0 (a due day may legitimately trade nothing).
            "rebalance_due": bool(is_rebalance),
            "is_rebalance": bool(
                is_rebalance and bool(views.scores.get(trade_date))
            ),
            "policy_exit_count": len(actual_exits),
            "policy_entry_count": len(actual_entries),
            "hard_stop_exit_count": sum(exit_reasons.get(x) == "hard_stop" for x in actual_exits),
            "score_delta_exit_count": sum(exit_reasons.get(x) == "score_delta" for x in actual_exits),
            "winner_trailing_exit_count": sum(exit_reasons.get(x) == "winner_trailing" for x in actual_exits),
            "stale_replacement_exit_count": sum(exit_reasons.get(x) == "stale_replacement" for x in actual_exits),
            "rank_exit_exit_count": sum(exit_reasons.get(x) == "rank_exit" for x in actual_exits),
            "score_delta_threshold": delta_threshold,
            "score_delta_observation_count": views.delta_observations.get(trade_date, 0),
        }
    )
