"""Data contract validation — cutoff enforcement, date normalization, snapshot integrity.

These validations are **data-layer contracts** that complement the artifact and
strategy-interface contracts defined elsewhere.  They enforce the "no future
data leakage" principle at the point of data access.
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from qsys.ops.market_snapshot import ShadowRebalanceError

_TRADE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Date validation ────────────────────────────────────────────────────────────


def normalize_trade_date(date_str: str | None) -> str:
    """Validate and normalize a trade-date string to ``YYYY-MM-DD``.

    Raises
    ------
    ValueError
        If *date_str* is not a valid ``YYYY-MM-DD`` string or cannot be
        parsed as an ISO date.
    """
    if date_str is None:
        raise ValueError("invalid trade date: None")
    if _TRADE_DATE_RE.match(date_str):
        return date_str
    try:
        return pd.Timestamp(date_str).strftime("%Y-%m-%d")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"invalid trade date: {date_str!r}") from exc


# ── Future-data enforcement ────────────────────────────────────────────────────


def assert_no_future_rows(
    df: pd.DataFrame,
    cutoff_date: str,
    date_col: str = "trade_date",
) -> None:
    """Raise ``ValueError`` if any row in *df* has a date after *cutoff_date*.

    Parameters
    ----------
    df
        Data to check.  Must contain *date_col*.
    cutoff_date
        Upper bound (inclusive, YYYY-MM-DD).  Rows with ``date_col > cutoff_date``
        are considered "future" data.
    date_col
        Name of the date column (default ``"trade_date"``).

    Raises
    ------
    ValueError
        If *date_col* is missing from *df*, or if any row violates the cutoff.
    """
    if date_col not in df.columns:
        raise ValueError(f"column {date_col!r} not found in DataFrame")

    cutoff = pd.Timestamp(cutoff_date)
    dates = pd.to_datetime(df[date_col], errors="coerce")
    if dates.isna().any():
        raise ValueError(f"column {date_col!r} contains unparseable dates")

    future = df[dates > cutoff]
    if not future.empty:
        bad_dates = future[date_col].dropna().unique().tolist()
        raise ValueError(
            f"{len(future)} future row(s) found after cutoff {cutoff_date}: "
            f"{bad_dates}"
        )


def validate_market_snapshot(
    current_prices: dict[str, float],
    market_status: pd.DataFrame,
    instruments: list[str],
) -> None:
    """Validate that a market snapshot covers all requested *instruments*.

    Raises
    ------
    ShadowRebalanceError
        If any instrument is missing from *current_prices* or *market_status*.
    """
    missing_prices = sorted(set(instruments) - set(current_prices))
    if missing_prices:
        raise ShadowRebalanceError(
            f"market snapshot missing prices for: {missing_prices}"
        )

    if not market_status.index.isin(instruments).all():
        raise ShadowRebalanceError(
            "market_status contains instruments not in requested list"
        )

    missing_status = sorted(
        set(instruments) - set(market_status.index)
    )
    if missing_status:
        raise ShadowRebalanceError(
            f"market snapshot missing status for: {missing_status}"
        )


def validate_feature_frame(
    df: pd.DataFrame,
    end_date: str,
    *,
    instrument_col: str = "instrument",
    date_col: str = "datetime",
) -> None:
    """Validate a feature frame against the data-cutoff contract.

    Checks:
    1. Required columns exist.
    2. No rows after *end_date*.
    3. No NaN in instrument or date columns.

    Raises
    ------
    ValueError
        If any check fails.
    """
    for col in (instrument_col, date_col):
        if col not in df.columns:
            raise ValueError(f"feature frame missing column {col!r}")

    if df[instrument_col].isna().any():
        raise ValueError("feature frame contains NaN in instrument column")

    if df[date_col].isna().any():
        raise ValueError("feature frame contains NaN in date column")

    assert_no_future_rows(df, end_date, date_col=date_col)
