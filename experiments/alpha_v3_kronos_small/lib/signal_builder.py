"""Post-process Kronos predictions → signal parquet + manifest + evaluation."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


def build_signals(raw_predictions: pd.DataFrame, ohlcv_df: pd.DataFrame) -> pd.DataFrame:
    """Convert raw Kronos predictions to cross-sectional alpha signals.

    For each (trade_date, instrument):
      - kronos_ret_5d = mean(pred_close[1:5]) / fq_close - 1
      - kronos_ret_20d = mean(pred_close[1:20]) / fq_close - 1

    Then per trade_date, cross-section rank and zscore transforms.

    Parameters
    ----------
    raw_predictions : pd.DataFrame
        Columns: trade_date, instrument, step, pred_open, pred_high, pred_low,
        pred_close, pred_volume, pred_amount
    ohlcv_df : pd.DataFrame
        Columns: trade_date, instrument, fq_open, fq_high, fq_low, fq_close,
        volume, amount

    Returns
    -------
    pd.DataFrame with schema:
        trade_date, instrument, kronos_ret_5d, kronos_ret_5d_rank,
        kronos_ret_5d_zscore, kronos_ret_20d, kronos_ret_20d_rank,
        kronos_ret_20d_zscore, current_fq_close, model_name, lookback,
        price_mode, created_at, run_id
    """
    print("[SignalBuilder] Building signals from raw predictions...")

    # Merge current prices (normalise date types)
    price_df = ohlcv_df[["trade_date", "instrument", "fq_close"]].drop_duplicates(
        subset=["trade_date", "instrument"]
    ).copy()
    price_df["trade_date"] = price_df["trade_date"].astype(str).str[:10]
    raw_predictions["trade_date"] = raw_predictions["trade_date"].astype(str).str[:10]
    merged = raw_predictions.merge(price_df, on=["trade_date", "instrument"], how="left")

    # Compute forward return predictions for each horizon
    results = []
    for (td, inst), grp in merged.groupby(["trade_date", "instrument"]):
        grp = grp.sort_values("step")
        pred_close = grp["pred_close"].values
        fq_close = grp["fq_close"].iloc[0]

        if fq_close is None or fq_close <= 0:
            continue

        kronos_5d = np.mean(pred_close[1:5]) / fq_close - 1 if len(pred_close) >= 5 else np.nan
        kronos_20d = np.mean(pred_close[1:20]) / fq_close - 1 if len(pred_close) >= 20 else np.nan

        results.append({
            "trade_date": td,
            "instrument": inst,
            "kronos_ret_5d": kronos_5d,
            "kronos_ret_20d": kronos_20d,
            "current_fq_close": fq_close,
        })

    signal_df = pd.DataFrame(results)

    # Cross-sectional transforms per trade_date
    def _rank(v: pd.Series) -> pd.Series:
        return v.rank(pct=True)

    def _zscore(v: pd.Series) -> pd.Series:
        std = v.std(ddof=0)
        if pd.isna(std) or std < 1e-12:
            return pd.Series(0.0, index=v.index)
        return ((v - v.mean()) / std).clip(-3, 3)

    for col in ["kronos_ret_5d", "kronos_ret_20d"]:
        signal_df[f"{col}_rank"] = signal_df.groupby("trade_date")[col].transform(_rank)
        signal_df[f"{col}_zscore"] = signal_df.groupby("trade_date")[col].transform(_zscore)

    # Metadata columns
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    signal_df["model_name"] = "NeoQuasar/Kronos-small"
    signal_df["lookback"] = 90
    signal_df["price_mode"] = "fq_close"
    signal_df["created_at"] = now
    signal_df["run_id"] = f"alpha_v3_kronos_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    signal_df = signal_df.reset_index(drop=True)
    print(f"  Built {len(signal_df)} signal rows, "
          f"{signal_df['trade_date'].nunique()}d, {signal_df['instrument'].nunique()} stocks")
    return signal_df


def save_signal_artifact(signals: pd.DataFrame, output_dir: Path) -> tuple[str, str]:
    """Save signals to parquet + manifest. Returns (parquet_path, manifest_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / "signals.parquet"
    manifest_path = output_dir / "manifest.json"

    signals.to_parquet(parquet_path, index=False)

    manifest = {
        "run_id": signals["run_id"].iloc[0] if "run_id" in signals.columns else "unknown",
        "mode": "kronos",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_rows": len(signals),
        "n_dates": int(signals["trade_date"].nunique()),
        "n_stocks": int(signals["instrument"].nunique()),
        "signal_schema": list(signals.columns),
        "model": "NeoQuasar/Kronos-small",
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)

    print(f"  → {parquet_path}")
    print(f"  → {manifest_path}")
    return str(parquet_path), str(manifest_path)


def smooth_signals(signals: pd.DataFrame, alphas: list[float] | None = None) -> pd.DataFrame:
    """Apply EMA smoothing to signal z-scores across rebalance dates.

    For each instrument and alpha:
        smoothed[t] = alpha * raw[t] + (1-alpha) * smoothed[t-1]

    This dampens week-to-week prediction noise, improving rank persistence
    and reducing unnecessary turnover.

    Parameters
    ----------
    signals : pd.DataFrame
        Signal DataFrame with trade_date, instrument, and score columns.
    alphas : list[float], optional
        Smoothing factors. Each generates ``_sma{int(a*100)}`` columns.
        Default: [0.3, 0.4, 0.5].

    Returns
    -------
    pd.DataFrame with additional ``_sma{int(alpha*100)}`` columns for each alpha.
    """
    if alphas is None:
        alphas = [0.3, 0.4, 0.5]

    result = signals.sort_values(["instrument", "trade_date"]).copy()

    for alpha in alphas:
        suffix = f"_sma{int(alpha*100)}"
        cols_to_smooth = [c for c in signals.columns
                          if c.endswith("_zscore") and not c.endswith(suffix)]
        if not cols_to_smooth:
            continue
        for col in cols_to_smooth:
            new_col = f"{col}{suffix}"
            result[new_col] = (
                result.groupby("instrument")[col]
                .transform(lambda x, a=alpha: x.ewm(alpha=a, adjust=False).mean())
            )
        print(f"  [Smooth] alpha={alpha}: {[f'{c}{suffix}' for c in cols_to_smooth]}")

    return result


# ── helpers ──

def _zscore_series(v: pd.Series) -> pd.Series:
    """Cross-sectional z-score, clipped to [-3, 3]."""
    std = v.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=v.index)
    return ((v - v.mean()) / std).clip(-3, 3)


