#!/usr/bin/env python3
"""Track 6 — Audit 2023 for E1.

Attributes the +143.5% 2023 return:
  - Top1 / Top5 episode PnL contribution (episodes EXITED in 2023, cash-flow
    based via episode_pnl)
  - industry contribution (symbol -> industry from stock_basic)
  - entry-cohort contribution (by entry month)
  - monthly E1 NAV returns within 2023
  - 2021-2025 comparison: RankIC(60d), Top5 excess(60d), cross-sectional score
    std, score_5_minus_6 gap (from the weekly snapshot panel)

Note: realized-PnL attribution by exit year ignores unrealized PnL still held
at year end; E1's median holding is short (~6d) so most PnL is realized within
the year.
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
    load_benchmark,
    load_close_matrix,
    load_industry,
    load_score_panel,
    weekly_snapshots,
)


def _pct(x: float | None, nd: int = 1) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    return f"{x * 100:+.1f}%"


def main() -> int:
    e1 = EXEC_ROOT / "E1_rank_exit"
    env = json.loads(Path("/tmp/ablation_episodes/E1_rank_exit.json").read_text())
    eps = env["data"]["episodes"]
    industry = load_industry()

    rows = []
    for e in eps:
        rows.append({
            "symbol": e["symbol"],
            "entry_date": e["entry_date"],
            "exit_date": e["exit_date"],
            "episode_pnl": e.get("episode_pnl") or 0.0,
            "realized_return": e.get("realized_return"),
            "holding_days": e.get("holding_days"),
            "exit_reason": e.get("exit_reason"),
            "industry": industry.get(e["symbol"], None),
        })
    df = pd.DataFrame(rows)
    df["exit_year"] = df["exit_date"].str[:4]
    df["entry_month"] = df["entry_date"].str[:7]

    out = {"by_year_total_pnl": {}}
    print("=" * 96)
    print("Track 6 — Audit 2023 (E1 realized-PnL attribution by exit year)")
    print("=" * 96)

    for yr in ["2021", "2022", "2023", "2024", "2025"]:
        g = df[df["exit_year"] == yr]
        out["by_year_total_pnl"][yr] = {
            "n": int(len(g)),
            "total_pnl": float(g["episode_pnl"].sum()),
        }
        print(f"{yr}: {len(g)} exits, realized PnL {g['episode_pnl'].sum()/1e6:+.1f}M")

    g23 = df[df["exit_year"] == "2023"].copy()
    print(f"\n2023 exits: n={len(g23)}, total PnL {g23['episode_pnl'].sum()/1e6:+.1f}M")

    # Top1 / Top5 contribution
    tot23 = float(g23["episode_pnl"].sum())
    srt = g23.sort_values("episode_pnl", ascending=False)
    top1 = srt.iloc[0]
    top5 = srt.head(5)
    top1_share = float(top1["episode_pnl"]) / tot23 if tot23 != 0 else None
    top5_share = float(top5["episode_pnl"].sum()) / tot23 if tot23 != 0 else None
    out["top1"] = {"symbol": top1["symbol"], "pnl": float(top1["episode_pnl"]),
                   "share": top1_share, "return": top1["realized_return"],
                   "holding_days": top1["holding_days"],
                   "entry": top1["entry_date"], "exit": top1["exit_date"]}
    out["top5"] = {"symbols": top5["symbol"].tolist(),
                   "pnl": float(top5["episode_pnl"].sum()), "share": top5_share}
    print(f"Top1: {top1['symbol']} {top1['episode_pnl']/1e6:+.2f}M "
          f"(share {_pct(top1_share)}, ret {_pct(top1['realized_return'])})")
    print(f"Top5: {top5['symbol'].tolist()} {top5['episode_pnl'].sum()/1e6:+.2f}M "
          f"(share {_pct(top5_share)})")

    # Top 10 individual contributors
    print("\nTop 10 contributors (2023 exits):")
    for _, r in srt.head(10).iterrows():
        print(f"  {r['symbol']:10s} {r['episode_pnl']/1e6:+.2f}M "
              f"ret {_pct(r['realized_return'])} {r['holding_days']}d "
              f"[{r['entry_date']} -> {r['exit_date']}] {r['industry']}")

    # Industry contribution
    ind = g23.groupby("industry")["episode_pnl"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    ind["share"] = ind["sum"] / tot23 if tot23 else 0
    out["industry"] = {str(k): {"pnl": float(v["sum"]), "n": int(v["count"]),
                                "share": float(v["share"])}
                       for k, v in ind.iterrows() if pd.notna(k)}
    print("\nIndustry contribution (2023):")
    for k, v in list(out["industry"].items())[:10]:
        print(f"  {k:8s} {v['pnl']/1e6:+8.2f}M ({_pct(v['share'])}, n={v['n']})")

    # Entry cohort contribution (by entry month)
    cohort = g23.groupby("entry_month")["episode_pnl"].sum().sort_index()
    out["entry_cohort"] = {str(k): float(v) for k, v in cohort.items()}
    print("\nEntry-cohort PnL (2023 exits by entry month):")
    for k, v in cohort.items():
        print(f"  {k}: {v/1e6:+.2f}M", end="  ")
    print()

    # Monthly E1 NAV returns 2023
    daily = pd.read_csv(e1 / "daily_summary.csv")
    daily["trade_date"] = pd.to_datetime(daily["trade_date"])
    d23 = daily[daily["trade_date"].dt.year == 2023].sort_values("trade_date")
    d23["month"] = d23["trade_date"].dt.month
    monthly = {}
    for m, g in d23.groupby("month"):
        monthly[f"2023-{m:02d}"] = float(g.iloc[-1]["total_value_after"] / g.iloc[0]["total_value_after"] - 1.0)
    out["monthly_nav"] = monthly
    print("\n2023 monthly E1 NAV returns:")
    for k, v in monthly.items():
        print(f"  {k}: {_pct(v)}", end=" ")
    print()

    # 2021-2025 comparison from snapshot panel
    cm = load_close_matrix()
    sp = load_score_panel()
    bench = load_benchmark("000906.SH")
    snap = weekly_snapshots(e1, cm, sp, bench)
    comp = {}
    print("\n2021-2025 comparison (weekly snapshots):")
    for yr in ["2021", "2022", "2023", "2024", "2025"]:
        seg = snap[snap["year"] == int(yr)]
        comp[yr] = {
            "rankic_60": float(seg["rankic_60"].median()) if seg["rankic_60"].notna().any() else None,
            "top5_excess_60": float(seg["top5_excess_60"].median()) if seg["top5_excess_60"].notna().any() else None,
            "cross_section_score_std": float(seg["cross_section_score_std"].median()),
            "score_5_minus_6": float(seg["score_5_minus_6"].median()),
            "top5_mean_minus_universe_median": float(seg["top5_mean_minus_universe_median"].median()),
            "n": int(len(seg)),
        }
        print(f"  {yr}: RankIC60 {comp[yr]['rankic_60']:+.3f} | "
              f"Top5exc60 {_pct(comp[yr]['top5_excess_60'])} | "
              f"score_std {comp[yr]['cross_section_score_std']:.3f} | "
              f"gap5_6 {comp[yr]['score_5_minus_6']:.3f}")
    out["year_comparison"] = comp

    Path("/tmp/diag_track6.json").write_text(json.dumps(out, indent=1, default=float))
    print("\n-> /tmp/diag_track6.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
