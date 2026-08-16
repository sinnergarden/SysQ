#!/usr/bin/env python3
"""Structural comparison for the S180 cadence + rank-hysteresis experiments.

Groups (selected with --group):
  A  S180 cadence robustness : S180_20d / A_S180_60d / S180_60d_off20 /
                               S180_60d_off40 / S180_180d
  B  S180 rank-hysteresis   : A_S180_60d (fixed 60d cadence) vs
                               S180_band_weekly (weekly eval, Top5 entry,
                               keep while rank <= 10, exit > 10, refill Top5,
                               exactly 5 holdings, hold drift, dead rules)
  G  Corrected gates        : E1_refresh_60d / G1 / G2 / G3 (blend) +
                               A_S180_60d / G1_S180 / G2_S180

Per-run outputs (full-sample + yearly, P0.1 conventions):
  CAGR, MaxDD, yearly return, active return vs CSI800, turnover, orders,
  per-instrument realized PnL reconstruction (avg-cost model replicating
  Account.update_after_deal: fees NOT in avg_cost; sell realized =
  (deal - avg_cost)*qty - fee), Top1 & Top5 PnL concentration (% of total NAV
  gain), and total return EXCLUDING the largest winner.

  PnL of instrument X = net cash flow from X's trades + final marked position
  (== realized + unrealized under the avg-cost model).  The without-X NAV is
  final_equity - pnl_X, exact: removing X's trades removes exactly its net cash
  contribution and its marked position.

Run from the MAIN SysQ cwd (data + qsys resolve).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scratch.ablation.diag_common import (  # noqa: E402
    EXEC_ROOT,
    INIT_CAPITAL,
    load_benchmark,
    load_close_matrix,
    load_nav,
)

GROUP_RUNS = {
    "A": ["S180_20d", "A_S180_60d", "S180_60d_off20", "S180_60d_off40", "S180_180d"],
    "B": ["A_S180_60d", "S180_band_weekly"],
    "G": [
        "E1_refresh_60d", "G1_market_risk", "G2_model_health", "G3_either",
        "A_S180_60d", "G1_S180_market_risk", "G2_S180_model_health",
    ],
}


def _cagr(nav: pd.Series) -> float:
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    return nav.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 else np.nan


def _maxdd(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def _yearly(nav: pd.Series) -> pd.Series:
    years = sorted({d.year for d in nav.index})
    out = {}
    for y in years:
        end = nav[nav.index.year == y].iloc[-1]
        base = 1.0 if y == years[0] else nav[nav.index.year == y - 1].iloc[-1]
        out[y] = end / base - 1.0
    return pd.Series(out)


def reconstruct_per_instrument_pnl(
    run_dir: Path, close_mat: pd.DataFrame, final_date: pd.Timestamp
) -> pd.DataFrame:
    """Per-instrument PnL from executions.csv (avg-cost, mirrors Account).

    sorted by (trade_date, sequence) so sell fees and avg-cost use the same
    processing order as the engine.  status=='filled' only.
    """
    execs = pd.read_csv(run_dir / "executions.csv")
    execs = execs[execs["status"] == "filled"]
    execs = execs.sort_values(["trade_date", "sequence"])
    qty: dict[str, float] = {}
    avg: dict[str, float] = {}
    realized: dict[str, float] = {}
    for r in execs.itertuples(index=False):
        inst = r.instrument
        q = float(r.filled_qty)
        price = float(r.deal_price)
        fee = float(r.total_fee)
        if r.side == "buy":
            old_q, old_a = qty.get(inst, 0.0), avg.get(inst, 0.0)
            new_q = old_q + q
            avg[inst] = (old_q * old_a + q * price) / new_q
            qty[inst] = new_q
        else:  # sell
            old_a = avg.get(inst, 0.0)
            realized[inst] = realized.get(inst, 0.0) + (price - old_a) * q - fee
            qty[inst] = qty.get(inst, 0.0) - q

    rows = []
    for inst in sorted(set(qty) | set(realized)):
        q = qty.get(inst, 0.0)
        a = avg.get(inst, 0.0)
        unreal = 0.0
        if q > 0 and inst in close_mat.columns:
            s = close_mat[inst].reindex(close_mat.index[close_mat.index <= final_date])
            s = s.dropna()
            if len(s):
                close_final = float(s.iloc[-1])
                unreal = q * close_final - q * a
        rows.append({
            "instrument": inst,
            "realized": realized.get(inst, 0.0),
            "unrealized": unreal,
            "pnl": realized.get(inst, 0.0) + unreal,
        })
    df = pd.DataFrame(rows).sort_values("pnl", ascending=False).reset_index(drop=True)
    return df


def _concentration(run_dir: Path, close_mat: pd.DataFrame) -> dict:
    daily = pd.read_csv(run_dir / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    final_date = pd.Timestamp(daily["trade_date"].iloc[-1])
    final_equity = float(daily["total_value_after"].iloc[-1])
    total_gain = final_equity - INIT_CAPITAL
    pnl = reconstruct_per_instrument_pnl(run_dir, close_mat, final_date)
    sum_pnl = float(pnl["pnl"].sum()) if len(pnl) else 0.0
    top1_pnl = float(pnl.iloc[0]["pnl"]) if len(pnl) else 0.0
    top1_inst = str(pnl.iloc[0]["instrument"]) if len(pnl) else None
    top5_pnl = float(pnl.head(5)["pnl"].sum()) if len(pnl) else 0.0
    return {
        "final_equity": final_equity,
        "recon_sum_pnl": sum_pnl,
        "total_gain": total_gain,
        "recon_gap": sum_pnl - total_gain,  # ≈0 if avg-cost replication is exact
        "top1_instrument": top1_inst,
        "top1_pnl": top1_pnl,
        "top1_share": top1_pnl / total_gain if total_gain != 0 else np.nan,
        "top5_pnl": top5_pnl,
        "top5_share": top5_pnl / total_gain if total_gain != 0 else np.nan,
    }


def _run_summary(run_dir: Path, close_mat: pd.DataFrame) -> dict:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    metrics = json.loads((run_dir / "metrics.json").read_text())
    nav = load_nav(run_dir)
    bench = load_benchmark("000906.SH")
    bench_rebased = bench / bench.iloc[0]
    span_bench = bench_rebased[bench_rebased.index.isin(nav.index)]
    total = float(nav.iloc[-1] - 1.0)
    active = total - float(span_bench.iloc[-1] - 1.0)

    conc = _concentration(run_dir, close_mat)
    final_equity = conc["final_equity"]
    ret_excl_top1 = final_equity / INIT_CAPITAL - 1.0 - conc["top1_pnl"] / INIT_CAPITAL
    return {
        "name": run_dir.name,
        "signal_id": manifest.get("signal_id"),
        "gate": (manifest.get("exposure_gate") or {}).get("mode", "none"),
        "total_ret": total,
        "cagr": _cagr(nav),
        "maxdd": _maxdd(nav),
        "active_vs_csi800": active,
        "turnover": metrics.get("turnover_total"),
        "orders": metrics.get("order_count_total"),
        "yearly": _yearly(nav),
        **conc,
        "ret_excl_top1": ret_excl_top1,
    }


def print_group(group: str, names: list[str], close_mat: pd.DataFrame) -> None:
    rows = []
    for name in names:
        run_dir = EXEC_ROOT / name
        if not (run_dir / "manifest.json").exists():
            print(f"[skip] {name}: no manifest yet", file=sys.stderr)
            continue
        rows.append(_run_summary(run_dir, close_mat))

    print(f"\n################## Experiment {group} ##################")

    print("\n=== Full-sample ===")
    print(f"{'run':<18}{'gate':<12}{'total':>8}{'cagr':>8}{'maxdd':>8}"
          f"{'active':>8}{'turnover':>12}{'orders':>7}")
    for r in rows:
        print(f"{r['name']:<18}{str(r['gate']):<12}{r['total_ret']:>8.2f}"
              f"{r['cagr']:>8.3f}{r['maxdd']:>8.1%}{r['active_vs_csi800']:>8.2f}"
              f"{r['turnover'] / 1e9 if r['turnover'] else np.nan:>11.2f}B"
              f"{r['orders']:>7}")

    years = sorted({y for r in rows for y in r["yearly"].index})
    print("\n=== Yearly returns (P0.1) ===")
    print(f"{'run':<18}" + "".join(f"{y:>8}" for y in years))
    for r in rows:
        print(f"{r['name']:<18}" + "".join(
            f"{r['yearly'].get(y, np.nan):>8.1%}" for y in years))

    print("\n=== PnL concentration (avg-cost reconstruction vs actual NAV) ===")
    print(f"{'run':<18}{'recon_gap':>11}{'top1':>7}{'top1_shr':>9}{'top5_shr':>9}"
          f"{'excl_top1':>11}")
    for r in rows:
        print(f"{r['name']:<18}{r['recon_gap']:>11.0f}{r['top1_instrument']:>7}"
              f"{r['top1_share']:>9.1%}{r['top5_share']:>9.1%}"
              f"{r['ret_excl_top1']:>11.1%}")

    print("\n=== Top-1 instruments ===")
    for r in rows:
        print(f"  {r['name']:<18} {r['top1_instrument']}: "
              f"{r['top1_pnl'] / 1e6:>8.2f}M ({r['top1_share']:>6.1%} of NAV gain)")

    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="A,B,G",
                    help="comma-separated experiment groups (A cadence, B band, G gates)")
    ap.add_argument("--runs", default=None,
                    help="optional comma-separated explicit run names (overrides groups)")
    args = ap.parse_args()

    close_mat = load_close_matrix()
    if args.runs:
        for name in args.runs.split(","):
            if not name.strip():
                continue
            r = _run_summary(EXEC_ROOT / name.strip(), close_mat)
            print(f"\n=== {r['name']} ===")
            print(f"  total {r['total_ret']:+.2f} | cagr {r['cagr']:+.3f} | "
                  f"maxdd {r['maxdd']:+.1%} | active {r['active_vs_csi800']:+.2f}")
            print(f"  top1 {r['top1_instrument']} {r['top1_share']:+.1%} | "
                  f"top5 {r['top5_share']:+.1%} | excl_top1 {r['ret_excl_top1']:+.1%}")
        return
    for g in [s.strip() for s in args.group.split(",") if s.strip()]:
        print_group(g, GROUP_RUNS.get(g, []), close_mat)


if __name__ == "__main__":
    main()
