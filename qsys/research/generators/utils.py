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
