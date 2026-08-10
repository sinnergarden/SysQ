"""Point-in-time availability rules for externally published feature inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


MARGIN_SOURCE = "tushare.margin_detail"
MARGIN_SOURCE_FIELDS = (
    "margin_balance",
    "margin_buy_amount",
    "margin_repay_amount",
    "margin_total_balance",
    "lend_volume",
    "lend_sell_volume",
    "lend_repay_volume",
)


def normalise_feature_availability(value: Any) -> dict[str, dict[str, Any]]:
    """Return the canonical feature-availability contract.

    Missing configuration preserves the historical zero-lag behaviour for
    strategies that do not opt in.  ``financial_rc`` explicitly configures a
    one-session lag and pins the resulting contract in every model/artifact.
    """

    if value is None:
        raw: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise ValueError("feature_availability must be a mapping")

    margin_raw = raw.get("margin", {})
    if not isinstance(margin_raw, Mapping):
        raise ValueError("feature_availability.margin must be a mapping")
    try:
        lag_sessions = int(margin_raw.get("lag_sessions", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "feature_availability.margin.lag_sessions must be an integer"
        ) from exc
    if lag_sessions < 0:
        raise ValueError(
            "feature_availability.margin.lag_sessions must be non-negative"
        )
    source = str(margin_raw.get("source", MARGIN_SOURCE)).strip()
    if source != MARGIN_SOURCE:
        raise ValueError(
            f"feature_availability.margin.source must be {MARGIN_SOURCE!r}"
        )
    return {
        "margin": {
            "source": source,
            "lag_sessions": lag_sessions,
            "availability_rule": "previous_open_session",
        }
    }


def resolve_lagged_open_session(
    signal_date: str,
    open_dates: Sequence[str],
    lag_sessions: int,
) -> str:
    """Resolve the exact open session available after ``lag_sessions``."""

    if lag_sessions < 0:
        raise ValueError("lag_sessions must be non-negative")
    resolved_signal = pd.Timestamp(signal_date).strftime("%Y-%m-%d")
    sessions = sorted(
        {pd.Timestamp(value).strftime("%Y-%m-%d") for value in open_dates}
    )
    if resolved_signal not in sessions:
        raise ValueError(f"signal_date is not an open session: {resolved_signal}")
    index = sessions.index(resolved_signal) - lag_sessions
    if index < 0:
        raise ValueError(
            f"insufficient calendar history for lag_sessions={lag_sessions} "
            f"at {resolved_signal}"
        )
    return sessions[index]


def apply_margin_source_lag(
    frame: pd.DataFrame,
    *,
    lag_sessions: int,
    open_dates: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Lag raw margin fields by exact panel trading sessions.

    The lag is applied before any derived margin feature is built.  A simple
    groupby shift can silently jump across a missing instrument/date row; the
    calendar comparison below masks such jumps instead of using data older
    than the declared availability session.
    """

    if lag_sessions < 0:
        raise ValueError("lag_sessions must be non-negative")
    out = frame.copy()
    if lag_sessions == 0 or out.empty:
        return out
    required = {"trade_date", "ts_code"}
    if not required.issubset(out.columns):
        raise ValueError(
            "margin availability lag requires trade_date and ts_code columns"
        )
    margin_fields = [field for field in MARGIN_SOURCE_FIELDS if field in out.columns]
    if not margin_fields:
        raise ValueError("margin availability lag requires raw margin fields")

    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="raise")
    out["_availability_row_order"] = range(len(out))
    out = out.sort_values(["ts_code", "trade_date", "_availability_row_order"])

    if open_dates is None:
        sessions = sorted(
            {pd.Timestamp(value) for value in out["trade_date"].dropna().tolist()}
        )
    else:
        sessions = sorted({pd.Timestamp(value) for value in open_dates})
    source_date_by_target = {
        target: sessions[index - lag_sessions]
        for index, target in enumerate(sessions)
        if index >= lag_sessions
    }
    expected_source_date = out["trade_date"].map(source_date_by_target)
    grouped = out.groupby("ts_code", sort=False)
    observed_source_date = grouped["trade_date"].shift(lag_sessions)
    exact_source = observed_source_date.eq(expected_source_date)
    for field in margin_fields:
        out[field] = grouped[field].shift(lag_sessions).where(exact_source)

    return (
        out.sort_values("_availability_row_order")
        .drop(columns=["_availability_row_order"])
        .reset_index(drop=True)
    )
