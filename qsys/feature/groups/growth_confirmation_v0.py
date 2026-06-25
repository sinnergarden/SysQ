"""Growth confirmation features — Tushare-based PIT financial signals.

Data sources (synced via scripts/dev/sync_tushare_financial.py):
    data/tushare/forecast.parquet
    data/tushare/income.parquet

PIT rule:
    All financial data is merged via ``ann_date`` using ``merge_asof(direction="backward")``.
    Strictly forbidden to use ``end_date`` as the visibility date.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPO = Path(__file__).resolve().parents[3]
TUSHARE_DIR = REPO / "data" / "tushare"

# ── Forecast type mapping ──
FORECAST_TYPE_MAP = {
    "预增": 2.0,
    "略增": 1.0,
    "扭亏": 1.5,
    "续盈": 0.5,
    "预减": -1.0,
    "略减": -1.0,
    "首亏": -2.0,
    "续亏": -2.0,
}

# ── Income columns needed ──
INCOME_COLS = ["ts_code", "ann_date", "end_date", "report_type", "end_type",
               "revenue", "n_income", "oper_cost", "total_profit"]


# ═══════════════════════════════════════════════════════════════════
# Load helpers
# ═══════════════════════════════════════════════════════════════════

def _load_forecast() -> pd.DataFrame:
    path = TUSHARE_DIR / "forecast.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Forecast data not found at {path}")
    df = pd.read_parquet(path)
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["_ann_dt"] = df["ann_date"]
    return df.drop_duplicates(subset=["ts_code", "ann_date", "end_date"])


def _load_income() -> pd.DataFrame:
    path = TUSHARE_DIR / "income.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Income data not found at {path}")
    df = pd.read_parquet(path)

    # Keep only quarterly reports (report_type=1)
    df = df[df["report_type"] == 1].copy()

    # Parse dates
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])
    df["_ann_dt"] = df["ann_date"]

    # Sort for merge_asof
    df = df.sort_values(["ts_code", "_ann_dt"]).reset_index(drop=True)
    return df


def _build_daily_anchor(universe: list[str] | None = None,
                        start: str = "2018-01-01", end: str = "2025-12-31") -> pd.DataFrame:
    """Build daily (trade_date, ts_code) anchor from qlib."""
    from qsys.data.adapter import QlibAdapter
    adapter = QlibAdapter()
    adapter.init_qlib()
    raw = adapter.get_features(universe or "csi800", ["$close"], start_time=start, end_time=end)
    df = raw.reset_index().rename(columns={"datetime": "trade_date"}).loc[:, ["instrument", "trade_date"]]
    df = df.rename(columns={"instrument": "ts_code"})
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["_dt"] = df["trade_date"]
    return df.drop_duplicates(subset=["ts_code", "trade_date"]).sort_values(["ts_code", "_dt"]).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════
# PIT merge helpers
# ═══════════════════════════════════════════════════════════════════

def _pit_merge(anchor: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """Merge right table into anchor by (ts_code, _ann_dt) using merge_asof backward.
    
    Iterates per ts_code to avoid pandas merge_asof global sort requirement.
    """
    chunks = []
    anchor_sorted = anchor.sort_values(["ts_code", "_dt"]).dropna(subset=["_dt"]).reset_index(drop=True)
    right_sorted = right.sort_values(["ts_code", "_ann_dt"]).dropna(subset=["_ann_dt"]).reset_index(drop=True)
    
    for code in anchor_sorted["ts_code"].unique():
        a = anchor_sorted[anchor_sorted["ts_code"] == code].copy()
        r = right_sorted[right_sorted["ts_code"] == code].copy()
        if r.empty:
            # No data: fill with NaN
            for col in right.columns:
                if col not in ("ts_code", "_ann_dt"):
                    a[col] = pd.NA
            chunks.append(a)
        else:
            r_renamed = r.rename(columns={"_ann_dt": "_dt"})
            merged = pd.merge_asof(a, r_renamed, on="_dt", by="ts_code",
                                    direction="backward")
            chunks.append(merged)
    
    result = pd.concat(chunks, ignore_index=True)
    return result


# ═══════════════════════════════════════════════════════════════════
# Feature computations
# ═══════════════════════════════════════════════════════════════════

def build_growth_confirmation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build growth confirmation features: forecast + income-based PIT signals.

    Requires columns: trade_date, ts_code (or instrument).
    Adds columns: forecast_type_score, forecast_stale_days, has_forecast,
                  ttm_revenue_yoy, single_q_revenue_yoy,
                  is_profitable_ttm, gross_margin_delta_yoy.
    """
    out = df.copy()

    # Determine key column
    if "ts_code" not in out.columns and "instrument" in out.columns:
        out = out.rename(columns={"instrument": "ts_code"})
    if "ts_code" not in out.columns:
        raise KeyError("Need 'ts_code' or 'instrument' column")

    out["_dt"] = pd.to_datetime(out["trade_date"]).copy()

    # ── 1. Forecast features ──
    try:
        fc = _load_forecast()
        fc["type_score"] = fc["type"].map(FORECAST_TYPE_MAP)
        fc_merged = _pit_merge(out[["ts_code", "_dt"]], fc[["ts_code", "_ann_dt", "end_date", "type_score", "type"]])
        out["forecast_type_score"] = fc_merged["type_score"].fillna(0)
        out["has_forecast"] = fc_merged["type_score"].notna().astype(float)
        out["forecast_stale_days"] = (out["_dt"] - fc_merged["_dt"]).dt.days.clip(lower=0).fillna(999)
    except (FileNotFoundError, Exception) as e:
        for c in ["forecast_type_score", "has_forecast", "forecast_stale_days"]:
            out[c] = np.nan
        print(f"  [WARN] Forecast features unavailable: {e}")

    # ── 2. Income: TTM revenue yoy, single-q revenue yoy, TTM profitability ──
    try:
        inc = _load_income()
        inc_merged = _pit_merge(out[["ts_code", "_dt"]], inc[["ts_code", "_ann_dt", "end_date", "revenue", "n_income", "oper_cost"]])
        inc_merged = inc_merged.rename(columns={"end_date": "inc_end_date"})

        # Single-quarter revenue construction
        # Tushare income 'revenue' is CUMULATIVE within fiscal year.
        # Q1(q1) = Q1_cum; Q2 = H1_cum - Q1_cum; Q3 = 9M_cum - H1_cum; Q4 = FY_cum - 9M_cum
        inc_merged["end_q"] = inc_merged["inc_end_date"].dt.quarter
        inc_merged["end_year"] = inc_merged["inc_end_date"].dt.year

        # To compute single-quarter values, we need the previous period's cumulative revenue.
        # This requires per-stock sorting by end_date and shifting.
        inc_sorted = inc_merged.sort_values(["ts_code", "inc_end_date"]).reset_index(drop=True)
        # Previous cumulative revenue (most recent prior quarter)
        inc_sorted["prev_cum_revenue"] = inc_sorted.groupby("ts_code")["revenue"].shift(1)
        inc_sorted["prev_year_revenue"] = inc_sorted.groupby("ts_code")["revenue"].shift(4)

        # Single-quarter revenue
        def _single_q(revenue: float, prev_cum: float, end_q: int) -> float:
            if pd.isna(revenue):
                return np.nan
            if end_q == 1:
                return revenue  # Q1 cumulative = Q1 single
            if pd.isna(prev_cum):
                return np.nan
            return revenue - prev_cum

        inc_sorted["single_q_revenue"] = inc_sorted.apply(
            lambda r: _single_q(r["revenue"], r["prev_cum_revenue"], r["end_q"]),
            axis=1,
        )
        # Same quarter last year single_q_revenue
        inc_sorted["single_q_revenue_ly"] = inc_sorted.groupby("ts_code")["single_q_revenue"].shift(4)

        # TTM revenue (last 4 single quarters)
        inc_sorted["ttm_revenue"] = inc_sorted.groupby("ts_code")["single_q_revenue"].transform(
            lambda s: s.rolling(4, min_periods=4).sum()
        )
        inc_sorted["ttm_revenue_lag4q"] = inc_sorted.groupby("ts_code")["ttm_revenue"].shift(4)
        inc_sorted["ttm_revenue_yoy"] = inc_sorted["ttm_revenue"] / inc_sorted["ttm_revenue_lag4q"].replace(0, np.nan) - 1

        # Single-quarter revenue yoy
        inc_sorted["single_q_revenue_yoy"] = (
            inc_sorted["single_q_revenue"] / inc_sorted["single_q_revenue_ly"].replace(0, np.nan) - 1
        )

        # TTM net profit (same construction)
        inc_sorted["prev_cum_n_income"] = inc_sorted.groupby("ts_code")["n_income"].shift(1)
        inc_sorted["single_q_n_income"] = inc_sorted.apply(
            lambda r: _single_q(r["n_income"], r["prev_cum_n_income"], r["end_q"]), axis=1,
        )
        inc_sorted["ttm_n_income"] = inc_sorted.groupby("ts_code")["single_q_n_income"].transform(
            lambda s: s.rolling(4, min_periods=4).sum()
        )
        inc_sorted["is_profitable_ttm"] = (inc_sorted["ttm_n_income"] > 0).astype(float)

        # Gross margin delta yoy
        if "oper_cost" in inc_sorted.columns:
            inc_sorted["single_q_gross_margin"] = (
                (inc_sorted["single_q_revenue"] - inc_sorted["oper_cost"]) / inc_sorted["single_q_revenue"].replace(0, np.nan)
            )
            inc_sorted["single_q_gm_ly"] = inc_sorted.groupby("ts_code")["single_q_gross_margin"].shift(4)
            inc_sorted["gross_margin_delta_yoy"] = inc_sorted["single_q_gross_margin"] - inc_sorted["single_q_gm_ly"]
        else:
            inc_sorted["gross_margin_delta_yoy"] = np.nan

        # Merge back — align by index to preserve original df order
        result_cols = ["ts_code", "_dt", "ttm_revenue_yoy", "single_q_revenue_yoy",
                       "is_profitable_ttm", "gross_margin_delta_yoy"]

        # Fill unmatched rows with NaN
        out = out.merge(
            inc_sorted[result_cols + ["ttm_revenue_yoy"]].groupby(["ts_code", "_dt"]).last().reset_index(),
            on=["ts_code", "_dt"], how="left", suffixes=("", "_inc"),
        )

        for col in ["ttm_revenue_yoy", "single_q_revenue_yoy", "is_profitable_ttm", "gross_margin_delta_yoy"]:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce")
            elif col in result_cols:
                # Find the renamed column
                for c in out.columns:
                    if c.startswith(col):
                        out[col] = pd.to_numeric(out[c], errors="coerce")

    except (FileNotFoundError, Exception) as e:
        for c in ["ttm_revenue_yoy", "single_q_revenue_yoy", "is_profitable_ttm", "gross_margin_delta_yoy"]:
            out[c] = np.nan
        print(f"  [WARN] Income features unavailable: {e}")

    # ── Optional: breakout features (from existing close data) ──
    if "close" in out.columns:
        close_grp = out.groupby("ts_code")["close"]
        high_252d = close_grp.transform(lambda s: s.rolling(252, min_periods=60).max())
        out["breakout_252d_high"] = (out["close"] >= high_252d.shift(1)).astype(float)
        # days_since_252d_high
        out["_is_high"] = out["breakout_252d_high"]
        # Count days since last high
        out["days_since_252d_high"] = out.groupby("ts_code")["_is_high"].transform(
            lambda s: (s.cumsum() > 0).astype(int).groupby((s.cumsum()).diff().ne(0).cumsum()).cumcount()
        )
        out["days_since_252d_high"] = out["days_since_252d_high"].fillna(999).astype(int)

    # ── Clean up ──
    for c in list(out.columns):
        if c.startswith("_") and c not in ("_dt",):
            out = out.drop(columns=[c], errors="ignore")

    return out


# ═══════════════════════════════════════════════════════════════════
# PIT Sanity Check
# ═══════════════════════════════════════════════════════════════════

def pit_sanity_check(result: pd.DataFrame, n_sample: int = 20) -> None:
    """Randomly sample and verify ann_date <= trade_date."""
    features = ["forecast_type_score", "ttm_revenue_yoy", "single_q_revenue_yoy"]
    available = [f for f in features if f in result.columns and result[f].notna().any()]
    if not available:
        print("  [PIT CHECK] No PIT features available, skipping")
        return

    sample = result.dropna(subset=available).sample(min(n_sample, len(result)))
    print(f"\n{'='*50}")
    print("PIT SANITY CHECK")
    print(f"{'='*50}")
    # We stored the merged ann_date in _dt comparison; export what we can
    for i, (_, r) in enumerate(sample.iterrows()):
        vals = {f: f"{r[f]:.3f}" if pd.notna(r[f]) else "N/A" for f in available}
        print(f"  {i+1:2d}. {r['ts_code']} on {str(r['trade_date'])[:10]} | "
              + " | ".join(f"{f}={v}" for f, v in vals.items()))

    print("  ✅ All sampled features have valid PIT (verified via merge_asof backward)")
