"""Shared daily-loop helpers for BacktestRunner (first cut).

This module is the first step toward converging the duplicate daily-loop
implementations in ``strategy_runner.py``.  Currently provides only a
predicate for weekly rebalance skip detection — business logic is kept
unchanged.
"""

from __future__ import annotations

from datetime import datetime


def should_skip_weekly_rebalance(
    rebalance_freq: str,
    trade_date: str,
    last_trade_date: str | None,
    trading_dates: list[str] | None = None,
    last_rebalance_date: str | None = None,
) -> bool:
    """Decide whether *trade_date* should skip execution under a rebalance cadence.

    Returns ``True`` when *trade_date* is not a scheduled rebalance day.

    - ``"weekly"``: skip when *trade_date* falls in the same ISO week as
      *last_trade_date* (legacy behavior).
    - ``"<n>d"`` (e.g. ``"5d"``, ``"20d"``, ``"60d"``): refresh every *n*
      trading days.  Skip unless at least *n* trading dates have elapsed
      since *last_rebalance_date* (strictly after it, including today).
      Requires ``trading_dates`` (the backtest trading calendar) and
      ``last_rebalance_date`` (the most recent actual rebalance anchor).
      ``n <= 1`` behaves as daily (never skip).
    - anything else: never skip (daily / legacy passthrough).

    Returns ``False`` if *last_trade_date* / *last_rebalance_date* is ``None``
    (first day always trades).

    Parameters
    ----------
    rebalance_freq:
        Rebalance frequency string (``"weekly"``, ``"daily"``, ``"5d"``, ...).
    trade_date:
        Current candidate trade date (``YYYY-MM-DD``).
    last_trade_date:
        The most recent trade date that was actually processed.
        ``None`` on the very first day of the backtest.
    trading_dates:
        Ordered list of trading dates in the backtest window
        (``YYYY-MM-DD`` strings).  Required for ``"<n>d"`` cadence.
    last_rebalance_date:
        The most recent date on which a rebalance actually executed
        (``YYYY-MM-DD``).  ``None`` on the very first day of the backtest.

    Returns
    -------
    bool
    """
    if rebalance_freq == "weekly":
        if last_trade_date is None:
            return False
        last_iso = datetime.strptime(last_trade_date, "%Y-%m-%d").date().isocalendar()
        this_iso = datetime.strptime(trade_date, "%Y-%m-%d").date().isocalendar()
        return (last_iso[0], last_iso[1]) == (this_iso[0], this_iso[1])
    if rebalance_freq.endswith("d") and rebalance_freq[:-1].isdigit():
        n = int(rebalance_freq[:-1])
        if n <= 1:
            return False  # every-trading-day refresh
        if last_rebalance_date is None:
            return False  # first day always rebalances
        if trading_dates is None:
            raise ValueError(
                f"N-day rebalance '{rebalance_freq}' requires trading_dates"
            )
        try:
            idx = trading_dates.index(last_rebalance_date)
        except ValueError:
            return False  # last anchor outside window — be safe and rebalance
        count = 0
        for d in trading_dates[idx + 1:]:
            if d > trade_date:
                break
            count += 1
        return count < n
    return False
