"""Growth confirmation features — Tushare-based PIT financial signals.

Data sources:
    data/tushare/forecast.parquet
    explicit audited immutable income PIT sidecar + manifest identity, or the
    declared legacy-unverified compatibility source

PIT rule:
    Each income-derived feature is merged via the maximum availability of all
    quarterly inputs actually used by its unchanged formula.  ``end_date`` is
    only a report-period ordering key, never a visibility date.
    Audited income is visible only on dates strictly after publication; legacy
    compatibility preserves the historical exact-date merge behavior.

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


def _load_income(
    *,
    artifact_path: str,
    artifact_sha256: str,
    manifest_path: str,
    manifest_sha256: str,
    required_start: str | None,
    required_end: str | None,
    required_history_start: str,
    required_symbols: set[str],
) -> pd.DataFrame:
    """Load one explicit audited first-available income artifact."""

    from qsys.data.income_sidecar import validate_income_sidecar_identity

    identity = validate_income_sidecar_identity(
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        required_start=required_start,
        required_end=required_end,
        required_history_start=required_history_start,
        required_symbols=required_symbols,
    )
    df = pd.read_parquet(identity["artifact_path"])
    required = {
        "ts_code", "ann_date", "f_ann_date", "publication_date",
        "availability_date", "end_date", "report_type", "comp_type",
        "end_type", "update_flag", "n_income", "revenue", "oper_cost",
        "source_run_id", "source_receipt_id", "source_payload_sha256",
    }
    missing = required - set(df.columns)
    manifest = identity["manifest"]
    if missing:
        raise ValueError(f"audited income sidecar missing fields: {sorted(missing)}")
    if (
        len(df) != manifest["artifact"].get("rows")
        or list(df.columns) != manifest["artifact"].get("columns")
    ):
        raise ValueError("audited income sidecar row/schema identity mismatch")
    if df.duplicated(["ts_code", "end_date"]).any():
        raise ValueError("audited income sidecar contains duplicate report periods")
    for column in ("ann_date", "publication_date", "availability_date", "end_date"):
        df[column] = pd.to_datetime(df[column], errors="coerce")
    if df[["ann_date", "publication_date", "availability_date", "end_date"]].isna().any().any():
        raise ValueError("audited income sidecar contains invalid dates")
    cutoff = pd.to_datetime(
        str(manifest["scope"]["availability_cutoff"]),
        format="%Y%m%d",
        errors="raise",
    )
    if df["availability_date"].gt(cutoff).any():
        raise ValueError("audited income sidecar contains rows after its cutoff")
    return df.sort_values(["ts_code", "end_date"], kind="mergesort").reset_index(drop=True)


def _load_legacy_unverified_income() -> pd.DataFrame:
    """Load the pre-audit mutable income table under an explicit legacy mode."""

    path = _tushare_dir() / "income.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"Legacy unverified income data not found at {path}")
    frame = pd.read_parquet(path)
    required = {"ts_code", "ann_date", "end_date", "report_type"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"legacy unverified income table missing fields: {sorted(missing)}")
    frame = frame.loc[
        pd.to_numeric(frame["report_type"], errors="coerce").eq(1)
    ].copy()
    frame["ann_date"] = pd.to_datetime(frame["ann_date"], errors="coerce")
    frame["end_date"] = pd.to_datetime(frame["end_date"], errors="coerce")
    if frame[["ann_date", "end_date"]].isna().any().any():
        raise ValueError("legacy unverified income table contains invalid dates")
    frame["availability_date"] = frame["ann_date"]
    return (
        frame.sort_values(["ts_code", "end_date", "ann_date"], kind="mergesort")
        .drop_duplicates(["ts_code", "end_date"], keep="last")
        .reset_index(drop=True)
    )


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

def _pit_merge(
    anchor: pd.DataFrame,
    right: pd.DataFrame,
    *,
    allow_exact_matches: bool = True,
) -> pd.DataFrame:
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
            merged = pd.merge_asof(
                a,
                r_renamed,
                on="_dt",
                by="ts_code",
                direction="backward",
                allow_exact_matches=allow_exact_matches,
            )
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


def _max_available_from(*values: pd.Series) -> pd.Series:
    frame = pd.concat([pd.to_datetime(value) for value in values], axis=1)
    return frame.max(axis=1)


def _rolling_max_available_from(
    values: pd.Series,
    groups: pd.Series,
    *,
    window: int,
) -> pd.Series:
    dates = pd.to_datetime(values)
    # ``NaT.astype(int64)`` is the minimum int64 value and must never become
    # an apparently ancient availability timestamp inside a rolling maximum.
    numeric = dates.astype("int64").astype("float64").where(dates.notna())
    rolled = numeric.groupby(groups).transform(
        lambda series: series.rolling(window, min_periods=window).max()
    )
    return pd.to_datetime(rolled)


def _compute_quarterly_features(inc_raw: pd.DataFrame) -> pd.DataFrame:
    """Compute single-quarter and TTM features on the quarterly table.

    Steps:
        1. Sort by (ts_code, end_date).
        2. Convert cumulative revenue/n_income/oper_cost to single-quarter.
        3. Compute single_q_revenue_yoy, ttm_revenue_yoy, is_profitable_ttm,
           gross_margin_delta_yoy at the quarterly level.
        4. Propagate each feature's actual dependency availability separately.
    """
    inc = inc_raw.copy()
    inc["availability_date"] = pd.to_datetime(inc["availability_date"])
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
        previous_available = inc.groupby("ts_code")["availability_date"].shift(1)
        inc[f"{col}_single_q_available_from"] = inc["availability_date"]
        dependency_available = _max_available_from(
            inc["availability_date"], previous_available,
        )
        inc.loc[
            inc["end_q"].ne(1) & valid_prev,
            f"{col}_single_q_available_from",
        ] = dependency_available

    # ── Single-quarter revenue yoy ──
    inc["single_q_revenue_ly"] = inc.groupby("ts_code")["revenue_single_q"].shift(4)
    inc["single_q_revenue_yoy"] = (
        inc["revenue_single_q"] / inc["single_q_revenue_ly"].replace(0, np.nan) - 1
    )
    inc["single_q_revenue_yoy_available_from"] = _max_available_from(
        inc["availability_date"],
        inc["revenue_single_q_available_from"],
        inc.groupby("ts_code")["revenue_single_q_available_from"].shift(4),
    )

    # ── TTM revenue (last 4 single quarters) ──
    inc["ttm_revenue"] = inc.groupby("ts_code")["revenue_single_q"].transform(
        lambda s: s.rolling(4, min_periods=4).sum()
    )
    inc["ttm_revenue_lag4q"] = inc.groupby("ts_code")["ttm_revenue"].shift(4)
    inc["ttm_revenue_yoy"] = (
        inc["ttm_revenue"] / inc["ttm_revenue_lag4q"].replace(0, np.nan) - 1
    )
    inc["ttm_revenue_available_from"] = _rolling_max_available_from(
        inc["revenue_single_q_available_from"],
        inc["ts_code"],
        window=4,
    )
    inc["ttm_revenue_yoy_available_from"] = _max_available_from(
        inc["availability_date"],
        inc["ttm_revenue_available_from"],
        inc.groupby("ts_code")["ttm_revenue_available_from"].shift(4),
    )

    # ── TTM profitability ──
    inc["ttm_n_income"] = inc.groupby("ts_code")["n_income_single_q"].transform(
        lambda s: s.rolling(4, min_periods=4).sum()
    )
    inc["is_profitable_ttm"] = (inc["ttm_n_income"] > 0).astype(float)
    inc["is_profitable_ttm_available_from"] = _max_available_from(
        inc["availability_date"],
        _rolling_max_available_from(
            inc["n_income_single_q_available_from"],
            inc["ts_code"],
            window=4,
        ),
    )

    # ── Gross margin delta yoy (both revenue and cost are single-quarter) ──
    inc["single_q_gross_margin"] = (
        (inc["revenue_single_q"] - inc["oper_cost_single_q"])
        / inc["revenue_single_q"].replace(0, np.nan)
    )
    inc["single_q_gm_ly"] = inc.groupby("ts_code")["single_q_gross_margin"].shift(4)
    inc["gross_margin_delta_yoy"] = inc["single_q_gross_margin"] - inc["single_q_gm_ly"]
    inc["single_q_gross_margin_available_from"] = _max_available_from(
        inc["availability_date"],
        inc["revenue_single_q_available_from"],
        inc["oper_cost_single_q_available_from"],
    )
    inc["gross_margin_delta_yoy_available_from"] = _max_available_from(
        inc["availability_date"],
        inc["single_q_gross_margin_available_from"],
        inc.groupby("ts_code")["single_q_gross_margin_available_from"].shift(4),
    )

    # ── Keep only feature columns + ann_date anchor ──
    keep = [
        "ts_code", "ann_date", "availability_date", "end_date",
        "single_q_revenue_yoy", "ttm_revenue_yoy",
        "is_profitable_ttm", "gross_margin_delta_yoy",
    ]
    availability_columns = [
        f"{feature}_available_from"
        for feature in (
            "single_q_revenue_yoy", "ttm_revenue_yoy",
            "is_profitable_ttm", "gross_margin_delta_yoy",
        )
    ]
    for c in keep:
        if c not in inc.columns:
            inc[c] = np.nan

    for feature in (
        "single_q_revenue_yoy", "ttm_revenue_yoy",
        "is_profitable_ttm", "gross_margin_delta_yoy",
    ):
        unavailable_value = inc[feature].notna() & inc[
            f"{feature}_available_from"
        ].isna()
        if unavailable_value.any():
            raise ValueError(
                f"income feature {feature} has value without dependency availability"
            )

    return inc[keep + availability_columns]


def _latest_mature_feature_events(
    quarterly: pd.DataFrame,
    feature: str,
) -> pd.DataFrame:
    """Emit only report-period innovations for one feature availability stream."""

    available_column = f"{feature}_available_from"
    events = quarterly[
        ["ts_code", "end_date", feature, available_column]
    ].copy()
    events = events.rename(columns={available_column: "_ann_dt"})
    events = events.dropna(subset=["_ann_dt", "end_date"])
    events = events.sort_values(
        ["ts_code", "_ann_dt", "end_date"], kind="mergesort",
    )
    events = events.drop_duplicates(["ts_code", "_ann_dt"], keep="last")
    retained: list[pd.DataFrame] = []
    for _, group in events.groupby("ts_code", sort=False):
        latest_before = group["end_date"].cummax().shift(1)
        retained.append(group.loc[latest_before.isna() | group["end_date"].gt(latest_before)])
    if not retained:
        return events.iloc[0:0]
    return pd.concat(retained, ignore_index=True)


# ═══════════════════════════════════════════════════════════════════
# Main builder
# ═══════════════════════════════════════════════════════════════════

def build_growth_confirmation_features(
    df: pd.DataFrame,
    *,
    income_sidecar_path: str = "",
    income_sidecar_sha256: str = "",
    income_sidecar_manifest_path: str = "",
    income_sidecar_manifest_sha256: str = "",
    income_source_mode: str = "legacy_unverified_global_v0",
    income_sidecar_required_start: str | None = None,
    income_sidecar_required_end: str | None = None,
    income_sidecar_required_history_start: str = "",
) -> pd.DataFrame:
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
    from qsys.data.income_sidecar import (
        INCOME_SOURCE_MODE_AUDITED,
        INCOME_SOURCE_MODE_LEGACY,
        normalize_income_feature_source,
    )

    source = normalize_income_feature_source({
        "mode": income_source_mode,
        "artifact_path": income_sidecar_path,
        "artifact_sha256": income_sidecar_sha256,
        "manifest_path": income_sidecar_manifest_path,
        "manifest_sha256": income_sidecar_manifest_sha256,
        "required_history_start": income_sidecar_required_history_start,
    })
    if source["mode"] == INCOME_SOURCE_MODE_AUDITED:
        inc_raw = _load_income(
            artifact_path=source["artifact_path"],
            artifact_sha256=source["artifact_sha256"],
            manifest_path=source["manifest_path"],
            manifest_sha256=source["manifest_sha256"],
            required_start=income_sidecar_required_start,
            required_end=income_sidecar_required_end,
            required_history_start=source["required_history_start"],
            required_symbols=set(out["ts_code"].astype(str).unique()),
        )
    elif source["mode"] == INCOME_SOURCE_MODE_LEGACY:
        warnings.warn(
            "growth confirmation is using legacy_unverified_global_v0 income; "
            "this source is not eligible for audited PIT certification",
            RuntimeWarning,
            stacklevel=2,
        )
        inc_raw = _load_legacy_unverified_income()
    else:  # pragma: no cover - normalizer owns the closed mode set.
        raise ValueError(f"unsupported income source mode: {source['mode']}")
    q_feats = _compute_quarterly_features(inc_raw)

    for col in [
        "single_q_revenue_yoy", "ttm_revenue_yoy",
        "is_profitable_ttm", "gross_margin_delta_yoy",
    ]:
        events = _latest_mature_feature_events(q_feats, col)
        inc_merged = _pit_merge(
            out[["ts_code", "_dt"]],
            events[["ts_code", "_ann_dt", col]],
            allow_exact_matches=(source["mode"] == INCOME_SOURCE_MODE_LEGACY),
        )
        out[col] = pd.to_numeric(inc_merged[col], errors="coerce")

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

def pit_sanity_check(
    result: pd.DataFrame,
    n_sample: int = 20,
    *,
    income_source_mode: str = "legacy_unverified_global_v0",
) -> None:
    """Report the selected income mode's feature visibility boundary.

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
    audited = income_source_mode == "audited_sidecar_v1"
    relation = "<" if audited else "<="
    print(
        "PIT SANITY CHECK — income publication_date "
        f"{relation} feature trade_date ({income_source_mode})"
    )
    print(f"{'='*60}")

    for i, (_, r) in enumerate(sample.iterrows()):
        td = str(r["trade_date"])[:10]
        vals = {f: f"{r[f]:.4f}" if pd.notna(r.get(f)) else "N/A" for f in available}
        line = f"  {i+1:2d}. {r['ts_code']} trade_date={td} | "
        line += " | ".join(f"{f}={v}" for f, v in vals.items())
        print(line)

    print(
        "  PIT boundary: merge_asof backward with publication_date "
        f"{relation} trade_date"
    )
