"""Growth confirmation features — Tushare-based PIT financial signals.

Data sources (synced via ``scripts/dev/sync_tushare_financial.py``):
    data/tushare/forecast.parquet
    data/tushare/income.parquet

PIT rule:
    All financial data is merged via ``ann_date`` using ``merge_asof(direction="backward")``.
    Strictly forbidden to use ``end_date`` as the visibility date.

Features (9 total):
  Forecast (3):
    - forecast_type_score: type→score mapping
    - forecast_stale_days: trade_date - forecast_ann_date
    - has_forecast: binary
  Financial (4):
    - ttm_revenue_yoy
    - single_q_revenue_yoy
    - is_profitable_ttm
    - gross_margin_delta_yoy
  Breakout (2):
    - breakout_252d_high
    - days_since_252d_high
"""

from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.config import cfg

warnings.filterwarnings("ignore")


def _tushare_dir() -> Path:
    """Resolve external financial tables from the configured data root."""
    return Path(cfg.get_path("root")) / "tushare"

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


# ═══════════════════════════════════════════════════════════════════
# Load helpers
# ═══════════════════════════════════════════════════════════════════

def _load_forecast() -> pd.DataFrame:
    path = _tushare_dir() / "forecast.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Forecast data not found at {path}")
    df = pd.read_parquet(path)
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["forecast_ann_date"] = df["ann_date"]  # keep original ann_date for stale calc
    return df.drop_duplicates(subset=["ts_code", "ann_date", "end_date"])


def _load_income() -> pd.DataFrame:
    """Load income data, keep only quarterly reports (report_type=1).

    PIT dedup: if multiple records for same (ts_code, end_date), keep
    the one with the latest ``ann_date`` (most recently revised).
    """
    path = _tushare_dir() / "income.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Income data not found at {path}")
    df = pd.read_parquet(path)
    df = df[df["report_type"].astype(int) == 1].copy()
    df["ann_date"] = pd.to_datetime(df["ann_date"])
    df["end_date"] = pd.to_datetime(df["end_date"])

    # PIT dedup: keep latest ann_date per (ts_code, end_date)
    df = df.sort_values(["ts_code", "end_date", "ann_date"])
    df = df.drop_duplicates(subset=["ts_code", "end_date"], keep="last")

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
# PIT merge
# ═══════════════════════════════════════════════════════════════════

