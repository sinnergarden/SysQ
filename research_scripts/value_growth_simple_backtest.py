"""
Research reproducibility script — NOT part of the qsys framework.

Purpose:
  Reproduce the value-growth Phase 1 simple backtest numbers.
    - 20d rebalance, T+1 entry, actual adjusted close prices
    - Equal-weight vs rank-weight, with or without industry cap
    - 20bps / 30bps round-trip cost

Inputs:
  - qlib bin data (close, factor)
  - predictions.parquet from research experiments
  - StockDataStore stock list (for industry/list_date)

Outputs:
  - stdout metrics table (same format as research_note)

Original run:
  Phase 1, 2025-12-08 — candidate_pool_analysis scratch

Limitations:
  - Entry uses next calendar trading day, not exit_eval_date -1
  - Prices are close-only (no limit order simulation)
  - No partial fill, no cash drag
  - CSI800 static universe (see static universe bias audit)
  - Industry cap at 25% has minimal effect due to granularity
  - 180d forward return is NOT the same as 20d return;
    this backtest uses actual 20d price change between rebalance dates
  - Hardcoded Phase 1 artifact paths (SIGNAL_PATH, RUN_ID);
    update manually if artifacts move or regenerate
  - Industry cap drops excess names without refill;
    if cap binds, actual holdings may be below topK

Usage:
  cd <project_root>
  python research_scripts/value_growth_simple_backtest.py
"""

import sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np
import pandas as pd
import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.data.storage import StockDataStore
from qsys.data.adapter import QlibAdapter

# ── Config ──────────────────────────────────────────────────────────────
SIGNAL_ID = "fwd_ret_180d_raw__daily_zscore"
RUN_ID = "rolling__value_growth_extended_validation__vgb_ext__fwd_ret_180d_raw__daily_zscore__2013-01-01_2025-12-31"
SIGNAL_PATH = f"data/research/signals/{SIGNAL_ID}/{RUN_ID}/predictions.parquet"
COST_BPS = 0.002  # 20bps round-trip (commission + spread)
TOP_K = 50        # default
INDUSTRY_CAP = 1.0  # 1.0 = no cap, 0.25 = 25%
RANK_WEIGHT = True  # True=rank, False=equal

# ── Load ────────────────────────────────────────────────────────────────
adapter = QlibAdapter()
adapter.init_qlib()
from qlib.data import D
from qlib.data import DatasetD

cal = D.calendar(start_time="2005-01-01", end_time="2026-06-01")
CAL_STR = [str(d)[:10] for d in cal]
CAL_MAP = {d: i for i, d in enumerate(CAL_STR)}

signal = pd.read_parquet(SIGNAL_PATH)
stock_df = StockDataStore().get_stock_list()
ind_map = dict(zip(stock_df["ts_code"].str.upper(), stock_df["industry"]))
list_map = dict(zip(stock_df["ts_code"].str.upper(), stock_df["list_date"]))

signal["inst_up"] = signal["instrument"].str.upper()
signal["industry"] = signal["inst_up"].map(ind_map)
signal["list_dt"] = pd.to_datetime(signal["inst_up"].map(list_map), format="%Y%m%d", errors="coerce")
signal = signal[
    signal["list_dt"].notna()
    & (pd.to_datetime(signal["trade_date"]) >= signal["list_dt"] + pd.Timedelta(days=370))
]


