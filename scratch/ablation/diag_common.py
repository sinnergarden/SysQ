#!/usr/bin/env python3
"""Shared loaders for the E1 alpha-diagnostics (P0 conventions + 6 tracks).

All data is read from the MAIN SysQ repo
(/home/liuming/.openclaw/workspace/SysQ) — run from that cwd so qsys + data
resolve.  Scored universe is exactly the instruments present in the blend
predictions.parquet panel (800 names, ~780/day).

P0.1 yearly-return convention (fixed here, single source of truth):
    2021  = NAV(2021-12-31) / initial_capital - 1
    2022+ = NAV(year_end)   / NAV(prev_year_end) - 1
    (year_end = last backtest trading day in the calendar year; 2026 reported
     only through 2026-07-31, explicitly labeled partial.)

P0.2 multi-swap day flags + day-level basket metrics live in analyze_layer4.py;
this module only provides the shared inputs.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

MAIN = Path("/home/liuming/.openclaw/workspace/SysQ")
EXEC_ROOT = Path(
    "/home/liuming/.openclaw/workspace/SysQ-execution-ledger/data/research/ablation/execution_policy"
)

SIGNAL_ID = "financial_rc_60d_180d_50_50__daily_zscore"
SIGNAL_RUN_ID = "blend__007a93600f45de00"
PRED_PATH = (
    MAIN
    / "data/research/signals"
    / SIGNAL_ID
    / SIGNAL_RUN_ID
    / "predictions.parquet"
)

INIT_CAPITAL = 10_000_000.0
START_DATE = pd.Timestamp("2021-01-04")
END_DATE = pd.Timestamp("2026-07-31")

PRICE_CACHE = Path("/tmp/diag_price_cache.parquet")


# --------------------------------------------------------------------------
# NAV / benchmark / calendar
# --------------------------------------------------------------------------

def load_nav(run_dir: Path) -> pd.Series:
    """Return Series(trade_date -> total_value_after / initial_capital)."""
    daily = pd.read_csv(run_dir / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    init = INIT_CAPITAL
    nav = pd.Series(
        daily["total_value_after"].astype(float).values / init,
        index=pd.DatetimeIndex(daily["trade_date"]),
        name="nav",
    )
    nav.index.name = "trade_date"
    return nav


def load_benchmark(index_code: str = "000906.SH", window: bool = True) -> pd.Series:
    """Close series of an index from data/raw/index/<code>.csv (YYYYMMDD dates).

    window=True clips to [START_DATE, END_DATE]; window=False returns the full
    history (needed for calendar-year benchmark returns anchored at the prior
    year end).
    """
    path = MAIN / "data/raw/index" / f"{index_code}.csv"
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
    s = pd.Series(df["close"].astype(float).values, index=pd.DatetimeIndex(df["date"]))
    s.index.name = "trade_date"
    s = s.sort_index()
    if window:
        return s[(s.index >= START_DATE) & (s.index <= END_DATE)]
    return s


def load_rebalance_dates(run_dir: Path) -> list[pd.Timestamp]:
    """Weekly refresh dates from daily_summary (policy_entry_count > 0)."""
    daily = pd.read_csv(run_dir / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    reb = daily.loc[daily["policy_entry_count"] > 0, "trade_date"].tolist()
    return [pd.Timestamp(d) for d in reb]


def load_trading_dates(run_dir: Path) -> list[pd.Timestamp]:
    daily = pd.read_csv(run_dir / "daily_summary.csv")
    return [pd.Timestamp(d) for d in daily["trade_date"]]


def trading_days_between(start: pd.Timestamp, end: pd.Timestamp, run_dir: Path) -> int:
    dates = load_trading_dates(run_dir)
    return sum(1 for d in dates if start <= d <= end)


# --------------------------------------------------------------------------
# Score panel + prices
# --------------------------------------------------------------------------

def load_score_panel() -> pd.DataFrame:
    """Long panel trade_date x instrument x score (all NaN-free)."""
    df = pd.read_parquet(PRED_PATH)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df[["trade_date", "instrument", "score"]]


def _load_price_matrix() -> pd.DataFrame:
    """Wide matrix trade_date x instrument of raw close, scored universe only.

    Cached as a parquet in MAIN/data/research/ablation/ so the six tracks do
    not re-read 800 feather files each run.
    """
    if PRICE_CACHE.exists():
        mat = pd.read_parquet(PRICE_CACHE)
        mat.index = pd.to_datetime(mat.index)
        return mat

    panel = load_score_panel()
    insts = sorted(panel["instrument"].unique())
    rows = []
    for i, code in enumerate(insts):
        f = MAIN / "data/canonical/daily" / f"{code}.feather"
        if not f.exists():
            continue
        d = pd.read_feather(f, columns=["ts_code", "trade_date", "close"])
        d["trade_date"] = pd.to_datetime(d["trade_date"].astype(str), format="%Y%m%d")
        d = d.rename(columns={"ts_code": "instrument"})
        rows.append(d[["trade_date", "instrument", "close"]])
    allc = pd.concat(rows, ignore_index=True)
    # Keep data past END_DATE (canonical feathers extend a few weeks beyond the
    # backtest window) so late-2026 rebalance snapshots still get forward
    # returns; each instrument's own data bounds remain respected.
    allc = allc[allc["trade_date"] >= START_DATE]
    mat = allc.pivot(index="trade_date", columns="instrument", values="close")
    mat = mat.sort_index()
    mat.to_parquet(PRICE_CACHE)
    return mat


def load_close_matrix() -> pd.DataFrame:
    return _load_price_matrix()


def load_industry() -> pd.Series:
    """instrument -> industry from meta.db stock_basic."""
    import sqlite3

    conn = sqlite3.connect(MAIN / "data/meta.db")
    df = pd.read_sql("SELECT ts_code, industry FROM stock_basic", conn)
    conn.close()
    df["ts_code"] = df["ts_code"].astype(str)
    return pd.Series(df["industry"].values, index=pd.Index(df["ts_code"]))


# --------------------------------------------------------------------------
# Forward returns (strict close, point-in-time)
# --------------------------------------------------------------------------

def forward_return(
    close_mat: pd.DataFrame,
    at: pd.Timestamp,
    horizon_days: int,
    instruments: list[str],
) -> pd.Series:
    """Equal-weight-able forward close-to-close return over `horizon_days`
    TRADING days starting from the close of `at`'s row.

    Uses the backtest's own trading calendar (the close matrix rows), so
    horizon_days means the row at offset `horizon_days` from the current row.
    Returns NaN where either endpoint close is missing (strict).
    """
    idx = close_mat.index
    if at not in idx:
        return pd.Series(np.nan, index=pd.Index(instruments))
    pos = idx.get_loc(at)
    j = pos + horizon_days
    if j >= len(idx):
        return pd.Series(np.nan, index=pd.Index(instruments))
    t0 = close_mat.iloc[pos]
    t1 = close_mat.iloc[j]
    out = t1 / t0 - 1.0
    out = out.reindex(instruments)
    return out.astype(float)


# --------------------------------------------------------------------------
# P0.1 yearly returns (new convention)
# --------------------------------------------------------------------------

def yearly_returns(nav: pd.Series, init_capital: float = INIT_CAPITAL) -> dict:
    """Calendar-year returns with the fixed convention.

    `nav` is already normalized (total_value_after / initial_capital), so the
    initial denominator in NAV units is 1.0.

    Returns {year_str: {"return": float, "n_days": int, "note": str|None}}.
    2026 is a partial year through 2026-07-31 and is labeled as such.
    """
    prev_end_nav = 1.0  # normalized initial capital
    years = sorted({d.year for d in nav.index})
    out = {}
    for yr in years:
        seg = nav[nav.index.year == yr]
        if len(seg) == 0:
            continue
        end_nav = float(seg.iloc[-1])
        ret = end_nav / prev_end_nav - 1.0
        note = None
        if yr == 2026:
            note = "partial: through 2026-07-31"
        out[str(yr)] = {"return": ret, "n_days": int(len(seg)), "note": note}
        prev_end_nav = end_nav
    return out


def active_stats_from_nav(
    nav: pd.Series, bench: pd.Series
) -> pd.DataFrame:
    """Join strategy NAV and benchmark close on common dates -> daily returns.

    Returns DataFrame indexed by trade_date with columns
    strat_ret, bench_ret, active_ret (= strat - bench, daily).
    """
    df = pd.DataFrame({"nav": nav, "bench": bench}).dropna()
    df = df.sort_index()
    df["strat_ret"] = df["nav"].pct_change()
    df["bench_ret"] = df["bench"].pct_change()
    df["active_ret"] = df["strat_ret"] - df["bench_ret"]
    return df.dropna()


# --------------------------------------------------------------------------
# Universe equal-weight benchmark + weekly snapshot panel (tracks 3/4/5)
# --------------------------------------------------------------------------

def universe_benchmark(
    close_mat: pd.DataFrame, score_panel: pd.DataFrame
) -> pd.Series:
    """Equal-weight of the scored universe, daily-rebalanced, compounded.

    Point-in-time membership: only instruments actually scored on that day
    enter that day's mean (no lookahead from instruments added to the scoring
    universe later).

    = average stock in the model's own universe; the diversification baseline
    a top-5 strategy is being compared against.
    """
    scored = (
        score_panel.assign(v=1.0)
        .pivot(index="trade_date", columns="instrument", values="v")
        .reindex(index=close_mat.index, columns=close_mat.columns)
    )
    scored = scored.astype(float)
    daily_ret = close_mat / close_mat.shift(1) - 1.0
    ew = daily_ret.where(scored == 1.0).mean(axis=1, skipna=True).fillna(0.0)
    return (1.0 + ew).cumprod()


def weekly_snapshots(
    run_dir: Path,
    close_mat: pd.DataFrame,
    score_panel: pd.DataFrame,
    bench_close: pd.Series,
) -> pd.DataFrame:
    """Per-weekly-rebalance-date snapshot panel.

    Rows = E1 weekly refresh dates.  Columns:
      year, top5_mean_score, top5_min_score, score_5_minus_6,
      top5_mean_minus_universe_median, cross_section_score_std,
      fwd_top5_{20,60,180}, fwd_univ_{20,60,180}, top5_excess_{20,60,180},
      rankic_{20,60,180}, bench_120d_ret, breadth_20d.

    Forward returns are close-to-close over horizon trading rows (NaN where the
    horizon-end close is missing).  RankIC = Spearman(score, fwd) over the
    scored universe.  bench_120d_ret = CSI800 trailing 120 trading rows.
    breadth_20d = fraction of the scored universe with positive trailing 20d
    return.  All inputs point-in-time at the snapshot date.
    """
    reb = load_rebalance_dates(run_dir)
    panel_rows = []

    # Bench reindexed onto the close-matrix calendar for row-offset lookups.
    bench = bench_close.reindex(close_mat.index)
    idx = close_mat.index

    for t in reb:
        pos = idx.get_loc(t)
        row = {"trade_date": t, "year": int(t.year)}

        sc = score_panel.loc[score_panel["trade_date"] == t, ["instrument", "score"]]
        sc = sc.dropna(subset=["score"]).sort_values("score", ascending=False)
        if len(sc) == 0:
            continue
        scores = sc["score"].to_numpy()
        top5 = sc.head(5)["instrument"].tolist()

        row["top5_mean_score"] = float(scores[:5].mean())
        row["top5_min_score"] = float(scores[4]) if len(scores) >= 5 else None
        row["score_5_minus_6"] = (
            float(scores[4] - scores[5]) if len(scores) >= 6 else None
        )
        row["top5_mean_minus_universe_median"] = float(
            scores[:5].mean() - float(np.median(scores))
        )
        row["cross_section_score_std"] = float(np.std(scores, ddof=1))

        # Forward returns and RankIC per horizon.
        for h in (20, 60, 180):
            fwd = forward_return(close_mat, t, h, sc["instrument"].tolist())
            fwd = fwd.rename("fwd").to_frame()
            fwd["score"] = sc.set_index("instrument")["score"]
            fwd = fwd.dropna(subset=["fwd"])
            if len(fwd) == 0:
                row[f"fwd_top5_{h}"], row[f"fwd_univ_{h}"], row[f"rankic_{h}"] = None, None, None
                row[f"top5_excess_{h}"] = None
                continue
            top5_fwd = float(fwd.loc[fwd.index.isin(_top5_set(sc)), "fwd"].mean())
            univ_fwd = float(fwd["fwd"].mean())
            rankic = fwd[["score", "fwd"]].corr(method="spearman").iloc[0, 1]
            row[f"fwd_top5_{h}"] = top5_fwd
            row[f"fwd_univ_{h}"] = univ_fwd
            row[f"top5_excess_{h}"] = top5_fwd - univ_fwd
            row[f"rankic_{h}"] = float(rankic) if np.isfinite(rankic) else None

        # Regime (trailing, point-in-time).
        if pos >= 120:
            row["bench_120d_ret"] = float(bench.iloc[pos] / bench.iloc[pos - 120] - 1.0)
        else:
            row["bench_120d_ret"] = None
        if pos >= 20:
            trailing = close_mat.iloc[pos] / close_mat.iloc[pos - 20] - 1.0
            row["breadth_20d"] = float((trailing > 0).sum() / trailing.notna().sum())
        else:
            row["breadth_20d"] = None

        panel_rows.append(row)

    return pd.DataFrame(panel_rows).set_index("trade_date").sort_index()


def _top5_set(sc: pd.DataFrame) -> set:
    return set(sc.head(5)["instrument"].tolist())


if __name__ == "__main__":
    # quick self-check
    e1 = EXEC_ROOT / "E1_rank_exit"
    nav = load_nav(e1)
    yr = yearly_returns(nav)
    for k, v in yr.items():
        print(k, f"{v['return']:+.4f}", v["n_days"], v.get("note") or "")
    b = load_benchmark("000906.SH")
    print("benchmark rows:", len(b), b.index.min().date(), "->", b.index.max().date())
    sc = load_score_panel()
    print("score panel:", sc.shape)
    cm = load_close_matrix()
    print("close matrix:", cm.shape, cm.index.min().date(), "->", cm.index.max().date())
