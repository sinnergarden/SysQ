"""Market snapshot — fetch live prices and trading status for a given trade date.

Single-responsibility module: wraps QlibAdapter to produce a price dict and
market status DataFrame.  No dependencies on other qsys.ops modules.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.data.adapter import QlibAdapter


class ShadowRebalanceError(RuntimeError):
    """Raised when market data or account state is invalid."""
    pass


def fetch_market_snapshot(
    trade_date: str,
    instruments: list[str],
    price_col: str = "close",
) -> tuple[dict[str, float], pd.DataFrame]:
    """Fetch close/open prices and market status for *instruments* on *trade_date*.

    Returns
    -------
    current_prices : dict[str, float]
        Instrument → price (the column selected by *price_col*).
    market_status : pd.DataFrame
        Indexed by instrument, columns ``is_suspended``, ``is_limit_up``,
        ``is_limit_down`` (bool).
    """
    adapter = QlibAdapter()
    adapter.init_qlib()
    market = adapter.get_features(
        instruments,
        ["$close", "$open", "$factor", "$paused", "$high_limit", "$low_limit"],
        start_time=trade_date, end_time=trade_date,
    )
    if market is None or market.empty:
        raise ShadowRebalanceError(f"no market data for {trade_date}")
    market = market.copy()
    market.columns = ["close", "open", "factor", "is_suspended", "limit_up", "limit_down"]
    if isinstance(market.index, pd.MultiIndex) and market.index.names == ["datetime", "instrument"]:
        market = market.swaplevel().sort_index()
    elif isinstance(market.index, pd.MultiIndex) and market.index.names != ["instrument", "datetime"]:
        market = market.reorder_levels([1, 0]).sort_index()
    frame = market.reset_index()
    frame = frame[frame["datetime"].astype(str).str.startswith(trade_date)]
    if frame.empty:
        raise ShadowRebalanceError(f"no market snapshot rows for {trade_date}")
    frame = frame.sort_values(["instrument", "datetime"]).drop_duplicates(subset=["instrument"], keep="last")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame["open"] = pd.to_numeric(frame["open"], errors="coerce")
    frame["limit_up"] = pd.to_numeric(frame["limit_up"], errors="coerce")
    frame["limit_down"] = pd.to_numeric(frame["limit_down"], errors="coerce")
    frame["is_suspended"] = frame["is_suspended"].fillna(0).astype(bool)
    frame["is_limit_up"] = (frame["limit_up"] > 0.01) & (frame[price_col] >= frame["limit_up"])
    frame["is_limit_down"] = (frame["limit_down"] > 0.01) & (frame[price_col] <= frame["limit_down"])
    frame = frame.dropna(subset=[price_col])
    if frame.empty:
        raise ShadowRebalanceError(f"no valid close prices for {trade_date}")
    market_status = frame.set_index("instrument")[["is_suspended", "is_limit_up", "is_limit_down"]]
    current_prices = frame.set_index("instrument")[price_col].astype(float).to_dict()
    return current_prices, market_status
