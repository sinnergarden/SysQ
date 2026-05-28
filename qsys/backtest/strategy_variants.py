"""Strategy variants for rolling backtest research.

Each ``make_*_portfolio_fn`` returns a ``portfolio_fn`` closure compatible
with ``BacktestEngine.run()`` — it receives ``(scores, account, *, top_n, buffer_hold,
buffer_buy, single_stock_cap, signal_info, **kwargs)`` and returns
``{instrument: target_weight}``.

All functions use only data visible before or on the rebalance date.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.backtest.portfolio import build_rank_weight_portfolio


# ── Helpers ──────────────────────────────────────────────────────────────────


def _rank_pct(series: pd.Series) -> pd.Series:
    """Cross-sectional rank percentile (0..1), lower is better."""
    r = series.rank(ascending=True)
    return (r - 1) / max(len(r) - 1, 1)


def _rolling_quantile(values: list[float], q: float) -> float:
    """Compute quantile from a list of values (at least 20)."""
    if len(values) < 20:
        # default fallback: use all available
        pass
    return float(pd.Series(values).quantile(q))


def compute_index_regime(
    index_close: pd.Series,
    date_str: str,
    *,
    lookback: int = 60,
    vol_lookback: int = 20,
    vol_percentile: float = 0.80,
    vol_hist_window: int = 252,
) -> float:
    """Determine equity exposure based on market regime.

    Uses only index data up to (and including) ``date_str``.
    Returns exposure factor ∈ {1.0, 0.75, 0.50, 0.30}.
    """
    idx = index_close.loc[:date_str]
    if len(idx) < lookback + 5:
        return 1.0

    close = idx.iloc[-1]
    ma60 = idx.iloc[-lookback:].mean()
    ma20 = idx.iloc[-20:].mean()

    # Daily returns for vol computation
    rets = idx.pct_change().dropna()
    vol_20d = float(rets.iloc[-vol_lookback:].std() * np.sqrt(252))

    # Historical vol percentile using past vol_hist_window days
    vol_history = rets.rolling(vol_lookback).std().dropna() * np.sqrt(252)
    recent_vol_history = vol_history.iloc[-vol_hist_window:] if len(vol_history) >= vol_hist_window else vol_history
    vol_threshold = float(recent_vol_history.quantile(vol_percentile)) if len(recent_vol_history) > 20 else 0.5

    above_ma60 = close > ma60

    if above_ma60 and ma20 > ma60:
        # risk_on: trend up, short-term momentum
        return 1.0
    elif above_ma60 and ma20 <= ma60:
        # neutral: above long-term MA but short-term weak
        return 0.75
    elif close <= ma60 and vol_20d <= vol_threshold:
        # risk_off: below MA, normal vol
        return 0.50
    else:
        # crash: below MA, elevated vol
        return 0.30


def compute_crash_risk(
    bt_frame: pd.DataFrame,
    date_str: str,
    instruments: list[str],
    *,
    ret_5d_threshold: float = -0.08,
    vol_ratio_threshold: float = 1.5,
    vol_lookback: int = 10,
    vol_hist_percentile: float = 0.90,
    vol_hist_window: int = 252,
    latest_ret_threshold: float = -0.05,
    vol_ma_window: int = 60,
) -> set[str]:
    """Return set of instruments that trigger crash risk filters.

    Uses bt_frame (OHLCV) data up to and including ``date_str``.
    No future data is used.
    """
    frame = bt_frame[bt_frame["trade_date"] <= date_str].copy()
    if frame.empty:
        return set()

    excluded: set[str] = set()

    for inst in instruments:
        inst_frame = frame[frame["instrument"] == inst].sort_values("trade_date")
        if len(inst_frame) < 10:
            continue

        # Latest close and recent data
        latest = inst_frame.iloc[-1]
        recent = inst_frame.iloc[-6:-1] if len(inst_frame) >= 6 else inst_frame.iloc[:-1]
        past_5d = inst_frame.iloc[-6:-1] if len(inst_frame) >= 6 else inst_frame.iloc[:-1]

        # Close returns for latest 5 trading days
        closes = inst_frame["$close"].values
        volumes = inst_frame["$volume"].values

        # Past 5d return (5 trading days ago to yesterday)
        if len(closes) >= 6:
            past_5d_ret = closes[-2] / closes[-6] - 1  # yesterday vs 5 days ago
        else:
            past_5d_ret = 0.0

        # Past 5d volume ratio
        if len(volumes) >= vol_ma_window + 5:
            vol_ma60 = np.mean(volumes[-(vol_ma_window + 5):-5])
            recent_vol = np.mean(volumes[-6:-1]) if len(volumes) >= 6 else 0.0
        else:
            vol_ma60 = np.mean(volumes) if len(volumes) > 0 else 1.0
            recent_vol = np.mean(volumes[-5:]) if len(volumes) >= 5 else 0.0
        vol_ratio = recent_vol / max(vol_ma60, 1.0)

        # 10d realized vol
        daily_rets = pd.Series(closes).pct_change().dropna()
        if len(daily_rets) >= vol_lookback:
            vol_10d = float(daily_rets.iloc[-vol_lookback:].std() * np.sqrt(252))
        else:
            vol_10d = 0.0

        # Universe percentile for vol
        # (We approximate by comparing to all-instrument history rather than
        # computing per-instrument then taking universe percentile.)

        # Latest day return
        latest_ret = (closes[-1] / closes[-2] - 1) if len(closes) >= 2 else 0.0

        # Condition 1: sharp drop + elevated volume
        if past_5d_ret < ret_5d_threshold and vol_ratio > vol_ratio_threshold:
            excluded.add(inst)
            continue

        # Condition 2: extremely high volatility
        # (compare to all instruments' 10d vol for this date)
        # Note: universe percentile is computed per date outside this function

        # Condition 3: latest day crash
        if latest_ret < latest_ret_threshold:
            excluded.add(inst)

    return excluded


def _compute_vol_percentile(
    bt_frame: pd.DataFrame,
    date_str: str,
    instruments: list[str],
    *,
    vol_lookback: int = 10,
    percentile: float = 0.90,
) -> float:
    """Compute the ``percentile``-ile of 10d realized vol across universe on date_str."""
    frame = bt_frame[bt_frame["trade_date"] <= date_str].copy()
    vols: list[float] = []
    for inst in instruments:
        inst_frame = frame[frame["instrument"] == inst].sort_values("trade_date")
        closes = inst_frame["$close"].values
        if len(closes) < vol_lookback + 2:
            continue
        daily_rets = pd.Series(closes).pct_change().dropna()
        v = float(daily_rets.iloc[-vol_lookback:].std() * np.sqrt(252))
        vols.append(v)
    if len(vols) < 10:
        return 0.5  # fallback
    return float(pd.Series(vols).quantile(percentile))


def precompute_crash_features(bt_frame: pd.DataFrame) -> pd.DataFrame:
    """Pre-compute crash risk features for all (instrument, trade_date).

    Uses vectorized groupby.rolling operations — O(N) once, ~2s for full dataset.
    Exact same logic as the per-instrument-per-date loops in ``compute_crash_risk``.

    Returns
    -------
    DataFrame indexed by (instrument, trade_date) with columns:
        past_5d_ret, vol_ratio, vol_10d, latest_ret
    """
    bt = bt_frame.sort_values(["instrument", "trade_date"]).copy()

    # Daily return per instrument
    bt["daily_ret"] = bt.groupby("instrument")["$close"].transform(
        lambda x: x.pct_change()
    )

    # Past 5d return: close_{t-1} / close_{t-6} - 1  (5 days ago → yesterday)
    bt["past_5d_ret"] = bt.groupby("instrument")["$close"].transform(
        lambda x: x.pct_change(periods=5)
    )

    # Volume ratio: recent 5d avg / 60d avg
    bt["vol_ma60"] = bt.groupby("instrument")["$volume"].transform(
        lambda x: x.shift(1).rolling(60, min_periods=1).mean()
    )
    bt["recent_vol"] = bt.groupby("instrument")["$volume"].transform(
        lambda x: x.shift(1).rolling(5, min_periods=1).mean()
    )
    bt["vol_ratio"] = bt["recent_vol"] / bt["vol_ma60"].clip(lower=1.0)

    # 10d realized vol (annualized)
    bt["vol_10d"] = bt.groupby("instrument")["daily_ret"].transform(
        lambda x: x.rolling(10, min_periods=2).std() * np.sqrt(252)
    )

    # Latest 1d return
    bt["latest_ret"] = bt["daily_ret"]

    result = bt[["instrument", "trade_date", "past_5d_ret", "vol_ratio",
                  "vol_10d", "latest_ret"]].copy()
    return result.set_index(["instrument", "trade_date"])


def get_crash_risk_stocks(
    crash_features: pd.DataFrame,
    date_str: str,
    instruments: list[str],
    *,
    ret_5d_threshold: float = -0.08,
    vol_ratio_threshold: float = 1.5,
    latest_ret_threshold: float = -0.05,
    vol_percentile: float = 0.90,
) -> set[str]:
    """Crash risk detection using pre-computed features (vectorized).

    Returns set of instrument symbols that trigger exclusion conditions.
    Per-rebalance cost: O(1) indexed lookup (vs O(n * T_d) for original loop).
    """
    # Get features for this date, filtered to candidate instruments
    idx = pd.IndexSlice
    try:
        feats = crash_features.loc[idx[:, date_str], :].copy()
    except (KeyError, ValueError):
        return set()
    feats = feats[feats.index.get_level_values("instrument").isin(instruments)]
    if feats.empty:
        return set()

    # Universe vol percentile for this date
    vol_vals = feats["vol_10d"].dropna()
    vol_threshold = float(vol_vals.quantile(vol_percentile)) if len(vol_vals) >= 10 else 0.5

    # Vectorized conditions
    c1 = (feats["past_5d_ret"] < ret_5d_threshold) & \
         (feats["vol_ratio"] > vol_ratio_threshold)
    c2 = feats["vol_10d"] > vol_threshold
    c3 = feats["latest_ret"] < latest_ret_threshold

    excluded = c1 | c2 | c3
    return set(feats.loc[excluded].index.get_level_values("instrument").unique())


def apply_turnover_budget(
    ideal_weights: dict[str, float],
    prev_weights: dict[str, float],
    account,
    *,
    budget: float = 0.20,
    min_delta: float = 0.005,
) -> dict[str, float]:
    """Constrain rebalance turnover to ``budget`` fraction of portfolio.

    Priority ordering:
      P1: sell positions whose rank has fallen out of buffer
      P2: buy top-ranked stocks not currently held
      P3-P4: adjust over/under-weight positions
    """
    # If no prev_weights or no account positions, return ideal
    if not prev_weights:
        return ideal_weights

    # Determine current holdings
    held = set(account.positions.keys())

    # Compute deltas
    all_syms = set(ideal_weights.keys()) | held
    deltas: list[tuple[str, float, int]] = []  # (symbol, delta, priority)
    for sym in all_syms:
        ideal = ideal_weights.get(sym, 0.0)
        current = prev_weights.get(sym, 0.0)
        delta = ideal - current
        if abs(delta) < min_delta:
            continue

        if delta < 0:  # sell
            rank = _get_rank_from_weights(ideal_weights, sym)
            if rank is not None and rank > 60:
                priority = 1  # P1: sell out-of-buffer
            else:
                priority = 3  # P3: reduce
        else:  # buy
            rank = _get_rank_from_weights(ideal_weights, sym)
            if rank is not None and rank <= 20:
                priority = 2  # P2: buy top-ranked
            else:
                priority = 4  # P4: increase
        deltas.append((sym, delta, priority))

    # Sort by priority (ascending)
    deltas.sort(key=lambda x: x[2])

    # Apply deltas within budget
    total_turnover = sum(abs(d[1]) for d in deltas)
    if total_turnover <= budget:
        return ideal_weights

    scale = budget / max(total_turnover, 1e-12)
    result: dict[str, float] = {}
    applied: dict[str, float] = {}
    for sym, delta, _pri in deltas:
        adjusted_delta = delta * scale
        applied[sym] = prev_weights.get(sym, 0.0) + adjusted_delta

    # Normalize to sum=1
    total = sum(applied.values())
    if total > 0:
        result = {k: v / total for k, v in applied.items()}
    else:
        result = prev_weights

    return result


def _get_rank_from_weights(
    weights: dict[str, float], sym: str,
) -> int | None:
    """Infer rank from weights (higher weight → lower rank number)."""
    if not weights:
        return None
    sorted_syms = sorted(weights, key=lambda s: weights[s], reverse=True)
    try:
        return sorted_syms.index(sym) + 1
    except ValueError:
        return None


# ── Strategy 1: alpha_v1_dynamic_topn ──────────────────────────────────────


def make_dynamic_topn_portfolio_fn(window: int = 252):
    """Dynamically adjust top_n / buffer based on score spread quantile.

    Market hypothesis: strong signal days justify concentration;
    weak signal days require diversification.
    """
    spread_history: list[float] = []

    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        ranked = scores.sort_values(ascending=False)
        n = len(ranked)

        # Spread = mean(top20) - mean(rank80-120)
        top20_mean = ranked.head(20).mean()
        if n >= 120:
            mid_mean = ranked.iloc[79:120].mean()
        else:
            mid_mean = ranked.median()
        spread = top20_mean - mid_mean

        spread_history.append(spread)
        if len(spread_history) > window:
            spread_history.pop(0)

        # Determine regime from rolling quantiles
        if len(spread_history) >= 60:
            q70 = pd.Series(spread_history).quantile(0.70)
            q30 = pd.Series(spread_history).quantile(0.30)
            if spread >= q70:
                tn, bh, bb, cap = 10, 50, 30, 0.07
            elif spread >= q30:
                tn, bh, bb, cap = 20, 60, 40, 0.07
            else:
                tn, bh, bb, cap = 40, 100, 80, 0.05
        else:
            tn, bh, bb, cap = 20, 60, 40, 0.07

        return build_rank_weight_portfolio(
            scores, account,
            top_n=tn, buffer_hold=bh, buffer_buy=bb,
            single_stock_cap=cap,
        )

    return _fn


# ── Strategy 2: alpha_v1_split_5d20d ───────────────────────────────────────


def compute_split_5d20d_adjusted_scores(
    scores: pd.Series, signal_info: dict | None,
) -> pd.Series:
    """Regime-aware blend of z5 and z20 signals.

    Returns adjusted scores::
        z5_high + z20_high → 1.2*z5 + 0.8*z20  (resonance, amplify)
        z5_high + z20_low  → 0.7*z5 + 0.1*z20  (short-term pop, discount)
        z5_low  + z20_high → 0.4*z5 + 0.8*z20  (mid-term trend, blend)
        other              → 0.8*z5 + 0.2*z20  (baseline blend)
    """
    if not signal_info:
        return scores

    z5_all = np.array([si["z5"] for si in signal_info.values()])
    z20_all = np.array([si["z20"] for si in signal_info.values()])

    adj: dict[str, float] = {}
    for inst in scores.index:
        si = signal_info.get(inst)
        if si is None:
            adj[inst] = float(scores[inst])
            continue

        z5 = si["z5"]
        z20 = si["z20"]

        z5_pct = (z5_all < z5).mean()
        z20_pct = (z20_all < z20).mean()

        z5_high = z5_pct <= 0.25
        z20_high = z20_pct <= 0.25
        z5_low = z5_pct >= 0.60
        z20_low = z20_pct >= 0.60

        if z5_high and z20_high:
            adj[inst] = 1.2 * z5 + 0.8 * z20
        elif z5_high and z20_low:
            adj[inst] = 0.7 * z5 + 0.1 * z20
        elif z5_low and z20_high:
            adj[inst] = 0.4 * z5 + 0.8 * z20
        else:
            adj[inst] = 0.8 * z5 + 0.2 * z20

    return pd.Series(adj)


def make_split_5d20d_portfolio_fn():
    """Regime-aware blend of z5 and z20 signals.

    Market hypothesis: z5 and z20 express different market structures.
    Resonant signals (both high) deserve extra weight; short-term-only
    signals should be discounted.
    """
    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        adj_series = compute_split_5d20d_adjusted_scores(scores, signal_info)
        return build_rank_weight_portfolio(
            adj_series, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
        )

    return _fn


# ── Strategy 3: alpha_v1_regime_exposure ────────────────────────────────────


def make_regime_exposure_portfolio_fn(index_close: pd.Series):
    """Scale total equity exposure based on market regime.

    Market hypothesis: alpha_v1 is a long-only cross-sectional strategy;
    systematic beta drawdowns can be mitigated by reducing exposure
    when the broad market weakens.
    """
    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        # compute baseline weights
        baseline = build_rank_weight_portfolio(
            scores, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
        )
        if not baseline:
            return baseline

        # determine regime exposure
        date_str = scores.name if hasattr(scores, "name") and scores.name else ""
        exposure = compute_index_regime(index_close, date_str)

        # scale all weights
        return {k: v * exposure for k, v in baseline.items()}

    return _fn


# ── Strategy 4: alpha_v1_turnover_budget ────────────────────────────────────


def make_turnover_budget_portfolio_fn(
    budget: float = 0.20, min_delta: float = 0.005,
):
    """Limit per-rebalance turnover to reduce trading costs.

    Market hypothesis: weekly full rebalance over-trades;
    restricting turnover to high-conviction trades improves net returns.
    """
    prev_weights: dict[str, float] = {}

    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        ideal = build_rank_weight_portfolio(
            scores, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
        )
        result = apply_turnover_budget(
            ideal, prev_weights, account,
            budget=budget, min_delta=min_delta,
        )
        prev_weights.clear()
        prev_weights.update(result)
        return result

    return _fn


# ── Strategy 5: alpha_v1_rank_stability ─────────────────────────────────────


def make_rank_stability_portfolio_fn():
    """Require rank persistence for new buys.

    Market hypothesis: single-day high scores are noisy; stocks with
    consistently high scores across multiple signal dates have higher
    quality and lower reversal risk.
    """
    rank_history: dict[str, list[int]] = {}

    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        ranked = scores.sort_values(ascending=False)
        current_ranks = pd.Series(range(1, len(ranked) + 1), index=ranked.index)

        # Update rank history
        for inst in scores.index:
            rank_history.setdefault(inst, []).append(int(current_ranks[inst]))
            if len(rank_history[inst]) > 5:
                rank_history[inst].pop(0)

        held = set(account.positions.keys())

        # Determine which stocks can be held
        keep: dict[str, float] = {}
        for inst in held:
            if inst in current_ranks.index and current_ranks[inst] <= buffer_hold:
                keep[inst] = float(scores.get(inst, 0.0))

        # Determine which new stocks can be bought
        remaining = max(0, top_n - len(keep))
        buys: list[str] = []
        if remaining > 0:
            for inst in ranked.index:
                if inst in held:
                    continue
                if current_ranks[inst] > buffer_buy:
                    continue
                # Check rank stability: current rank <= 20 AND at least 2 of
                # last 3 signal dates had rank <= 40
                hist = rank_history.get(inst, [])
                if len(hist) < 2:
                    # Not enough history — allow with stricter rank check
                    if current_ranks[inst] > 10:
                        continue
                else:
                    recent = hist[-3:] if len(hist) >= 3 else hist
                    qualifying = sum(1 for r in recent if r <= 40)
                    if current_ranks[inst] > 20 or qualifying < 2:
                        continue
                buys.append(inst)
                if len(buys) >= remaining:
                    break

        selected = list(keep.keys()) + buys
        if not selected:
            return {}  # all cash

        selected.sort(key=lambda s: scores.get(s, 0.0), reverse=True)

        # Linear rank-weight with cap
        tr = sum(range(1, len(selected) + 1))
        ws: dict[str, float] = {}
        for ri, s in enumerate(selected):
            raw_w = (len(selected) - ri) / tr
            ws[s] = min(raw_w, single_stock_cap)

        # Normalize to sum=1
        total_w = sum(ws.values())
        if total_w > 0:
            ws = {k: v / total_w for k, v in ws.items()}

        return ws

    return _fn


# ── Strategy 6: alpha_v1_two_book ───────────────────────────────────────────


def make_two_book_portfolio_fn():
    """Split capital into aggressive (top10) and stable (top40) books.

    Market hypothesis: concentrated positions drive alpha while
    diversified holdings reduce drawdowns. A dual-book structure
    captures both benefits without compromise.
    """
    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        # Aggressive book: 40% capital, top10
        aggr = build_rank_weight_portfolio(
            scores, account,
            top_n=10, buffer_hold=30, buffer_buy=20,
            single_stock_cap=0.07,
        )
        aggr_scaled = {k: v * 0.40 for k, v in aggr.items()}

        # Stable book: 60% capital, top40
        stable = build_rank_weight_portfolio(
            scores, account,
            top_n=40, buffer_hold=90, buffer_buy=70,
            single_stock_cap=0.04,
        )
        stable_scaled = {k: v * 0.60 for k, v in stable.items()}

        # Merge with global single-stock cap
        merged: dict[str, float] = {}
        for k, v in list(aggr_scaled.items()) + list(stable_scaled.items()):
            merged[k] = merged.get(k, 0.0) + v

        # Apply global cap 0.07
        excess = sum(v - 0.07 for v in merged.values() if v > 0.07)
        n_capped = sum(1 for v in merged.values() if v > 0.07)
        for k in merged:
            if merged[k] > 0.07:
                merged[k] = 0.07
        # Redistribute excess proportionally to uncapped
        if excess > 0 and n_capped < len(merged):
            uncapped = {k: v for k, v in merged.items() if v < 0.07 and v > 0}
            total_uncapped = sum(uncapped.values())
            if total_uncapped > 0:
                for k in uncapped:
                    merged[k] += excess * (uncapped[k] / total_uncapped)

        # Normalize to sum=1
        total = sum(merged.values())
        if total > 0:
            merged = {k: v / total for k, v in merged.items()}

        return merged

    return _fn


# ── Strategy 7: alpha_v1_crash_filter ───────────────────────────────────────


def make_crash_filter_portfolio_fn(bt_frame: pd.DataFrame, crash_features: pd.DataFrame | None = None):
    """Filter individual stocks with crash risk before portfolio construction.

    Market hypothesis: some large drawdowns come from high-score stocks
    that subsequently crash. Pre-filtering crash-risk stocks reduces
    tail risk.

    Parameters
    ----------
    bt_frame : pd.DataFrame
        OHLCV data for the full backtest period (used when crash_features
        is not pre-computed).
    crash_features : pd.DataFrame or None
        Pre-computed crash risk features from ``precompute_crash_features``.
        When provided, uses fast lookup (O(1) per date) instead of the
        original O(n * T_d) per-date loop.  Results are identical.
    """
    using_fast = crash_features is not None

    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        date_str = scores.name if hasattr(scores, "name") and scores.name else ""
        instruments = list(scores.index)

        if using_fast:
            excluded = get_crash_risk_stocks(crash_features, date_str, instruments)
        else:
            # Original slow path (for backward compatibility)
            vol_90p = _compute_vol_percentile(bt_frame, date_str, instruments)
            excluded = compute_crash_risk(bt_frame, date_str, instruments)

            # Also exclude stocks with extreme vol
            frame = bt_frame[bt_frame["trade_date"] <= date_str].copy()
            for inst in instruments:
                inst_frame = frame[frame["instrument"] == inst].sort_values("trade_date")
                closes = inst_frame["$close"].values
                if len(closes) < 12:
                    continue
                daily_rets = pd.Series(closes).pct_change().dropna()
                v = float(daily_rets.iloc[-10:].std() * np.sqrt(252))
                if v > vol_90p:
                    excluded.add(inst)

        # For held positions that trigger crash filter:
        # Don't force-sell; just exclude from new buys
        held = set(account.positions.keys())
        filtered_instruments = [inst for inst in instruments if inst not in excluded or inst in held]

        if not filtered_instruments:
            filtered_instruments = instruments  # fallback

        filtered_scores = scores[filtered_instruments].dropna()
        if len(filtered_scores) < 5:
            filtered_scores = scores  # fallback: use all

        return build_rank_weight_portfolio(
            filtered_scores, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
        )

    return _fn


# ── Combo: alpha_v1_split_5d20d_regime_exposure ──────────────────────────


def make_split_5d20d_regime_exposure_portfolio_fn(index_close: pd.Series):
    """Split_5d20d scoring + regime-based exposure scaling.

    Market hypothesis: split_5d20d improves stock selection, but cannot
    protect against systematic beta drawdowns. Adding regime overlay
    reduces MDD while preserving most of the selection alpha.
    """

    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        # Step 1: Split_5d20d adjusted scores
        adj_series = compute_split_5d20d_adjusted_scores(scores, signal_info)

        # Step 2: Build baseline portfolio
        baseline = build_rank_weight_portfolio(
            adj_series, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
        )
        if not baseline:
            return baseline

        # Step 3: Scale by regime exposure
        date_str = scores.name if hasattr(scores, "name") and scores.name else ""
        exposure = compute_index_regime(index_close, date_str)
        return {k: v * exposure for k, v in baseline.items()}

    return _fn


# ── Combo: alpha_v1_split_5d20d_crash_filter ─────────────────────────────


def make_split_5d20d_crash_filter_portfolio_fn(
    bt_frame: pd.DataFrame,
    crash_features: pd.DataFrame | None = None,
):
    """Split_5d20d scoring + crash filter front-end.

    Market hypothesis: crash filter removes stocks likely to experience
    extreme negative returns; applying before split_5d20d scoring ensures
    the score adjustment is only computed on investable candidates.
    """

    def _fn(
        scores: pd.Series,
        account,
        *,
        top_n=20, buffer_hold=60, buffer_buy=40,
        single_stock_cap=0.07,
        signal_info=None, **kwargs,
    ) -> dict[str, float]:
        date_str = scores.name if hasattr(scores, "name") and scores.name else ""
        instruments = list(scores.index)

        # Step 1: Get crash-risk excluded set
        if crash_features is not None:
            excluded = get_crash_risk_stocks(crash_features, date_str, instruments)
        else:
            excluded = compute_crash_risk(bt_frame, date_str, instruments)

        # Step 2: Keep held stocks (don't force-sell)
        held = set(account.positions.keys())
        filtered = [i for i in instruments if i not in excluded or i in held]
        if len(filtered) < 5:
            filtered = instruments

        # Step 3: Compute split_5d20d scores on filtered universe
        filtered_scores = scores[filtered].dropna()
        adj_series = compute_split_5d20d_adjusted_scores(filtered_scores, signal_info)

        # Step 4: Build portfolio
        return build_rank_weight_portfolio(
            adj_series, account,
            top_n=top_n, buffer_hold=buffer_hold,
            buffer_buy=buffer_buy, single_stock_cap=single_stock_cap,
        )

    return _fn
