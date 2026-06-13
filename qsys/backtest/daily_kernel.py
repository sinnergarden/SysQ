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
) -> bool:
    """Decide whether *trade_date* should skip execution under weekly rebalance.

    Returns ``True`` when *rebalance_freq* is ``"weekly"`` and
    *trade_date* falls in the same ISO week as *last_trade_date*.
    Returns ``False`` if *rebalance_freq* is not ``"weekly"`` or if
    *last_trade_date* is ``None`` (first day always trades).

    Parameters
    ----------
    rebalance_freq:
        Rebalance frequency string (``"weekly"``, ``"daily"``, etc.).
    trade_date:
        Current candidate trade date (``YYYY-MM-DD``).
    last_trade_date:
        The most recent trade date that was actually processed.
        ``None`` on the very first day of the backtest.

    Returns
    -------
    bool
    """
    if rebalance_freq != "weekly":
        return False
    if last_trade_date is None:
        return False
    last_iso = datetime.strptime(last_trade_date, "%Y-%m-%d").date().isocalendar()
    this_iso = datetime.strptime(trade_date, "%Y-%m-%d").date().isocalendar()
    return (last_iso[0], last_iso[1]) == (this_iso[0], this_iso[1])
