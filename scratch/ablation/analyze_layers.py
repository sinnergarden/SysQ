#!/usr/bin/env python3
"""Layer 1-3 analysis for the A0-A5 execution-policy ablation.

Computes, for every run dir:
  Layer 1  portfolio results: Total Return / CAGR / MaxDD / Calmar / annual vol /
            turnover / fills / episodes / median holding / avg gross exposure,
            plus yearly return and yearly MaxDD (calendar year).
  Layer 2  episode behavior: win rate / avg / median return, median MFE / MAE,
            Top1 / Top5 / Top10% PnL contribution, PnL excluding Top1/5/10%,
            holding buckets 0-10 / 11-20 / 21-40 / 41-60 / 61-120 (count /
            median return / MFE / MAE).
  Layer 3  Rule Effect table (A1..A4 vs A0): deltas + verdict.

Inputs per run dir: daily_summary.csv, metrics.json, executions.csv,
and an episodes envelope JSON (from derive_episodes.py).
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path

import pandas as pd


# --------------------------------------------------------------------------
# Layer 1 helpers
# --------------------------------------------------------------------------

def _cagr(total_return: float, n_days: int) -> float | None:
    if n_days <= 0:
        return None
    years = n_days / 252.0
    if years <= 0:
        return None
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def portfolio_metrics(run_dir: Path) -> dict:
    daily = pd.read_csv(run_dir / "daily_summary.csv", dtype={"trade_date": str})
    metrics = json.loads((run_dir / "metrics.json").read_text())
    daily = daily.sort_values("trade_date").reset_index(drop=True)

    eq = daily["total_value_after"].astype(float).to_numpy()
    init = float(metrics["initial_capital"])
    n = len(eq)
    total_return = float(metrics["total_return"])

    peak = eq[0]
    max_dd = 0.0
    for v in eq[1:]:
        if v > peak:
            peak = v
        dd = v / peak - 1.0
        if dd < max_dd:
            max_dd = dd

    cagr = _cagr(total_return, n)
    rets = eq[1:] / eq[:-1] - 1.0
    ann_vol = float(rets.std(ddof=1) * math.sqrt(252)) if len(rets) > 1 else None

    # Gross exposure: market value / total equity per day.
    exposure = daily["market_value_after"].astype(float) / eq
    avg_exposure = float(exposure.mean())

    # Yearly return / MaxDD by calendar year.
    daily["year"] = daily["trade_date"].str[:4]
    yearly = {}
    for yr, grp in daily.groupby("year"):
        y = grp.sort_values("trade_date")
        eq_y = y["total_value_after"].astype(float).to_numpy()
        year_ret = eq_y[-1] / eq_y[0] - 1.0
        p = eq_y[0]
        ydd = 0.0
        for v in eq_y[1:]:
            p = max(p, v)
            ydd = min(ydd, v / p - 1.0)
        yearly[yr] = {"return": year_ret, "maxdd": ydd, "n_days": len(eq_y)}

    turn = float(metrics["turnover_total"])
    fills = int(metrics["filled_count_total"])

    execs = pd.read_csv(run_dir / "executions.csv", dtype={"trade_date": str})
    sells = execs[execs["side"] == "sell"] if "side" in execs.columns else execs.iloc[0:0]
    buys = execs[execs["side"] == "buy"] if "side" in execs.columns else execs.iloc[0:0]

    return {
        "total_return": total_return,
        "final_value": float(metrics["final_value"]),
        "cagr": cagr,
        "maxdd": max_dd,
        "calmar": (cagr / abs(max_dd)) if (cagr is not None and max_dd != 0) else None,
        "ann_vol": ann_vol,
        "turnover": turn,
        "fills": fills,
        "sells": int(len(sells)),
        "buys": int(len(buys)),
        "avg_exposure": avg_exposure,
        "trading_days": n,
        "yearly": yearly,
    }


# --------------------------------------------------------------------------
# Layer 2 helpers
# --------------------------------------------------------------------------

def _closed(eps: list[dict]) -> list[dict]:
    return [e for e in eps if e.get("exit_reason") != "open"]


def _median(xs: list[float | None]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return st.median(xs) if xs else None


def _mean(xs: list[float | None]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(xs) / len(xs) if xs else None


def _pos_frac(xs: list[float | None]) -> float | None:
    xs = [x for x in xs if x is not None and math.isfinite(x)]
    return sum(1 for x in xs if x > 0) / len(xs) if xs else None


def episode_behavior(eps_env: dict) -> dict:
    eps = eps_env["data"]["episodes"]
    cl = _closed(eps)
    rr = [e.get("realized_return") for e in cl]

    # PnL concentration: sum of realized_return as proxy PnL (equal entry weight
    # makes per-episode return a valid unit-PnL measure).
    pnl = sorted((e.get("realized_return") or 0.0) for e in cl)
    total_pnl = sum(pnl)
    n = len(pnl)

    def top_k_share(k: int) -> float | None:
        if n == 0 or total_pnl == 0:
            return None
        return sum(pnl[-k:]) / total_pnl if k < n else 1.0

    def pnl_excluding(k: int) -> float | None:
        return sum(pnl[:-k]) if k < n else (total_pnl if n == 0 else 0.0)

    top10_n = max(int(round(n * 0.10)), 1) if n else 0

    mfe = [e.get("MFE") for e in cl]
    mae = [e.get("MAE") for e in cl]

    # Holding buckets: 0-10 / 11-20 / 21-40 / 41-60 / 61-120
    buckets = {
        "0-10d": lambda h: 0 <= h <= 10,
        "11-20d": lambda h: 11 <= h <= 20,
        "21-40d": lambda h: 21 <= h <= 40,
        "41-60d": lambda h: 41 <= h <= 60,
        "61-120d": lambda h: 61 <= h <= 120,
    }
    bucket_out = {}
    for name, f in buckets.items():
        g = [e for e in cl if e.get("holding_days") is not None and f(e["holding_days"])]
        gr = [e.get("realized_return") for e in g]
        gmfe = [e.get("MFE") for e in g]
        gmae = [e.get("MAE") for e in g]
        bucket_out[name] = {
            "n": len(g),
            "median_return": _median(gr),
            "mean_return": _mean(gr),
            "win_rate": _pos_frac(gr),
            "median_MFE": _median(gmfe),
            "median_MAE": _median(gmae),
        }

    holding = [e.get("holding_days") for e in cl if e.get("holding_days") is not None]

    # 41d+ "survivor" winners: median realized return of episodes held > 40d.
    surv = [e.get("realized_return") for e in cl if (e.get("holding_days") or 0) > 40]
    survivor_41d_median = _median(surv)

    return {
        "n_episodes": len(eps),
        "n_closed": len(cl),
        "n_open": len(eps) - len(cl),
        "win_rate": _pos_frac(rr),
        "avg_return": _mean(rr),
        "median_return": _median(rr),
        "median_holding": _median(holding),
        "avg_holding": _mean(holding),
        "median_MFE": _median(mfe),
        "median_MAE": _median(mae),
        "mean_MAE": _mean(mae),
        "survivor_41d_median": survivor_41d_median,
        "survivor_41d_count": len(surv),
        "total_pnl": total_pnl,
        "top1_share": top_k_share(1),
        "top5_share": top_k_share(5),
        "top10pct_share": top_k_share(top10_n),
        "top10pct_n": top10_n,
        "pnl_excl_top1": pnl_excluding(1),
        "pnl_excl_top5": pnl_excluding(5),
        "pnl_excl_top10pct": pnl_excluding(top10_n),
        "holding_buckets": bucket_out,
        "exit_reason_counts": {
            k: sum(1 for e in cl if e.get("exit_reason") == k)
            for k in sorted({e.get("exit_reason") for e in cl})
        },
    }


# --------------------------------------------------------------------------
# Layer 3 rule-effect table
# --------------------------------------------------------------------------

VERDICTS = ["likely helpful", "likely harmful", "mixed", "insufficient sample"]

def rule_effect_row(base: dict, rule: dict) -> dict:
    d = {}
    for metric in ["cagr", "maxdd", "calmar", "turnover", "median_return",
                   "top10pct_share", "survivor_41d_median", "avg_exposure"]:
        b = base.get(metric)
        r = rule.get(metric)
        d[f"delta_{metric}"] = (r - b) if (b is not None and r is not None) else None
    # Verdict heuristic (documented; no threshold tuning).
    score = 0
    if d["delta_cagr"] is not None:
        score += 1 if d["delta_cagr"] > 0 else -1
    if d["delta_maxdd"] is not None:
        score += 1 if d["delta_maxdd"] > 0 else -1  # maxdd is negative; higher=better
    if d["delta_calmar"] is not None:
        score += 1 if d["delta_calmar"] > 0 else -1
    if d["delta_median_return"] is not None:
        score += 1 if d["delta_median_return"] > 0 else -1
    if d["delta_top10pct_share"] is not None:
        # Lower top-10% concentration is usually better diversification of winners
        score += 1 if d["delta_top10pct_share"] < 0 else -1
    if rule.get("n_rule_events", 0) < 10:
        verdict = "insufficient sample"
    elif score >= 2:
        verdict = "likely helpful"
    elif score <= -2:
        verdict = "likely harmful"
    else:
        verdict = "mixed"
    d["verdict"] = verdict
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-root", required=True)
    ap.add_argument("--episodes-root", required=True, help="dir holding <run>.json episode envelopes")
    ap.add_argument("--out", required=True)
    ap.add_argument("--runs", default="A0_none,A1_hard_stop,A2_score_delta,A3_winner_trailing,A4_stale,A5_all")
    args = ap.parse_args()

    runs_root = Path(args.runs_root)
    ep_root = Path(args.episodes_root)
    names = [s.strip() for s in args.runs.split(",") if s.strip()]

    layer1 = {}
    layer2 = {}
    for name in names:
        rd = runs_root / name
        if not (rd / "metrics.json").exists():
            print(f"!! missing run dir: {rd}", flush=True)
            continue
        layer1[name] = portfolio_metrics(rd)
        ep_env = json.loads((ep_root / f"{name}.json").read_text())
        layer2[name] = episode_behavior(ep_env)
        print(f"[analyzed] {name}: total_return={layer1[name]['total_return']:+.1%} "
              f"cagr={layer1[name]['cagr']:+.1%} maxdd={layer1[name]['maxdd']:.1%} "
              f"episodes={layer2[name]['n_closed']}", flush=True)

    # Layer 3: A1..A4 vs A0.
    l3 = {}
    if "A0_none" in layer1:
        base = {**layer1["A0_none"], **layer2["A0_none"]}
        for name in ["A1_hard_stop", "A2_score_delta", "A3_winner_trailing", "A4_stale"]:
            if name not in layer1:
                continue
            rule = {**layer1[name], **layer2[name]}
            n_events = sum(layer2[name].get("exit_reason_counts", {}).values())
            rule["n_rule_events"] = n_events
            row = rule_effect_row(base, rule)
            row["n_rule_events"] = n_events
            l3[name] = row

    out = {
        "layer1": layer1,
        "layer2": layer2,
        "layer3": l3,
    }
    Path(args.out).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"-> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