def evaluate_signals(signals: pd.DataFrame, label_df: pd.DataFrame | None = None) -> dict:
    """Compute IC, RankIC, ICIR, group returns.

    Parameters
    ----------
    signals : pd.DataFrame
        Signal DataFrame with trade_date, instrument, and score columns.
    label_df : pd.DataFrame, optional
        OHLCV DataFrame used to compute forward returns.

    Returns
    -------
    dict with keys:
        ic_daily : pd.DataFrame — per-date IC/RankIC for each signal column
        ic_summary : dict — mean IC, ICIR, RankIC per horizon
        group_returns : pd.DataFrame — quantile portfolio forward returns
    """
    from scipy.stats import pearsonr, spearmanr

    # Compute forward returns
    if label_df is not None:
        fwd = label_df.sort_values(["instrument", "trade_date"]).copy()
        fwd["trade_date"] = fwd["trade_date"].astype(str).str[:10]
        for h in [5, 20]:
            fwd[f"fwd_{h}d"] = (
                fwd.groupby("instrument")["fq_close"].shift(-h) / fwd["fq_close"] - 1
            )
    else:
        fwd = None

    signal_cols = [c for c in signals.columns
                   if c.endswith("_zscore") or "_blend" in c or c == "momentum_zscore"]

    merged_data = {}
    if fwd is not None:
        for col in signal_cols:
            sig_df = signals[["trade_date", "instrument", col]].dropna().copy()
            sig_df["trade_date"] = sig_df["trade_date"].astype(str).str[:10]
            merged_data[col] = sig_df.merge(
                fwd[["trade_date", "instrument", "fwd_5d", "fwd_20d"]],
                on=["trade_date", "instrument"], how="inner",
            ).dropna(subset=[col, "fwd_5d"])

    dates = sorted(set.intersection(
        *(set(m["trade_date"].unique()) for m in merged_data.values())
    )) if merged_data else []

    ic_rows = []
    for d in dates:
        row = {"date": d}
        for col in signal_cols:
            day = merged_data[col][merged_data[col]["trade_date"] == d]
            if len(day) < 30:
                continue
            try:
                ic_5, _ = pearsonr(day[col], day["fwd_5d"])
                ric_5, _ = spearmanr(day[col], day["fwd_5d"])
            except Exception:
                ic_5, ric_5 = np.nan, np.nan
            try:
                ic_20, _ = pearsonr(day[col], day["fwd_20d"])
            except Exception:
                ic_20 = np.nan
            row[f"ic_5d_{col}"] = ic_5
            row[f"rankic_5d_{col}"] = ric_5
            row[f"ic_20d_{col}"] = ic_20
        if len(row) > 1:
            ic_rows.append(row)

    ic_daily = pd.DataFrame(ic_rows) if ic_rows else pd.DataFrame()

    # Summary
    summary = {}
    for col in signal_cols:
        ic5 = ic_daily.get(f"ic_5d_{col}", pd.Series(dtype=float)).dropna()
        if len(ic5) > 5:
            summary[col] = {
                "ic_mean": round(ic5.mean(), 4),
                "ic_std": round(ic5.std(ddof=0), 4),
                "icir": round(ic5.mean() / max(ic5.std(ddof=0), 1e-10), 4),
                "rankic_mean": round(ic_daily[f"rankic_5d_{col}"].dropna().mean(), 4),
                "ic_positive_pct": round((ic5 > 0).mean() * 100, 1),
                "n_dates": len(ic5),
            }

    # Group returns (using best signal columns)
    group_returns = {}
    if fwd is not None:
        for col in ["kronos_ret_5d_zscore", "kronos_ret_5d_zscore_sma30"]:
            if col not in signals.columns:
                continue
            sig_df = signals[["trade_date", "instrument", col]].dropna().copy()
            sig_df["trade_date"] = sig_df["trade_date"].astype(str).str[:10]
            merged = sig_df.merge(
                fwd[["trade_date", "instrument", "fwd_5d"]],
                on=["trade_date", "instrument"], how="inner",
            ).dropna(subset=[col, "fwd_5d"])
            if len(merged) < 100:
                continue
            merged["quantile"] = merged.groupby("trade_date")[col].transform(
                lambda x: pd.qcut(x, 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"],
                                  duplicates="drop")
            )
            grp = merged.groupby("quantile")["fwd_5d"].agg(["mean", "std", "count"])
            group_returns[col] = grp

    return {
        "ic_daily": ic_daily,
        "ic_summary": summary,
        "group_returns": group_returns,
    }


