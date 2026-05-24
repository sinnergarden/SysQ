"""Synthetic signal generator for when Kronos model is unavailable."""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd


def generate_signals(
    ohlcv_df: pd.DataFrame,
    start_date: str | None = None,
    lookback: int = 20,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic Kronos-like signals using momentum + noise.

    This is a **fallback** when the real Kronos model cannot be loaded.
    Signals are clearly labelled as synthetic and should NOT be treated
    as realistic Kronos predictions.

    Strategy: use ``(close[t] / close[t-lookback] - 1)`` as a base,
    add Gaussian noise, then apply cross-sectional rank and zscore transforms.
    """
    rng = np.random.default_rng(seed)
    print(f"[Synthetic] Generating signals (lookback={lookback}, seed={seed})")

    df = ohlcv_df.copy()
    if start_date is not None:
        df = df[df["trade_date"] >= start_date].copy()

    df = df.sort_values(["instrument", "trade_date"])
    # Per-stock momentum
    df["momentum"] = df.groupby("instrument")["fq_close"].pct_change(lookback)

    # Forward returns for 5d and 20d
    for h in [5, 20]:
        df[f"future_ret_{h}d"] = (
            df.groupby("instrument")["fq_close"].shift(-h) / df["fq_close"] - 1
        )

    # Synthetic kronos signals: noisy version of forward returns
    for h in [5, 20]:
        base = df[f"future_ret_{h}d"].fillna(0).values
        noise = rng.normal(0, base.std() * 0.5 if base.std() > 0 else 0.01, size=len(base))
        df[f"kronos_ret_{h}d_raw"] = base + noise

    # Cross-sectional transforms per trade_date
    def _rank(v: pd.Series) -> pd.Series:
        return v.rank(pct=True)

    def _zscore(v: pd.Series) -> pd.Series:
        std = v.std(ddof=0)
        if pd.isna(std) or std < 1e-12:
            return pd.Series(0.0, index=v.index)
        return ((v - v.mean()) / std).clip(-3, 3)

    for h in [5, 20]:
        col = f"kronos_ret_{h}d_raw"
        df[f"kronos_ret_{h}d"] = df[col]
        df[f"kronos_ret_{h}d_rank"] = df.groupby("trade_date")[col].transform(_rank)
        df[f"kronos_ret_{h}d_zscore"] = df.groupby("trade_date")[col].transform(_zscore)

    # Select output columns
    out_cols = [
        "trade_date", "instrument", "fq_close",
        "kronos_ret_5d", "kronos_ret_5d_rank", "kronos_ret_5d_zscore",
        "kronos_ret_20d", "kronos_ret_20d_rank", "kronos_ret_20d_zscore",
    ]
    result = df[out_cols].dropna(subset=["kronos_ret_5d", "kronos_ret_20d"]).reset_index(drop=True)

    # Metadata columns
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result["model_name"] = "synthetic_fallback"
    result["lookback"] = lookback
    result["price_mode"] = "fq_close"
    result["created_at"] = now
    result["run_id"] = f"alpha_v3_synthetic_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    print(f"  Generated {len(result)} synthetic signal rows, "
          f"{result['trade_date'].nunique()}d, {result['instrument'].nunique()} stocks")
    return result


def save_synthetic_artifact(
    signals: pd.DataFrame, output_dir, run_id: str | None = None,
) -> tuple[str, str]:
    """Save synthetic signals to parquet + manifest. Returns (parquet_path, manifest_path)."""
    output_dir = output_dir / "signals"
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "signals.parquet"
    manifest_path = output_dir / "manifest.json"

    signals.to_parquet(parquet_path, index=False)

    manifest = {
        "run_id": run_id or signals["run_id"].iloc[0] if "run_id" in signals.columns else "unknown",
        "mode": "synthetic",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_rows": len(signals),
        "n_dates": int(signals["trade_date"].nunique()),
        "n_stocks": int(signals["instrument"].nunique()),
        "signal_schema": list(signals.columns),
        "warning": "SYNTHETIC DATA — NOT REAL KRONOS PREDICTIONS",
        "model": "synthetic_fallback",
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"  → {parquet_path}")
    print(f"  → {manifest_path}")
    return str(parquet_path), str(manifest_path)
