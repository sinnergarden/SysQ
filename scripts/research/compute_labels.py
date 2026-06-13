#!/usr/bin/env python3
"""Compute and persist label artifacts to LabelStore.

Examples::

    python scripts/research/compute_labels.py \\
        --universe csi300 --start 2023-06-01 --end 2026-06-01 \\
        --horizons 5 20 --overwrite

    python scripts/research/compute_labels.py --help
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))


def _cs_zscore(s: pd.Series, clip: float = 3.0) -> pd.Series:
    """Cross-sectional zscore, clip, handle constant/all-NaN."""
    clean = s.dropna()
    if len(clean) == 0:
        return pd.Series(float("nan"), index=s.index)
    std = clean.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((clean - clean.mean()) / std).clip(-clip, clip).reindex(s.index)


def compute_label(
    universe: str,
    horizon: int,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Compute a single label and return a LabelStore-compatible DataFrame.

    Steps:
    1. Fetch close from qlib for the given universe and date range
    2. Compute forward return: shift(-horizon) / close - 1
    3. Per-date cross-sectional zscore, clip at ±3
    4. Return DataFrame(trade_date, instrument, label_id, horizon, label_value)
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    raw = adapter.get_features(
        universe, ["$close"],
        start_time=start, end_time=end,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    # Forward return
    shifted = frame.groupby("instrument")["$close"].transform(
        lambda s: s.shift(-horizon)
    )
    fwd = shifted / frame["$close"] - 1.0
    frame["_fwd"] = fwd

    # Per-date cross-sectional zscore
    # Compute zscore only on rows with valid forward returns (non-NaN tail removed)
    valid = frame.dropna(subset=["_fwd"]).copy()
    valid["label_value"] = valid.groupby("trade_date")["_fwd"].transform(
        lambda g: _cs_zscore(g.astype(float), clip=3.0)
    )

    label_id = f"fwd_ret_{horizon}d_xsz_clip3"
    result = pd.DataFrame({
        "trade_date": valid["trade_date"],
        "instrument": valid["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": valid["label_value"].astype(np.float32),
    })

    # Drop any remaining NaN rows (should not happen after valid filter, but be safe)
    result = result.dropna(subset=["label_value"]).reset_index(drop=True)

    return result


def compute_label_raw(
    universe: str,
    horizon: int,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Compute raw (un-normalized) forward return label.

    Returns DataFrame(trade_date, instrument, label_id, horizon, label_value).
    """
    from qsys.data.adapter import QlibAdapter

    adapter = QlibAdapter()
    adapter.init_qlib()

    raw = adapter.get_features(
        universe, ["$close"],
        start_time=start, end_time=end,
    )
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]

    shifted = frame.groupby("instrument")["$close"].transform(
        lambda s: s.shift(-horizon)
    )
    fwd = shifted / frame["$close"] - 1.0

    label_id = f"fwd_ret_{horizon}d_raw"
    result = pd.DataFrame({
        "trade_date": frame["trade_date"],
        "instrument": frame["instrument"],
        "label_id": label_id,
        "horizon": int(horizon),
        "label_value": fwd.astype(np.float32),
    })

    result = result.dropna(subset=["label_value"]).reset_index(drop=True)
    return result


def _coverage(row_count: int, expected: int) -> float:
    """Coverage ratio: actual rows / expected (dates × universe)."""
    if expected <= 0:
        return 0.0
    return min(row_count / expected, 1.0)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute and persist label artifacts to LabelStore",
    )
    parser.add_argument("--config", default=None,
                        help="Path to label YAML config (configs/labels/<id>.yaml)")
    parser.add_argument("--universe", default="csi300", help="Qlib universe")
    parser.add_argument("--start", default="2023-06-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-06-01", help="End date (YYYY-MM-DD)")
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 20],
                        help="Forward return horizons")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing label data")
    args = parser.parse_args()

    if args.config:
        import yaml
        config = yaml.safe_load(Path(args.config).read_text())
        config.setdefault("date_range", {"start_date": args.start, "end_date": args.end})
        from qsys.label.store import LabelStore
        store = LabelStore()
        store.compute_and_save_from_config(config, overwrite=args.overwrite)
        print(f"Done: {config['label_id']}")
        return

    from qsys.data.calendar import get_trading_calendar
    from qsys.label.store import LabelStore
    from qsys.data.adapter import QlibAdapter

    store = LabelStore()
    adapter = QlibAdapter()
    adapter.init_qlib()
    cal = get_trading_calendar(args.start, args.end)
    n_dates = len(cal) if cal else 0

    # Count expected instruments
    from qlib.data import D
    insts = D.instruments(args.universe)
    n_insts = len(insts) if insts else 0

    for h in args.horizons:
        effective_dates = max(n_dates - h, 0)
        expected_rows = effective_dates * n_insts if n_dates and n_insts else 0

        # ── xsz_clip3 label ──
        label_id = f"fwd_ret_{h}d_xsz_clip3"
        print(f"Computing {label_id} ...")
        df = compute_label(args.universe, h, args.start, args.end)
        cov = _coverage(len(df), expected_rows)
        print(f"  {len(df)} rows, effective_dates={effective_dates}, coverage={cov:.1%}")

        store.save_labels(
            label_id, df,
            manifest={
                "horizon": h,
                "universe": args.universe,
                "prediction_start": args.start,
                "prediction_end": args.end,
                "formula": f"shift(-{h}) / close - 1, then per-date cs_zscore",
                "normalization": "cross-sectional zscore",
                "clip": 3.0,
                "n_dates": n_dates,
                "effective_dates": effective_dates,
                "coverage": round(cov, 4),
            },
            overwrite=args.overwrite,
        )
        print(f"  Saved {label_id}")

        # ── raw label ──
        raw_id = f"fwd_ret_{h}d_raw"
        print(f"Computing {raw_id} ...")
        df_raw = compute_label_raw(args.universe, h, args.start, args.end)
        store.save_labels(
            raw_id, df_raw,
            manifest={
                "horizon": h,
                "universe": args.universe,
                "prediction_start": args.start,
                "prediction_end": args.end,
                "formula": f"shift(-{h}) / close - 1",
                "normalization": "none",
                "n_dates": n_dates,
                "effective_dates": effective_dates,
                "coverage": round(_coverage(len(df_raw), expected_rows), 4),
            },
            overwrite=args.overwrite,
        )
        print(f"  Saved {raw_id} ({len(df_raw)} rows)")

    print("Done.")


if __name__ == "__main__":
    main()
