"""Shared helpers for signal generators.

Provides:
- ``cs_zscore`` — cross-sectional z-score with clip
- ``build_prev_trading_date_lookup`` — trade_date → previous_trading_day
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd


def cs_zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-sectional z-score, clip at ±clip, handle constant."""
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-clip, clip)


def build_prev_trading_date_lookup(
    predict_start: str,
    predict_end: str,
) -> dict[str, str]:
    """Build a lookup from trade_date to previous actual trading day.

    Uses ``qsys.data.calendar.get_trading_calendar`` with context before
    *predict_start*.  Falls back to simple business-day logic.
    """
    try:
        from qsys.data.calendar import get_trading_calendar

        extended_start = (
            datetime.strptime(predict_start, "%Y-%m-%d") - timedelta(days=30)
        ).strftime("%Y-%m-%d")
        cal = get_trading_calendar(extended_start, predict_end)
        if cal:
            lookup: dict[str, str] = {}
            for i, d in enumerate(cal):
                if i > 0:
                    lookup[d] = cal[i - 1]
                else:
                    _dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
                    while _dt.weekday() >= 5:
                        _dt -= timedelta(days=1)
                    lookup[d] = _dt.strftime("%Y-%m-%d")
            return lookup
    except Exception:
        pass

    # Fallback: business days
    _start_dt = datetime.strptime(predict_start, "%Y-%m-%d")
    _end_dt = datetime.strptime(predict_end, "%Y-%m-%d")
    cur = _start_dt - timedelta(days=60)
    bdays: list[str] = []
    while cur <= _end_dt:
        if cur.weekday() < 5:
            bdays.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    lookup: dict[str, str] = {}
    for i, d in enumerate(bdays):
        if i > 0:
            lookup[d] = bdays[i - 1]
        else:
            _dt = datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)
            while _dt.weekday() >= 5:
                _dt -= timedelta(days=1)
            lookup[d] = _dt.strftime("%Y-%m-%d")
    return lookup


def build_next_trading_date_lookup(
    predict_start: str,
    predict_end: str,
) -> dict[str, str]:
    """Build a lookup from trade_date to the NEXT actual trading day.

    Mirror of ``build_prev_trading_date_lookup``: for each feature date we
    return the trading day on which a signal generated after that close can be
    executed at the open.  Used to align research signal date semantics with
    the production preopen convention (t 日收盘特征 → t+1 早盘买入):
    signal row = (trade_date=next_td(f), data_date=f), and no feature bar
    exists at/after trade_date.
    """
    try:
        from qsys.data.calendar import get_trading_calendar

        extended_end = (
            datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)
        ).strftime("%Y-%m-%d")
        cal = get_trading_calendar(predict_start, extended_end)
        if cal:
            lookup: dict[str, str] = {}
            for i, d in enumerate(cal):
                if i + 1 < len(cal):
                    lookup[d] = cal[i + 1]
            return lookup
    except Exception:
        pass

    # Fallback: business days
    _start_dt = datetime.strptime(predict_start, "%Y-%m-%d")
    _end_dt = datetime.strptime(predict_end, "%Y-%m-%d")
    cur = _start_dt
    bdays: list[str] = []
    while cur <= _end_dt + timedelta(days=30):
        if cur.weekday() < 5:
            bdays.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    lookup = {}
    for i, d in enumerate(bdays):
        if i + 1 < len(bdays):
            lookup[d] = bdays[i + 1]
    return lookup


def horizon_from_label_id(label_id: str) -> int:
    """Extract the horizon integer from a label ID.

    Handles forward-return labels (``fwd_ret_5d_xsz_clip3``) and max-drawdown
    labels (``fwd_maxdd_5d_binary_5pct``).
    """
    parts = label_id.split("_")
    for i, p in enumerate(parts):
        if p in ("ret", "maxdd") and i + 1 < len(parts):
            cand = parts[i + 1]
            if cand.endswith("d") and cand[:-1].isdigit():
                return int(cand[:-1])
    raise ValueError(f"Cannot extract horizon from label_id: {label_id}")


def check_training_label_maturity(train_end: str, predict_start: str, horizon: int) -> int:
    """F01/F16 maturity gate.

    With training labels shifted to ``next_td(f)`` (strict F01 alignment), the
    last training label ``fwd_ret[next_td(train_end)]`` is realized at
    ``next_td(train_end) + horizon`` trading days, which must be strictly
    before ``predict_start``'s feature cutoff.  This requires ``>= horizon + 2``
    trading days in ``(train_end, predict_start]``.

    Fails loudly when the declared ``label_maturity_lag_trading_days`` is too
    small (a 1-day training-label lookahead into the predict window).
    """
    from qsys.data.calendar import get_trading_calendar

    cal = get_trading_calendar(train_end, predict_start)
    gap = len([d for d in cal if d > train_end and d <= predict_start])
    if gap < horizon + 2:
        raise ValueError(
            f"F01/F16 label maturity violation: horizon={horizon}, "
            f"train_end={train_end}, predict_start={predict_start}; "
            f"trading-day gap={gap} < {horizon + 2}. "
            f"Set label_maturity_lag_trading_days >= {horizon + 1}."
        )
    return gap
