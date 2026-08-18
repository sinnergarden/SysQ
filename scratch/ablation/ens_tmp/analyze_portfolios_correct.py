#!/usr/bin/env python3
"""Task 8 + Task 9 — portfolio path dispersion + winner sensitivity, on CORRECT baselines.

single = RR_{phase}_single_correct (seed-42 from the verified seed bank)
ens3/ens5 = RR_{phase}_ens3 / RR_{phase}_ens5

Task 8 (path dispersion):
  - per-phase pairwise daily-return correlation single/ens3/ens5
  - final-NAV spread + drawdown/sharpe deltas (does ensembling change the path?)
Task 9 (winner sensitivity):
  - total return with top-1/3/5 per-symbol PnL contributors removed
  - share of total PnL from the single biggest contributor (lottery concentration)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scratch/ablation"))

from diag_common import INIT_CAPITAL  # noqa: E402

OUT_DIR = ROOT / "scratch/ablation/ens_tmp/analysis"
PF_ROOT = ROOT / "data/research/ablation/ensemble_pf"
BT_ROOT = ROOT / "data/research/backtests"
EPISODE_CACHE = ROOT / "scratch/ablation/ens_tmp/analysis/episodes"


def resolve_bt_dir(d: Path) -> Path:
    if (d / "metrics.json").exists():
        return d
    sub = next(d.glob("bt_*"), None)
    if sub is not None:
        return sub
    raise FileNotFoundError(f"{d} has no metrics.json or bt_* run dir")


def single_bt_dir(phase: str) -> Path:
    d = PF_ROOT / f"RR_{phase}_single_correct"
    if (d / "metrics.json").exists():
        return d
    # P0's correct single == stored rr_p0 rawrank (verified rho 1.0); it lives
    # in the canonical backtest index, not the ablation dir.
    top = next(BT_ROOT.glob(f"*rr_{phase}__rawrank__*afdd7696"))
    sub = next(top.glob("bt_*"))
    return sub


def ens_bt_dir(phase: str, tag: str) -> Path:
    return resolve_bt_dir(PF_ROOT / f"RR_{phase}_{tag}")


def run_dir(phase: str, tag: str) -> Path:
    return single_bt_dir(phase) if tag == "single" else ens_bt_dir(phase, tag)


def load_nav(run_dir: Path) -> pd.Series:
    df = pd.read_csv(run_dir / "daily_summary.csv", usecols=["trade_date", "total_value_after"])
    nav = df.set_index(pd.to_datetime(df["trade_date"]))["total_value_after"] / INIT_CAPITAL
    return nav


def cagr(nav: pd.Series) -> float:
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    return nav.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else np.nan


def maxdd(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def sharpe(nav: pd.Series) -> float:
    r = nav.pct_change().dropna()
    if r.std() == 0 or len(r) < 2:
        return np.nan
    return float(r.mean() / r.std() * np.sqrt(252.0))


def derive_episodes_json(run_dir: Path, phase: str, tag: str) -> dict:
    EPISODE_CACHE.mkdir(parents=True, exist_ok=True)
    out = EPISODE_CACHE / f"{phase}_{tag}.json"
    if out.exists():
        return json.loads(out.read_text())
    subprocess.run(
        [sys.executable, str(ROOT / "scratch/ablation/derive_episodes.py"),
         "--run-dir", str(run_dir), "--out", str(out)],
        cwd=ROOT, check=True, capture_output=True)
    return json.loads(out.read_text())


def symbol_pnl(env: dict) -> pd.DataFrame:
    eps = env["data"]["episodes"]
    if not eps:
        return pd.DataFrame(columns=["instrument", "sum_pnl", "n_ep", "hold_td_sum"])
    rows = []
    for e in eps:
        rows.append({
            "instrument": e["symbol"],
            "pnl": float(e.get("episode_pnl") or 0.0),
            "hold_td": int(e.get("holding_days") or 0),
            "ret": float(e.get("realized_return") or 0.0),
        })
    df = pd.DataFrame(rows)
    return df.groupby("instrument").agg(
        sum_pnl=("pnl", "sum"), n_ep=("pnl", "size"), hold_td_sum=("hold_td", "sum"),
        mean_ret=("ret", "mean")).reset_index()


def portfolio_stats(phase: str, tag: str) -> dict:
    rd = run_dir(phase, tag)
    nav = load_nav(rd)
    m = json.loads((rd / "metrics.json").read_text())
    env = derive_episodes_json(rd, phase, tag)
    sym = symbol_pnl(env)
    tot_pnl = float(m["final_value"] - INIT_CAPITAL)
    out = {
        "phase": phase, "tag": tag,
        "total_ret": float(nav.iloc[-1] - 1.0),
        "cagr": cagr(nav),
        "maxdd": maxdd(nav),
        "sharpe": sharpe(nav),
        "total_pnl": tot_pnl,
        "turnover": float(m.get("turnover_total", np.nan)),
        "orders": int(m.get("order_count_total", 0)),
        "n_episodes": int(env["meta"]["total_episodes"]),
    }
    sym_sorted = sym.reindex(sym["sum_pnl"].abs().sort_values(ascending=False).index)
    for k in (1, 3, 5):
        kept = sym_sorted.iloc[k:]["sum_pnl"].sum()
        out[f"ret_excl_top{k}"] = 1.0 + kept / INIT_CAPITAL - 1.0
        out[f"pnl_share_top{k}"] = float(sym_sorted.iloc[:k]["sum_pnl"].sum()) / tot_pnl
    out["recon_gap"] = float(sym["sum_pnl"].sum() - tot_pnl)
    return out, nav


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="p0,p5,p10,p15")
    ap.add_argument("--tags", default="single,ens3,ens5")
    args = ap.parse_args()
    phases = [p.strip() for p in args.phases.split(",") if p.strip()]
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    n = {}
    rows = []
    for ph in phases:
        for tag in tags:
            try:
                r, nav = portfolio_stats(ph, tag)
            except FileNotFoundError as e:
                print(f"[skip] {ph}/{tag}: {e}", flush=True)
                continue
            n[(ph, tag)] = nav
            rows.append(r)
    df = pd.DataFrame(rows).sort_values(["phase", "tag"])
    df.to_csv(OUT_DIR / "portfolio_table_correct.csv", index=False)

    print("\n### Portfolio table (correct singles) ###")
    for ph in phases:
        sub = df[df.phase == ph].set_index("tag")
        print(f"\n-- {ph.upper()} --")
        print(sub.round(4).to_string())

    print("\n### Task 8 — portfolio path dispersion (daily returns, pairwise corr) ###")
    for ph in phases:
        navs = {t: n.get((ph, t)) for t in tags}
        navs = {t: v for t, v in navs.items() if v is not None}
        if len(navs) < 2:
            continue
        rr = pd.concat({t: v.pct_change().dropna() for t, v in navs.items()}, axis=1).dropna()
        names = list(navs)
        line = f"{ph:<5}" + "   ".join(f"{a}↔{b}: {rr[a].corr(rr[b]):.3f}" for i, a in enumerate(names) for b in names[i+1:])
        finals = {t: v.iloc[-1] - 1 for t, v in navs.items()}
        spread = max(finals.values()) - min(finals.values())
        line += f"   final spread: {spread:.1%}"
        print(line)

    print("\n### Task 9 — winner sensitivity (total return after removing top contributors) ###")
    print(f"{'phase':<5}{'tag':>7}{'total':>9}{'excl1':>9}{'excl3':>9}{'excl5':>9}{'top1_share':>11}")
    for ph in phases:
        for tag in tags:
            r = df[(df.phase == ph) & (df.tag == tag)]
            if r.empty:
                continue
            r = r.iloc[0]
            print(f"{ph:<5}{tag:>7}{r['total_ret']:>9.2%}{r['ret_excl_top1']:>9.2%}"
                  f"{r['ret_excl_top3']:>9.2%}{r['ret_excl_top5']:>9.2%}"
                  f"{r['pnl_share_top1']:>10.1%}")
    print(f"\nsaved -> {OUT_DIR}/portfolio_table_correct.csv")


if __name__ == "__main__":
    main()
