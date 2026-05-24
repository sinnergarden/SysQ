"""Qlib data loader — fq OHLCV DataFrame for Kronos input."""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from qsys.data.adapter import QlibAdapter  # noqa: E402


def load_fq_ohlcv(
    universe: str = "csi800",
    start_date: str = "2024-07-01",
    end_date: str | None = None,
) -> pd.DataFrame:
    """Load daily OHLCV data from Qlib and compute forward-adjusted prices.

    Returns
    -------
    pd.DataFrame with columns:
        trade_date, instrument, fq_open, fq_high, fq_low, fq_close,
        volume, amount
    """
    print("[Data] Loading fq OHLCV...")
    t0 = time.time()

    adapter = QlibAdapter()
    adapter.init_qlib()

    fetch_end = end_date or datetime.now().strftime("%Y-%m-%d")
    fields = ["$open", "$high", "$low", "$close", "$volume", "$amount", "$factor"]

    raw = adapter.get_features(
        universe, fields,
        start_time=start_date, end_time=fetch_end,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    n_raw = len(frame)

    # Forward-adjust prices using $factor
    for col in ["$open", "$high", "$low", "$close"]:
        fq_col = col.replace("$", "fq_")
        frame[fq_col] = frame[col] * frame["$factor"]

    # Drop rows with NaN in any fq field, zero/negative volume or amount
    fq_cols = ["fq_open", "fq_high", "fq_low", "fq_close"]
    mask = (
        frame[fq_cols].notna().all(axis=1)
        & (frame["$volume"] > 0)
        & (frame["$amount"] > 0)
    )
    frame = frame[mask].copy()
    dropped = n_raw - len(frame)
    if dropped:
        print(f"  Dropped {dropped} rows ({dropped/n_raw*100:.1f}%) — NaN fq or zero vol/amt")

    result = frame[["trade_date", "instrument"] + fq_cols + ["$volume", "$amount"]].copy()
    result = result.rename(columns={"$volume": "volume", "$amount": "amount"})
    result = result.sort_values(["instrument", "trade_date"]).reset_index(drop=True)

    print(f"  Loaded: {len(result)} rows, {result['trade_date'].nunique()}d, {result['instrument'].nunique()} stocks")
    print(f"  Time: {time.time()-t0:.1f}s")
    return result


def resolve_date_range(config: dict) -> tuple[str, str | None]:
    """Resolve (start_date, end_date) from config, defaulting end to None (today)."""
    data_cfg = config.get("data", {})
    start = data_cfg.get("start_date", "2024-07-01")
    end = data_cfg.get("end_date")
    return start, end
