"""Label computation functions — forward return, cs_zscore, coverage.

Sunk from scripts/research/compute_labels.py.  CLI entrypoint remains
in scripts/; all business logic lives here.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def cs_zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-sectional zscore, clip, handle constant/all-NaN."""
    clean = s.dropna()
    if len(clean) == 0:
        return pd.Series(float("nan"), index=s.index)
    std = clean.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((clean - clean.mean()) / std).clip(-clip, clip).reindex(s.index)


def compute_forward_return(
    universe: str,
    horizon: int,
    start: str,
    end: str,
    price_field: str = "close",
    norm_type: str = "cs_zscore",
    clip_val: float | None = 3.0,
) -> pd.DataFrame:
    """Compute forward return label.

    Uses forward-adjusted prices (``$close * $factor``) so that dividends,
    stock splits, and rights issues do not distort the return calculation.
    ``$factor`` is the cumulative adjustment factor stored as an independent
    field in the qlib bin (Tushare ``adj_factor`` API, ingested unchanged).

    Parameters
    ----------
    norm_type: "" for raw, "cs_zscore" for cross-sectional normalization.
    clip_val: clip threshold (None = no clip).

    Returns DataFrame(trade_date, instrument, label_id, horizon, label_value).
    label_id = ``fwd_ret_{horizon}d_{suffix}``.
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    price_col = f"${price_field}"
    # Fetch both price and adjustment factor — factor is the cumulative
    # adjustment factor from Tushare (1.0 = no adjustment).
    raw = adapter.get_features(universe, [price_col, "$factor"], start_time=start, end_time=end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    # Forward-adjusted close — essential for correct long-horizon returns
    frame["_adj_price"] = frame[price_col] * frame["$factor"]

    shifted = frame.groupby("instrument")["_adj_price"].transform(lambda s: s.shift(-horizon))
    fwd = shifted / frame["_adj_price"] - 1.0
    frame["_fwd"] = fwd

    suffix = "raw"
    label_value = fwd.astype(np.float32)
    if norm_type == "cs_zscore":
        suffix = "cs_zscore"
        if clip_val is not None:
            suffix += f"_clip{int(clip_val)}"
        valid = frame.dropna(subset=["_fwd"]).copy()
        valid["label_value"] = valid.groupby("trade_date")["_fwd"].transform(
            lambda g: cs_zscore(g.astype(float), clip=clip_val or 3.0)
        )
        label_value = valid["label_value"].astype(np.float32)
        frame = valid

    label_id = f"fwd_ret_{horizon}d_{suffix}"
    result = pd.DataFrame({
        "trade_date": frame["trade_date"],
        "instrument": frame["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": label_value,
    })
    return result.dropna(subset=["label_value"]).reset_index(drop=True)


def compute_raw_forward_return(
    universe: str,
    horizon: int,
    start: str,
    end: str,
    price_field: str = "close",
) -> pd.DataFrame:
    """Compute raw (un-normalized) forward return label.
    Delegates to ``compute_forward_return`` with no normalization.
    """
    return compute_forward_return(universe, horizon, start, end, price_field=price_field, norm_type="", clip_val=None)


def coverage(row_count: int, expected: int) -> float:
    """Coverage ratio: actual rows / expected (dates x universe)."""
    if expected <= 0:
        return 0.0
    return min(row_count / expected, 1.0)