# ── Momentum signal ──

def add_momentum_signals(signals: pd.DataFrame, ohlcv_df: pd.DataFrame,
                         windows: list[int] | None = None) -> pd.DataFrame:
    """Add momentum z-score columns to signals DataFrame.

    For each (trade_date, instrument), computes past-window return and
    cross-sectional z-score.

    Parameters
    ----------
    signals : pd.DataFrame
        Must have trade_date, instrument.
    ohlcv_df : pd.DataFrame
        Must have trade_date, instrument, fq_close.
    windows : list[int], optional
        Momentum lookback windows.  Default: [5, 20].

    Returns
    -------
    pd.DataFrame with additional ``mom_{w}d_zscore`` columns.
    """
    if windows is None:
        windows = [5, 20]

    result = signals.copy()
    price = ohlcv_df[["trade_date", "instrument", "fq_close"]].drop_duplicates(
        subset=["trade_date", "instrument"]
    ).copy()
    price["trade_date"] = price["trade_date"].astype(str).str[:10]
    price = price.sort_values(["instrument", "trade_date"])

    for w in windows:
        price[f"mom_{w}d"] = price.groupby("instrument")["fq_close"].pct_change(w)
        col = f"mom_{w}d"

        merged = result[["trade_date", "instrument"]].merge(
            price[["trade_date", "instrument", col]],
            on=["trade_date", "instrument"], how="left"
        )
        zcol = f"momentum_{w}d_zscore"
        result[zcol] = merged.groupby("trade_date")[col].transform(_zscore_series)

    print(f"  [Momentum] windows={windows}: "
          f"{[f'momentum_{w}d_zscore' for w in windows]}")
    return result


