#!/usr/bin/env python3
"""Track 1 — Alpha/Beta Attribution for E1 vs CSI800 (primary), CSI300
(secondary) and the universe equal-weight benchmark.

Yearly: strategy return, benchmark return, active return, beta, residual
return (compounded strategy - beta*benchmark daily), MaxDD, active MaxDD.
Rolling 60/120/250d: per-day rolling beta / active return / residual return,
summarized over the sample (median, quantiles, % positive).

Key question the track answers: did E1 lose money in 2021/2022 while still
beating the benchmark (beta-driven loss with positive alpha)?
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from diag_common import (
    EXEC_ROOT,
    active_stats_from_nav,
    load_benchmark,
    load_close_matrix,
    load_nav,
    load_score_panel,
    universe_benchmark,
    yearly_returns,
)

HORIZONS = (60, 120, 250)


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.{nd}f}%"


def _beta(x: np.ndarray, y: np.ndarray) -> float:
    """beta of y on x (x=benchmark daily, y=strategy daily)."""
    if len(x) < 3:
        return np.nan
    cov = np.cov(x, y, ddof=1)
    if cov[0, 0] == 0:
        return np.nan
    return float(cov[0, 1] / cov[0, 0])


def _compounded(rets: pd.Series) -> float:
    return float((1.0 + rets.fillna(0.0)).prod() - 1.0)


def _max_dd(nav_cum: pd.Series) -> float:
    c = nav_cum.values
    if len(c) == 0:
        return np.nan
    peak = c[0]
    dd = 0.0
    for v in c:
        if v > peak:
            peak = v
        dd = min(dd, v / peak - 1.0)
    return float(dd)


def bench_calendar_yearly(bench_close_full: pd.Series) -> dict[int, float]:
    """Calendar-year benchmark returns anchored at prior year-end close.

    2021 uses the last close before 2021-01-04 (2020-12-31) as base, matching
    the strategy's P0.1 'initial capital' anchor.
    """
    s = bench_close_full.sort_index()
    out: dict[int, float] = {}
    years = sorted({d.year for d in s.index})
    # base for the first year = last close strictly before the first in-sample date
    base = float(s.iloc[0])
    for yr in years:
        seg = s[s.index.year == yr]
        if len(seg) == 0:
            continue
        out[yr] = float(seg.iloc[-1]) / base - 1.0
        base = float(seg.iloc[-1])
    return out


def _universe_yearly(ew: pd.Series) -> dict[str, float]:
    """Calendar-year returns of the universe EW series (anchored at its own
    first-day close = 1.0, since no prior year-end exists)."""
    out = {}
    for yr, g in ew.groupby(ew.index.year):
        out[str(yr)] = float(g.iloc[-1]) / float(g.iloc[0]) - 1.0
    return out


def yearly_table(
    df: pd.DataFrame,
    bench_name: str,
    strat_yearly: dict[int, float],
    bench_yearly: dict[int, float],
) -> list[dict]:
    """Yearly table with P0.1 NAV-anchored strategy returns and calendar-year
    benchmark returns.  Beta / residual are daily-regression based; active
    return is the compounded ratio (1+strat)/(1+bench)-1 so the table is
    internally consistent with the fixed convention.
    """
    rows = []
    for yr, g in df.groupby(df.index.year):
        sr = g["strat_ret"]
        br = g["bench_ret"]
        ar = g["active_ret"]
        b = _beta(br.to_numpy(), sr.to_numpy())
        resid = sr - b * br
        resid_ret = _compounded(resid)
        active_nav = (1.0 + ar).cumprod()
        strat_ret = strat_yearly.get(int(yr))
        bench_ret = bench_yearly.get(int(yr))
        active_ret = (
            (1.0 + strat_ret) / (1.0 + bench_ret) - 1.0
            if (strat_ret is not None and bench_ret is not None)
            else None
        )
        out = {
            "year": int(yr),
            "n_days": int(len(g)),
            "strat_return": strat_ret,
            f"{bench_name}_return": bench_ret,
            "active_return": active_ret,
            "beta": b,
            "residual_return": resid_ret,
            "maxdd": _max_dd((1.0 + sr).cumprod()),
            "active_maxdd": _max_dd(active_nav),
        }
        rows.append(out)
    return rows


def rolling_summary(df: pd.DataFrame) -> dict:
    """Per-horizon rolling beta / active / residual, summarized."""
    out = {}
    sr, br, ar = df["strat_ret"], df["bench_ret"], df["active_ret"]
    for w in HORIZONS:
        betas = []
        active = []
        resid = []
        for i in range(w, len(df)):
            b = _beta(br.iloc[i - w:i].to_numpy(), sr.iloc[i - w:i].to_numpy())
            betas.append(b)
            resid_daily = sr.iloc[i - w:i] - b * br.iloc[i - w:i]
            active.append(_compounded(ar.iloc[i - w:i]))
            resid.append(_compounded(resid_daily))
        b = np.array(betas)
        a = np.array(active)
        r = np.array(resid)
        out[str(w)] = {
            "n_windows": int(len(b)),
            "beta_median": float(np.nanmedian(b)),
            "beta_q10": float(np.nanpercentile(b, 10)),
            "beta_q90": float(np.nanpercentile(b, 90)),
            "active_median": float(np.nanmedian(a)),
            "active_p10": float(np.nanpercentile(a, 10)),
            "active_pos_frac": float(np.mean(a > 0)),
            "residual_median": float(np.nanmedian(r)),
            "residual_p10": float(np.nanpercentile(r, 10)),
        }
    return out


def main() -> int:
    e1 = EXEC_ROOT / "E1_rank_exit"
    nav = load_nav(e1)
    cm = load_close_matrix()
    sp = load_score_panel()
    strat_yearly = {
        int(k): v["return"] for k, v in yearly_returns(nav).items()
    }

    universe_ew = universe_benchmark(cm, sp)
    benchs = {
        "CSI800": (load_benchmark("000906.SH"), load_benchmark("000906.SH", window=False)),
        "CSI300": (load_benchmark("000300.SH"), load_benchmark("000300.SH", window=False)),
        "universe_ew": (universe_ew, universe_ew),
    }

    out = {"benchmarks": {}, "rolling": {}}
    print("=" * 96)
    print("Track 1 — Alpha/Beta Attribution (E1 pure score refresh)")
    print("=" * 96)
    for name, (bench, bench_full) in benchs.items():
        df = active_stats_from_nav(nav, bench)
        if name == "universe_ew":
            # universe EW series is anchored at 1.0 on its first day (no prior
            # year-end close exists) -> calendar returns from first day.
            bench_yearly = {int(k): v for k, v in _universe_yearly(bench_full).items()}
        else:
            bench_yearly = bench_calendar_yearly(bench_full)
        yt = yearly_table(df, name, strat_yearly, bench_yearly)
        out["benchmarks"][name] = {"yearly": yt, "rolling": rolling_summary(df)}

        print(f"\n--- {name} ---")
        print("year | strat  bench  active  beta  resid  maxdd  actMaxDD")
        for r in yt:
            print(
                f"{r['year']} | {_pct(r['strat_return'])} {_pct(r[f'{name}_return'])} "
                f"{_pct(r['active_return'])} {r['beta']:.2f} {_pct(r['residual_return'])} "
                f"{_pct(r['maxdd'])} {_pct(r['active_maxdd'])}"
            )
        rl = out["benchmarks"][name]["rolling"]
        print("rolling:")
        for w, s in rl.items():
            print(
                f"  {w}d n={s['n_windows']} beta_med={s['beta_median']:.2f} "
                f"active_med={_pct(s['active_median'])} pos={s['active_pos_frac']:.0%} "
                f"resid_med={_pct(s['residual_median'])}"
            )

    Path("/tmp/diag_track1.json").write_text(json.dumps(out, indent=1, default=float))
    print("\n-> /tmp/diag_track1.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