def run_one(start: str, end: str, top_k: int = TOP_K, cap: float = INDUSTRY_CAP,
            rank_wt: bool = RANK_WEIGHT, cost: float = COST_BPS) -> pd.DataFrame:
    """Run a 20d-rebalance backtest over [start, end).

    Returns DataFrame with columns: date, ret, n_hold, n_ind.
    """
    sub = signal[signal["trade_date"].between(start, end)].copy()
    all_dates = sorted(sub["trade_date"].unique())
    eval_dates = all_dates[::20]
    if len(eval_dates) < 2:
        return pd.DataFrame()

    pnl = []
    for i in range(len(eval_dates) - 1):
        d = eval_dates[i]
        next_d = eval_dates[i + 1]

        # Entry = T+1 (next calendar trading day)
        ei = CAL_MAP.get(d, 0) + 1
        if ei >= len(CAL_STR):
            continue
        entry_d = CAL_STR[ei]

        # Exit = 20 trading days after entry (approximate)
        xi = CAL_MAP.get(next_d, ei)
        exit_d = CAL_STR[xi]

        # Select topK
        day = sub[sub["trade_date"] == d].sort_values("score", ascending=False)
        if len(day) < top_k:
            continue
        top = day.head(top_k).copy()

        # Industry cap
        if cap < 1.0:
            ind_counts = top["industry"].value_counts()
            for ind in ind_counts[ind_counts > cap * len(top)].index:
                over = top[top["industry"] == ind]
                n_remove = int(ind_counts[ind] - cap * len(top))
                if n_remove > 0:
                    top = top.drop(over.tail(n_remove).index)

        insts = top["inst_up"].tolist()
        n = len(insts)
        if n == 0:
            continue

        # Fetch entry/exit prices
        try:
            px = D.features(insts, ["$close", "$factor"],
                            start_time=entry_d, end_time=entry_d)
            px_exit = D.features(insts, ["$close", "$factor"],
                                 start_time=exit_d, end_time=exit_d)
        except Exception:
            continue
        if px is None or px_exit is None or px.empty or px_exit.empty:
            continue

        px = px.reset_index()
        px_exit = px_exit.reset_index()
        px["inst"] = px["instrument"].str.upper()
        px_exit["inst"] = px_exit["instrument"].str.upper()

        rets = []
        for inst in insts:
            er = px[px["inst"] == inst]
            ex = px_exit[px_exit["inst"] == inst]
            if er.empty or ex.empty:
                continue
            ep = float(er.iloc[0]["$close"]) * float(er.iloc[0]["$factor"])
            xp = float(ex.iloc[0]["$close"]) * float(ex.iloc[0]["$factor"])
            if pd.notna(ep) and pd.notna(xp) and ep > 0:
                rets.append(xp / ep - 1)

        if len(rets) == 0:
            continue

        if rank_wt:
            w = np.array([(len(rets) - j) / len(rets) for j in range(len(rets))])
            w = w / w.sum()
        else:
            w = np.ones(len(rets)) / len(rets)

        port_ret = float(np.dot(w, rets))
        pnl.append({
            "date": d,
            "ret": port_ret - cost,
            "n_hold": len(rets),
            "n_ind": top[top["inst_up"].isin(insts)]["industry"].nunique(),
        })

    return pd.DataFrame(pnl)


def metrics(pnl: pd.DataFrame) -> dict:
    if pnl.empty or len(pnl) < 2:
        return {}
    cum = (1 + pnl["ret"]).cumprod()
    ann = ((1 + pnl["ret"]).prod()) ** (252 / (len(pnl) * 20)) - 1
    mdd = float((cum / cum.cummax() - 1).min())
    sr = float(pnl["ret"].mean() / pnl["ret"].std(ddof=1) * np.sqrt(252 / 20))
    return {
        "ann": ann, "mdd": mdd, "sharpe": sr,
        "win": float((pnl["ret"] > 0).mean()),
        "n": len(pnl),
        "hold": float(pnl["n_hold"].mean()),
        "ind": float(pnl["n_ind"].mean()),
    }


# ── Run ─────────────────────────────────────────────────────────────────
print("=" * 70)
print("Value Growth Simple Backtest — Reproducibility Script")
print("=" * 70)
print(f"Signal: {SIGNAL_ID}")
print(f"Run:    {RUN_ID}")
print(f"Cost:   {COST_BPS*10000:.0f}bps round-trip")
print()

for period, s, e in [
    ("2020-2025", "2020-01-01", "2025-06-01"),
    ("2023-2025", "2023-01-01", "2025-06-01"),
]:
    print(f"--- {period} ---")
    print(f"  {'K':>4s} {'wt':>6s} {'cap':>8s} {'ann':>8s} {'mdd':>8s} {'sr':>7s} {'win':>5s} {'n':>4s} {'hold':>5s} {'ind':>5s}")
    for top_k in [20, 50, 100]:
        for rw, wl in [(False, "equal"), (True, "rank")]:
            for cp, cl in [(1.0, "none"), (0.25, "25%")]:
                pnl = run_one(s, e, top_k=top_k, cap=cp, rank_wt=rw)
                m = metrics(pnl)
                if m:
                    print(f"  {top_k:>4d} {wl:>6s} {cl:>8s} {m['ann']*100:>7.2f}% {m['mdd']*100:>7.2f}% {m['sharpe']:>7.2f} {m['win']:>4.0%} {m['n']:>4d} {m['hold']:>5.0f} {m['ind']:>5.0f}")
    print()

print("NOTE: Numbers may vary slightly from research_note due to")
print("trading calendar alignment. Directional results are stable.")
