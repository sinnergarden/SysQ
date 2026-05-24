"""Trading calendar resolution — standalone date utilities.

Centralises qlib calendar access so strategies and runners don't duplicate
the same ``QlibAdapter().init_qlib()`` / ``calendar[-1]`` pattern.

Semantics
---------
**asof** (default)
    Return the latest trading day ≤ *trade_date*.  If *trade_date* is itself
    a trading day, returns it unchanged — weekend / holiday rolls back.

**previous**
    Return the latest trading day *strictly before* *trade_date*.  Always
    rolls back one trading day regardless of whether *trade_date* is a
    trading day.

Recognised date formats: ``YYYY-MM-DD``.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

# ---------------------------------------------------------------------------
# Pluggable calendar provider  (default = qlib; overridable for tests)
# ---------------------------------------------------------------------------

_calendar_provider: Callable[[str, str], list[str]] | None = None


def set_calendar_provider(provider: Callable[[str, str], list[str]] | None) -> None:
    """Override the calendar source (used in tests to avoid qlib)."""
    global _calendar_provider
    _calendar_provider = provider


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_calendar_from_qlib(start_date: str, end_date: str) -> list[str]:
    from qlib.data import D as qlib_D

    from qsys.data.adapter import QlibAdapter

    QlibAdapter().init_qlib()
    cal = qlib_D.calendar(start_time=start_date, end_time=end_date)
    if cal is None or len(cal) == 0:
        raise ValueError(
            f"qlib calendar returned no trading dates in [{start_date}, {end_date}]"
        )
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in cal]


def _get_calendar_fallback(start_date: str, end_date: str) -> list[str]:
    """Approximate fallback via pandas business-date range."""
    dates = pd.bdate_range(start=start_date, end=end_date)
    return [d.strftime("%Y-%m-%d") for d in dates]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_trading_calendar(start_date: str, end_date: str) -> list[str]:
    """Return sorted list of trading-day strings in [*start_date*, *end_date*].

    Uses the registered calendar provider when set (test hook), otherwise
    delegates to qlib.  Falls back to ``pd.bdate_range`` when qlib is
    unavailable.
    """
    if _calendar_provider is not None:
        return _calendar_provider(start_date, end_date)
    try:
        return _get_calendar_from_qlib(start_date, end_date)
    except Exception:
        return _get_calendar_fallback(start_date, end_date)


def resolve_data_date(trade_date: str, *, mode: str = "asof") -> str:
    """Resolve the data-observable date for *trade_date*.

    Parameters
    ----------
    trade_date : str
        Target trading date (YYYY-MM-DD).
    mode : str
        ``"asof"`` (default) or ``"previous"``.

    Returns
    -------
    str
        The resolved data date (YYYY-MM-DD).
    """
    if mode not in ("asof", "previous"):
        raise ValueError(f"unsupported calendar mode {mode!r}; expected 'asof' or 'previous'")

    cal = get_trading_calendar("2020-01-01", trade_date)
    if not cal:
        raise ValueError(f"no trading dates available up to {trade_date}")

    if mode == "asof":
        return cal[-1]

    # mode == "previous"
    if len(cal) < 2:
        raise ValueError(
            f"cannot resolve previous trading day for {trade_date}: "
            f"only one date ({cal[0]}) available in calendar"
        )
    return cal[-2]


def resolve_previous_trading_date(trade_date: str) -> str:
    """Shorthand for ``resolve_data_date(trade_date, mode='previous')``."""
    return resolve_data_date(trade_date, mode="previous")