# ── Risk filter (exclude bottom decile of Kronos preds) ──

def add_risk_filter_signals(signals: pd.DataFrame,
                            filter_pcts: list[float] | None = None,
                            base_col: str = "momentum_20d_zscore",
                            risk_col: str = "kronos_ret_5d") -> pd.DataFrame:
    """Add risk-filtered signal columns.

    For each date, stocks in the bottom ``filter_pct`` of ``risk_col`` are
    excluded by setting their ``base_col`` score to a very negative value.

    Parameters
    ----------
    signals : pd.DataFrame
        Must have trade_date, instrument, base_col, risk_col.
    filter_pcts : list[float], optional
        Fraction of stocks to exclude.  Default: [0.1].
    base_col : str
        Column to use as the base ranking signal.
    risk_col : str
        Column to use as the risk metric (lower = riskier).

    Returns
    -------
    pd.DataFrame with additional ``{base_col}_rf{int(pct*100)}`` columns.
    """
    if filter_pcts is None:
        filter_pcts = [0.1]

    result = signals.copy()
    for pct in filter_pcts:
        suffix = f"_rf{int(pct*100)}"
        new_col = f"{base_col}{suffix}"
        result[new_col] = result[base_col].copy()

        for d in result["trade_date"].unique():
            mask = result["trade_date"] == d
            sub = result.loc[mask, risk_col]
            if len(sub) < 10:
                continue
            threshold = sub.quantile(pct)
            exclude = mask & (result[risk_col] <= threshold)
            result.loc[exclude, new_col] = -9999.0  # effectively exclude from selection

        n_excluded = (result[new_col] < -9990).sum()
        print(f"  [RiskFilter] col={new_col}, exclude_bottom={pct*100:.0f}%, "
              f"excluded={n_excluded}/{len(result)} samples")

    return result


# ── Blended signal (Kronos + momentum) ──

def add_blended_signals(signals: pd.DataFrame,
                        blends: list[tuple[str, str, float]] | None = None
                        ) -> pd.DataFrame:
    """Add blended signal columns from two existing score columns.

    Parameters
    ----------
    signals : pd.DataFrame
        Must contain the referenced score columns.
    blends : list of (col1, col2, weight1), optional
        Creates  ``blend_{w1}{name1}_{w2}{name2}`` columns where
        blended = weight1 * col1 + (1-weight1) * col2.
        Default: [("momentum_20d_zscore", "kronos_ret_5d_zscore_sma30", 0.7)]

    Returns
    -------
    pd.DataFrame with additional blended columns.
    """
    if blends is None:
        blends = [
            ("momentum_20d_zscore", "kronos_ret_5d_zscore", 0.7),
            ("momentum_20d_zscore", "kronos_ret_5d_zscore", 0.5),
            ("momentum_20d_zscore", "kronos_ret_5d_zscore_sma30", 0.7),
            ("momentum_20d_zscore", "kronos_ret_5d_zscore_sma30", 0.5),
        ]

    result = signals.copy()
    for col1, col2, w1 in blends:
        if col1 not in result.columns or col2 not in result.columns:
            continue
        name1 = col1.replace("momentum_", "mom").replace("_zscore", "").replace("_", "")
        name2 = col2.replace("kronos_ret_5d_zscore", "kronos").replace("_sma", "sma")
        w2 = round(1 - w1, 1)
        new_col = f"blend_{int(w1*100)}{name1}_{int(w2*100)}{name2}"
        result[new_col] = w1 * result[col1] + w2 * result[col2]
        print(f"  [Blend] {new_col} = {w1}*{col1} + {w2}*{col2}")

    return result
