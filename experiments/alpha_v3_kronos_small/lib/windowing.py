"""Per-date window builder for Kronos input format."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import torch
except ImportError:
    torch = None


def build_windows(
    df: pd.DataFrame,
    lookback: int = 90,
    horizons: list[int] | None = None,
) -> list[dict[str, Any]]:
    """Build Kronos input windows for every (date, instrument) pair.

    For each trade_date starting from ``min_date + lookback``, for each
    instrument with ``lookback`` consecutive prior trading days, extract
    a (lookback, 6) tensor = [fq_open, fq_high, fq_low, fq_close, volume, amount].

    Parameters
    ----------
    df : pd.DataFrame
        Must have columns: trade_date, instrument, fq_open, fq_high, fq_low,
        fq_close, volume, amount.  Sorted by instrument then trade_date.
    lookback : int
        Number of prior days to use as input context.
    horizons : list[int] or None
        Forward-looking horizons for target dates (default [5, 20]).

    Returns
    -------
    list[dict]
        Each dict: {trade_date, instrument, input_tensor, target_dates_5d, target_dates_20d}
    """
    if horizons is None:
        horizons = [5, 20]

    windows: list[dict[str, Any]] = []
    all_dates = sorted(df["trade_date"].unique())
    date_to_idx = {d: i for i, d in enumerate(all_dates)}

    # Pre-compute date index lookup
    print(f"[Windowing] Building windows: lookback={lookback}, "
          f"dates={len(all_dates)}, instruments={df['instrument'].nunique()}")

    feature_cols = ["fq_open", "fq_high", "fq_low", "fq_close", "volume", "amount"]

    for inst, grp in df.groupby("instrument"):
        grp = grp.sort_values("trade_date").reset_index(drop=True)
        grp_dates = grp["trade_date"].values
        grp_data = grp[feature_cols].values  # (T, 6)

        for i in range(lookback, len(grp)):
            trade_date = grp_dates[i]
            tensor = grp_data[i - lookback : i]  # (lookback, 6)

            if np.isnan(tensor).any():
                continue

            # Forward target dates
            di = date_to_idx.get(pd.Timestamp(trade_date)) if isinstance(trade_date, str) else date_to_idx.get(trade_date)
            if di is None:
                continue

            target_dates = {}
            for h in horizons:
                target_di = di + h
                if target_di < len(all_dates):
                    target_dates[f"target_dates_{h}d"] = str(all_dates[target_di])
                else:
                    target_dates[f"target_dates_{h}d"] = None

            windows.append({
                "trade_date": str(trade_date),
                "instrument": inst,
                "input_tensor": tensor.astype(np.float32),
                **target_dates,
            })

    print(f"  Built {len(windows)} windows")
    return windows


def collate_batch(windows_batch: list[dict]) -> "torch.Tensor":
    """Stack a batch of windows into a (B, T, C) tensor."""
    if torch is None:
        raise ImportError("torch is required for collate_batch")
    tensors = [w["input_tensor"] for w in windows_batch]
    return torch.from_numpy(np.stack(tensors, axis=0))
