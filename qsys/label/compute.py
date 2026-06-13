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
) -> pd.DataFrame:
    """Compute forward return label with cs_zscore normalization.

    Returns DataFrame(trade_date, instrument, label_id, horizon, label_value).
    label_id = ``fwd_ret_{horizon}d_xsz_clip3``.
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    raw = adapter.get_features(universe, ["$close"], start_time=start, end_time=end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    shifted = frame.groupby("instrument")["$close"].transform(lambda s: s.shift(-horizon))
    fwd = shifted / frame["$close"] - 1.0
    frame["_fwd"] = fwd

    valid = frame.dropna(subset=["_fwd"]).copy()
    valid["label_value"] = valid.groupby("trade_date")["_fwd"].transform(
        lambda g: cs_zscore(g.astype(float), clip=3.0)
    )

    label_id = f"fwd_ret_{horizon}d_xsz_clip3"
    result = pd.DataFrame({
        "trade_date": valid["trade_date"],
        "instrument": valid["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": valid["label_value"].astype(np.float32),
    })
    return result.dropna(subset=["label_value"]).reset_index(drop=True)


def compute_raw_forward_return(
    universe: str,
    horizon: int,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Compute raw (un-normalized) forward return label.

    Returns DataFrame(trade_date, instrument, label_id, horizon, label_value).
    label_id = ``fwd_ret_{horizon}d_raw``.
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    raw = adapter.get_features(universe, ["$close"], start_time=start, end_time=end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    shifted = frame.groupby("instrument")["$close"].transform(lambda s: s.shift(-horizon))
    fwd = shifted / frame["$close"] - 1.0

    label_id = f"fwd_ret_{horizon}d_raw"
    result = pd.DataFrame({
        "trade_date": frame["trade_date"],
        "instrument": frame["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": fwd.astype(np.float32),
    })
    return result.dropna(subset=["label_value"]).reset_index(drop=True)


def coverage(row_count: int, expected: int) -> float:
    """Coverage ratio: actual rows / expected (dates x universe)."""
    if expected <= 0:
        return 0.0
    return min(row_count / expected, 1.0)