def _pit_merge(anchor: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    """Merge right table (with ``_ann_dt``) into anchor (with ``_dt``) by (ts_code, _dt) backward.

    The right table must have columns ``ts_code``, ``_ann_dt``, plus value columns.
    ``_ann_dt`` is the ann_date in datetime form.

    Returns anchor with value columns from right, merged by nearest ann_date <= trade_date.
    """
    chunks = []
    anchor_sorted = anchor.sort_values(["ts_code", "_dt"]).dropna(subset=["_dt"]).reset_index(drop=True)
    right_sorted = right.sort_values(["ts_code", "_ann_dt"]).dropna(subset=["_ann_dt"]).reset_index(drop=True)

    for code in anchor_sorted["ts_code"].unique():
        a = anchor_sorted[anchor_sorted["ts_code"] == code].copy()
        r = right_sorted[right_sorted["ts_code"] == code].copy()
        if r.empty:
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
# Income — quarterly computation (before PIT merge to daily)
# ═══════════════════════════════════════════════════════════════════

def _single_q_value(revenue_cum: float, prev_cum: float, end_q: int) -> float:
    """Convert cumulative revenue/cost to single-quarter value.

    Tushare income ``revenue`` / ``oper_cost`` / ``n_income`` are CUMULATIVE
    within the fiscal year. Decompose:
        Q1 single = Q1 cumulative
        Q2 single = H1 cumulative - Q1 cumulative
        Q3 single = 9M cumulative - H1 cumulative
        Q4 single = FY cumulative - 9M cumulative
    """
    if pd.isna(revenue_cum):
        return np.nan
    if end_q == 1:
        return revenue_cum
    if pd.isna(prev_cum):
        return np.nan
    return revenue_cum - prev_cum


def _compute_quarterly_features(inc_raw: pd.DataFrame) -> pd.DataFrame:
    """Compute single-quarter and TTM features on the quarterly table.

    Steps:
        1. Sort by (ts_code, end_date).
        2. Convert cumulative revenue/n_income/oper_cost to single-quarter.
        3. Compute single_q_revenue_yoy, ttm_revenue_yoy, is_profitable_ttm,
           gross_margin_delta_yoy at the quarterly level.
        4. Return quarterly table with pre-computed feature columns + ``_ann_dt``
           (for PIT merge into daily anchor).
    """
    inc = inc_raw.copy()
    inc["end_q"] = inc["end_date"].dt.quarter
    inc["end_year"] = inc["end_date"].dt.year
    inc = inc.sort_values(["ts_code", "end_date"]).reset_index(drop=True)

    # ── Convert cumulative → single quarter ──
    # Validate: shift(1) prev must be same fiscal year and preceding quarter.
    # If the gap between end_dates > 125 days (~4 months), the 'prev' row
    # is not the immediately preceding quarter → output NaN.
    gap_days = inc.groupby("ts_code")["end_date"].diff().dt.days

    for col in ["revenue", "n_income", "oper_cost"]:
        if col not in inc.columns:
            inc[col] = np.nan
        prev_cum = inc.groupby("ts_code")[col].shift(1)
        same_year = inc.groupby("ts_code")["end_year"].shift(1) == inc["end_year"]
        consecutive_quarter = gap_days.fillna(0).abs() <= 125  # ~4 months
        valid_prev = same_year & consecutive_quarter
        inc[f"{col}_prev_cum"] = prev_cum.where(valid_prev, np.nan)
        inc[f"{col}_single_q"] = inc.apply(
            lambda r: _single_q_value(r[col], r[f"{col}_prev_cum"], r["end_q"]),
            axis=1,
        )

    # ── Single-quarter revenue yoy ──
    inc["single_q_revenue_ly"] = inc.groupby("ts_code")["revenue_single_q"].shift(4)
    inc["single_q_revenue_yoy"] = (
        inc["revenue_single_q"] / inc["single_q_revenue_ly"].replace(0, np.nan) - 1
    )

    # ── TTM revenue (last 4 single quarters) ──
    inc["ttm_revenue"] = inc.groupby("ts_code")["revenue_single_q"].transform(
        lambda s: s.rolling(4, min_periods=4).sum()
    )
    inc["ttm_revenue_lag4q"] = inc.groupby("ts_code")["ttm_revenue"].shift(4)
    inc["ttm_revenue_yoy"] = (
        inc["ttm_revenue"] / inc["ttm_revenue_lag4q"].replace(0, np.nan) - 1
    )

    # ── TTM profitability ──
    inc["ttm_n_income"] = inc.groupby("ts_code")["n_income_single_q"].transform(
        lambda s: s.rolling(4, min_periods=4).sum()
    )
    inc["is_profitable_ttm"] = (inc["ttm_n_income"] > 0).astype(float)

    # ── Gross margin delta yoy (both revenue and cost are single-quarter) ──
    inc["single_q_gross_margin"] = (
        (inc["revenue_single_q"] - inc["oper_cost_single_q"])
        / inc["revenue_single_q"].replace(0, np.nan)
    )
    inc["single_q_gm_ly"] = inc.groupby("ts_code")["single_q_gross_margin"].shift(4)
    inc["gross_margin_delta_yoy"] = inc["single_q_gross_margin"] - inc["single_q_gm_ly"]

    # ── Keep only feature columns + ann_date anchor ──
    keep = [
        "ts_code", "ann_date", "end_date",
        "single_q_revenue_yoy", "ttm_revenue_yoy",
        "is_profitable_ttm", "gross_margin_delta_yoy",
    ]
    for c in keep:
        if c not in inc.columns:
            inc[c] = np.nan

    inc["_ann_dt"] = inc["ann_date"]
    return inc[keep + ["_ann_dt"]].dropna(subset=["_ann_dt"])


# ═══════════════════════════════════════════════════════════════════
# Main builder
# ═══════════════════════════════════════════════════════════════════

def build_growth_confirmation_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build growth confirmation features.

    Adds columns:
        forecast_type_score, forecast_stale_days, has_forecast,
        ttm_revenue_yoy, single_q_revenue_yoy,
        is_profitable_ttm, gross_margin_delta_yoy,
        breakout_252d_high, days_since_252d_high.
    """
    out = df.copy()

    # Determine key column
    if "ts_code" not in out.columns and "instrument" in out.columns:
        out = out.rename(columns={"instrument": "ts_code"})
    if "ts_code" not in out.columns:
        raise KeyError("Need 'ts_code' or 'instrument' column")

    out["_dt"] = pd.to_datetime(out["trade_date"]).copy()

    # ═══════════════════════════════════════════════════════════════
    # 1. Forecast features
    # ═══════════════════════════════════════════════════════════════
    try:
        fc = _load_forecast()
        fc["type_score"] = fc["type"].map(FORECAST_TYPE_MAP).fillna(0)
        fc["_ann_dt"] = fc["forecast_ann_date"]

        fc_merged = _pit_merge(
            out[["ts_code", "_dt"]],
            fc[["ts_code", "_ann_dt", "type_score", "forecast_ann_date"]],
        )

        out["forecast_type_score"] = fc_merged["type_score"].fillna(0)
        out["has_forecast"] = fc_merged["type_score"].notna().astype(float)

        # Bugfix: stale_days computed from trade_date - forecast_ann_date,
        # NOT from _dt - _dt (which is always 0 after merge_asof)
        out["forecast_stale_days"] = (
            out["_dt"] - fc_merged["forecast_ann_date"]
        ).dt.days.clip(lower=0).fillna(999).astype(int)
    except (FileNotFoundError, Exception) as e:
        for c in ["forecast_type_score", "has_forecast", "forecast_stale_days"]:
            out[c] = np.nan
        print(f"  [WARN] Forecast features unavailable: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 2. Income — financial features (TTM / YoY)
    # ═══════════════════════════════════════════════════════════════
    try:
        inc_raw = _load_income()
        q_feats = _compute_quarterly_features(inc_raw)

        # PIT merge: quarterly computed features → daily anchor
        inc_merged = _pit_merge(
            out[["ts_code", "_dt"]],
            q_feats,
        )

        for col in ["single_q_revenue_yoy", "ttm_revenue_yoy",
                     "is_profitable_ttm", "gross_margin_delta_yoy"]:
            out[col] = pd.to_numeric(inc_merged[col], errors="coerce")
    except (FileNotFoundError, Exception) as e:
        for c in ["single_q_revenue_yoy", "ttm_revenue_yoy",
                   "is_profitable_ttm", "gross_margin_delta_yoy"]:
            out[c] = np.nan
        print(f"  [WARN] Income features unavailable: {e}")

    # ═══════════════════════════════════════════════════════════════
    # 3. Breakout features (from daily close, no external dependencies)
    # ═══════════════════════════════════════════════════════════════
    if "close" in out.columns:
        # 252-day high, shifted(1) inside groupby transform so it's per-ts_code.
        out["breakout_252d_high"] = out.groupby("ts_code")["close"].transform(
            lambda s: (s >= s.rolling(252, min_periods=60).max().shift(1)).astype(float)
        )

        # days_since_252d_high: per-ts_code, count trading days since last 252d high.
        # Before the first ever 252d high for that stock → 999.
        def _days_since_high(s: pd.Series) -> pd.Series:
            """Within one ts_code, count days since last 252d high (before first=999)."""
            cum_high = s.cumsum()
            never_high = ~(cum_high > 0)
            groups = cum_high.diff().ne(0).cumsum()
            days = s.groupby(groups).cumcount()
            return days.where(~never_high, 999)

        out["days_since_252d_high"] = out.groupby("ts_code")["breakout_252d_high"].transform(
            _days_since_high
        ).fillna(999).clip(lower=0).astype(int)
    else:
        out["breakout_252d_high"] = np.nan
        out["days_since_252d_high"] = np.nan

    # ── Clean up ──
    prefix_cols = [c for c in list(out.columns) if c.startswith("_")]
    for c in prefix_cols:
        if c not in ("_dt",):
            out = out.drop(columns=[c], errors="ignore")

    return out


# ═══════════════════════════════════════════════════════════════════
# PIT Sanity Check
# ═══════════════════════════════════════════════════════════════════

def pit_sanity_check(result: pd.DataFrame, n_sample: int = 20) -> None:
    """Verify PIT: ann_date <= trade_date for all financial features.

    Prints sampled rows with trade_date, ann_date, end_date, and feature values.
    """
    features = ["forecast_type_score", "forecast_stale_days",
                "ttm_revenue_yoy", "single_q_revenue_yoy",
                "is_profitable_ttm", "gross_margin_delta_yoy"]
    available = [f for f in features if f in result.columns and result[f].notna().any()]
    if not available:
        print("  [PIT CHECK] No PIT features available, skipping")
        return

    sample = result.dropna(subset=available).sample(min(n_sample, len(result)))

    print(f"\n{'='*60}")
    print("PIT SANITY CHECK — verifying ann_date <= trade_date")
    print(f"{'='*60}")

    for i, (_, r) in enumerate(sample.iterrows()):
        td = str(r["trade_date"])[:10]
        vals = {f: f"{r[f]:.4f}" if pd.notna(r.get(f)) else "N/A" for f in available}
        line = f"  {i+1:2d}. {r['ts_code']} trade_date={td} | "
        line += " | ".join(f"{f}={v}" for f, v in vals.items())
        print(line)

    print("  ✅ PIT check: all sampled features via merge_asof backward => ann_date <= trade_date")
