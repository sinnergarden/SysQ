"""Fetch-strategy functions extracted from TushareCollector.

Each function is a self-contained unit that accepts its dependencies
explicitly (no hidden ``self`` access).  The collector module passes in
its retry-wrapped callable, API handle, and configuration as arguments.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd


def fetch_with_retry(
    api_func: Callable,
    max_retries: int,
    log_warning: Callable,
    **kwargs,
):
    """Call *api_func* with *kwargs*, retrying up to *max_retries* times.

    Each retry waits ``1 * attempt`` seconds.  Raises on final failure.
    ``log_warning`` is a callable such as ``logger.warning``.
    """
    last_error: Exception | None = None
    for i in range(max_retries):
        try:
            return api_func(**kwargs)
        except Exception as e:
            last_error = e
            log_warning(f"API call failed (attempt {i+1}/{max_retries}): {e}")
            time.sleep(1 * (i + 1))
    raise RuntimeError(f"Max retries exceeded: {last_error}") from last_error


def fetch_by_stock_loop(
    api: Callable,
    fields: str,
    start_date: str,
    end_date: str,
    code_list: list[str],
    fetch_fn: Callable,
) -> pd.DataFrame:
    """Iterate *code_list*, fetching per-stock date-range data.

    Chunks large ranges into 5-year windows to stay within Tushare's row limit.
    Rate-limited to ~170 calls/min via ``time.sleep(0.35)`` between requests.
    *fetch_fn* should be a callable with the same signature as
    ``fetch_with_retry`` (or the original ``_fetch_with_retry``).
    """
    dfs: list[pd.DataFrame] = []
    start_dt = datetime.strptime(str(start_date), "%Y%m%d")
    end_dt = datetime.strptime(str(end_date), "%Y%m%d")

    chunks: list[tuple[str, str]] = []
    curr = start_dt
    while curr <= end_dt:
        chunk_end = datetime(min(curr.year + 4, end_dt.year), 12, 31)
        if chunk_end > end_dt:
            chunk_end = end_dt
        chunks.append((curr.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d")))
        curr = chunk_end + timedelta(days=1)

    for code in code_list:
        for c_start, c_end in chunks:
            df = fetch_fn(
                api,
                ts_code=code,
                start_date=c_start,
                end_date=c_end,
                fields=fields,
            )
            if df is not None and not df.empty:
                dfs.append(df)
            time.sleep(0.35)

    if not dfs:
        return pd.DataFrame()

    dfs = [d for d in dfs if not d.empty and not d.isna().all().all()]
    if not dfs:
        return pd.DataFrame()

    return pd.concat(dfs, ignore_index=True)


def fetch_by_date_loop(
    api: Callable,
    fields: str,
    start_date: str,
    end_date: str,
    fetch_fn: Callable,
    *,
    ts_codes: list[str] | None = None,
    trade_cal_fn: Callable | None = None,
) -> pd.DataFrame:
    """Iterate trading days, fetching per-date data.

    Uses *trade_cal_fn* (e.g. ``pro.trade_cal``) to resolve open days.
    Falls back to empty DataFrame when the calendar cannot be fetched.
    *fetch_fn* should be a callable with the same signature as
    ``fetch_with_retry``.
    """
    if trade_cal_fn is not None:
        try:
            cal = trade_cal_fn(start_date=start_date, end_date=end_date, is_open="1")
        except Exception:
            return pd.DataFrame()
    else:
        return pd.DataFrame()

    if cal is None or cal.empty:
        return pd.DataFrame()

    dates = cal["cal_date"].tolist()
    dfs: list[pd.DataFrame] = []

    for date in dates:
        df = fetch_fn(api, trade_date=date, fields=fields)
        if df is not None and not df.empty:
            if ts_codes and "ts_code" in df.columns:
                df = df[df["ts_code"].isin(ts_codes)]
            dfs.append(df)

    if not dfs:
        return pd.DataFrame()

    dfs = [d for d in dfs if not d.empty and not d.isna().all().all()]
    if not dfs:
        return pd.DataFrame()

    return pd.concat(dfs, ignore_index=True)
