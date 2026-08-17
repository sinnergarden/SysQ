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
    *,
    offset: int = 0,
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

    ``offset`` phase-shifts the cadence grid in trading days: the first
    rebalance happens on the ``offset``-th trading day (0-indexed) of the
    window, then every *n* trading days after that anchor.  ``offset=0`` is
    the historical behaviour (first day always rebalances).  ``offset=20``
    with ``n=60`` puts rebalances on trading days 20, 80, 140, ... — a
    different phase of the same 60-day grid, for phase-robustness studies.

    Returns ``False`` if *last_trade_date* / *last_rebalance_date* is ``None``
    (first day always trades) and *offset* is 0.

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
    offset:
        Trading-day phase offset of the cadence grid (default 0 = first day
        rebalances, matching pre-offset behaviour).

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
        if offset < 0:
            raise ValueError(f"rebalance offset must be >= 0, got {offset}")
        if trading_dates is None:
            raise ValueError(
                f"N-day rebalance '{rebalance_freq}' requires trading_dates"
            )
        if trade_date not in trading_dates:
            return False  # date outside the window — be safe and rebalance
        trade_idx = trading_dates.index(trade_date)
        if trade_idx < offset:
            return True  # before the phase-shifted first rebalance
        if last_rebalance_date is None:
            # First rebalance sits on the offset-th trading day.
            return trade_idx > offset
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
