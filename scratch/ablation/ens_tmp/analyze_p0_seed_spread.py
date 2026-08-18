#!/usr/bin/env python3
"""P0 realization-lottery spread — 5 single seeds + ens3/ens5, same BASE config.

Collects CAGR / Sharpe / MaxDD / total_ret for every run and prints a table
sorted by total return, so the report's Section 6 has one canonical set of
numbers. Also prints the within-seed dispersion to quantify the lottery.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from diag_common import INIT_CAPITAL  # noqa: E402

PF = ROOT / "data/research/ablation/ensemble_pf"
BT = ROOT / "data/research/backtests"


def nav_stats(nav: pd.Series) -> tuple[float, float, float]:
    r = nav.pct_change().dropna()
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = nav.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else np.nan
    mdd = float((nav / nav.cummax() - 1.0).min())
    sh = float(r.mean() / r.std() * np.sqrt(252.0)) if r.std() > 0 else np.nan
    return cagr, mdd, sh


def load_nav(d: Path) -> pd.Series:
    df = pd.read_csv(d / "daily_summary.csv", usecols=["trade_date", "total_value_after"])
    return df.set_index(pd.to_datetime(df["trade_date"]))["total_value_after"] / INIT_CAPITAL


def p0_single_dir(seed: int) -> Path | None:
    d = PF / f"RR_p0_seed{seed}"
    if (d / "metrics.json").exists():
        return d
    return None


def p0_canonical_dir() -> Path:
    c = glob.glob(str(BT / "*rr_p0__rawrank__*afdd7696/bt_*/metrics.json"))
    return Path(c[0]).parent if c else None


def p0_ens_dir(tag: str) -> Path:
    d = PF / f"RR_p0_{tag}"
    return d if (d / "metrics.json").exists() else None


def main() -> None:
    runs = []
    # single seeds
    for seed in (7, 42, 77, 123, 456):
        d = p0_single_dir(seed)
        if d is None and seed == 42:
            d = p0_canonical_dir()
        if d is None:
            print(f"[missing] seed{seed}", flush=True)
            continue
        nav = load_nav(d)
        cagr, mdd, sh = nav_stats(nav)
        runs.append({"run": f"seed{seed}", "total_ret": nav.iloc[-1] - 1,
                     "cagr": cagr, "maxdd": mdd, "sharpe": sh})
    for tag in ("ens3", "ens5"):
        d = p0_ens_dir(tag)
        if d is None:
            print(f"[missing] {tag}", flush=True)
            continue
        nav = load_nav(d)
        cagr, mdd, sh = nav_stats(nav)
        runs.append({"run": tag, "total_ret": nav.iloc[-1] - 1,
                     "cagr": cagr, "maxdd": mdd, "sharpe": sh})

    df = pd.DataFrame(runs).sort_values("total_ret", ascending=False)
    df["total_ret_pct"] = (df["total_ret"] * 100).round(1)
    df["cagr_pct"] = (df["cagr"] * 100).round(1)
    df["maxdd_pct"] = (df["maxdd"] * 100).round(1)
    df["sharpe"] = df["sharpe"].round(2)
    print("### P0 realization-lottery spread (single seeds + ens) ###")
    print(df[["run", "total_ret_pct", "cagr_pct", "maxdd_pct", "sharpe"]].to_string(index=False))
    print()
    singles = df[df.run.str.startswith("seed")]
    print(f"single-seed total_ret: min {singles['total_ret'].min():.1%}  "
          f"median {singles['total_ret'].median():.1%}  max {singles['total_ret'].max():.1%}  "
          f"spread {singles['total_ret'].max() - singles['total_ret'].min():.1%}")
    print(f"single-seed CAGR:      median {singles['cagr'].median():.1%}  "
          f"spread {singles['cagr'].max() - singles['cagr'].min():.1%}")
    out = ROOT / "scratch/ablation/ens_tmp/analysis"
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / "p0_seed_spread.csv", index=False)
    print(f"\nsaved -> {out / 'p0_seed_spread.csv'}")


if __name__ == "__main__":
    main()
