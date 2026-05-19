"""Alpha V1 — signal generation: blend pred_5d / pred_20d → signal frame."""
from __future__ import annotations

import pandas as pd

from qsys.signal.alpha_v1.labels import cs_zscore


def compute_signal(
    pred_5d: pd.Series,
    pred_20d: pd.Series,
    instruments: pd.Index,
    trade_date: str,
    blend_5d: float = 0.8,
    blend_20d: float = 0.2,
) -> pd.DataFrame:
    """Blend two model predictions into a standard signal frame.

    Returns
    -------
    pd.DataFrame with columns:
        trade_date, instrument, score, rank, pred_5d, pred_20d
    """
    z5 = cs_zscore(pred_5d)
    z20 = cs_zscore(pred_20d)
    blended = blend_5d * z5.values + blend_20d * z20.values

    scores = pd.Series(blended, index=instruments)
    ranked = scores.sort_values(ascending=False)
    rank_map = pd.Series(range(1, len(ranked) + 1), index=ranked.index)

    rows = []
    for i, inst in enumerate(instruments):
        rows.append(
            {
                "trade_date": trade_date,
                "instrument": str(inst),
                "score": float(blended[i]) if pd.notna(blended[i]) else 0.0,
                "rank": int(rank_map.get(inst, 999)),
                "pred_5d": float(pred_5d.iloc[i]) if pd.notna(pred_5d.iloc[i]) else 0.0,
                "pred_20d": float(pred_20d.iloc[i]) if pd.notna(pred_20d.iloc[i]) else 0.0,
            }
        )

    return pd.DataFrame(rows)
