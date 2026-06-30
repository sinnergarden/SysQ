#!/usr/bin/env python3
"""Backtest with stop-loss + trailing-stop (research diagnostic, dev-level).

Same signal as `backtest_from_signal.py` but adds:

1. **Trailing stop**: profitable position drops ≥10% from peak → sell at close.
2. **Stop-loss**: position down ≥7% → sell at close.

Key behavioural rules (vs standard weekly rebalance):
- Profit positions: KEEP even if they drop out of Top20 at rebalance.
  Only sell via trailing stop trigger.
- Loss positions: KEEP until -7% stop-loss triggers.
  Only sell via stop-loss trigger.
- Stop sells at close → proceeds available NEXT trading day.
- Rebalance buys use prior accumulated cash only (no same-day stop cash).
- Total positions float naturally (don't force back to 20; only fill up to 20).

Fee config: slippage=0.001, commission=0.0003, NO stamp duty (same as baseline).

Usage:
    python scripts/dev/backtest_with_trailing_stop.py                                    # TOP20 60d financial
    python scripts/dev/backtest_with_trailing_stop.py --top-n 5                          # TOP5  60d financial
    python scripts/dev/backtest_with_trailing_stop.py --top-n 5 --blend-w180 0.7         # TOP5  30/70 blend
    python scripts/dev/backtest_with_trailing_stop.py --blend-w180 0.5 --blend-w60 0.5   # TOP20 50/50 blend

Output:
    artifacts/diagnostics/60d_backtest/backtest_{top_n}_{desc}.csv
"""
from __future__ import annotations

import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import numpy as np
from qsys.signal.store import SignalStore
from qlib.data import D
from qsys.data.adapter import QlibAdapter

# ── CLI ──
parser = argparse.ArgumentParser()
parser.add_argument("--top-n", type=int, default=20)
parser.add_argument("--blend-w60", type=float, default=1.0)
parser.add_argument("--blend-w180", type=float, default=0.0)
parser.add_argument("--signal-60d", default="rolling__60d_v3a_growth_financial__v3a_growth_financial_60d__fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31")
parser.add_argument("--signal-180d", default="rolling__180d_v3a_growth_financial__v3a_growth_financial_180d__fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31")
parser.add_argument("--start-date", default="2021-01-01")
parser.add_argument("--end-date", default="2025-08-27")
parser.add_argument("--sma-window", type=int, default=1,
                    help="Rolling SMA window for signal smoothing (1 = no smoothing)")
parser.add_argument("--no-trailing-stop", action="store_true",
                    help="Disable trailing stop and stop-loss (pure rebalance only)")
args = parser.parse_args()

INITIAL_CAPITAL = 10_000_000
TOP_N = args.top_n
COMMISSION = 0.0003
MIN_COMMISSION = 5.0
SLIPPAGE = 0.001
STAMP_DUTY = 0.0
STOP_LOSS = 0.07
TRAILING_STOP = 0.10
W60 = args.blend_w60
W180 = args.blend_w180

SIGNAL_60D_RUN = args.signal_60d
SIGNAL_180D_RUN = args.signal_180d
START_DATE = args.start_date
END_DATE = args.end_date

SIGNAL_60D_ID = "fwd_ret_60d_raw__daily_zscore"
SIGNAL_180D_ID = "fwd_ret_180d_raw__daily_zscore"

DESC_PARTS = []
if W60 > 0: DESC_PARTS.append(f"60d_{W60}")
if W180 > 0: DESC_PARTS.append(f"180d_{W180}")
if args.sma_window > 1: DESC_PARTS.append(f"sma{args.sma_window}")
DESC = "_".join(DESC_PARTS) if DESC_PARTS else "empty"
OUT_CSV = f"backtest_top{TOP_N}_{DESC}.csv"

OUT = Path("artifacts/diagnostics/60d_backtest")

# ═══════════════════════════════════════════════════════════════════
# 1. Load signal(s)
# ═══════════════════════════════════════════════════════════════════
QlibAdapter().init_qlib()
print("Loading signal(s)...")

sigs = {}
if W60 > 0:
    s = SignalStore().load_signal_run(SIGNAL_60D_ID, SIGNAL_60D_RUN)
    s["ts_code"] = s["instrument"]; s["trade_date"] = s["trade_date"].astype(str).str[:10]
    sigs["s60"] = s

if W180 > 0:
    s = SignalStore().load_signal_run(SIGNAL_180D_ID, SIGNAL_180D_RUN)
    s["ts_code"] = s["instrument"]; s["trade_date"] = s["trade_date"].astype(str).str[:10]
    sigs["s180"] = s

# Merge signals
sig = None
for k, s in sigs.items():
    s = s[["trade_date", "ts_code", "score"]].rename(columns={"score": k})
    sig = s if sig is None else sig.merge(s, on=["trade_date", "ts_code"], how="inner")

if sig is None:
    raise ValueError("No signals loaded")

# Weighted blend
score_cols = [c for c in ["s60","s180"] if c in sig.columns]
weights_map = {"s60": W60, "s180": W180}
sig["score"] = 0.0
for col in score_cols:
    sig["score"] += weights_map[col] * sig[col]
sig = sig[sig["trade_date"].between(START_DATE, END_DATE)].sort_values("trade_date").reset_index(drop=True)
dates = sorted(sig["trade_date"].unique())
print(f"  Signal: {len(sig)} rows, {len(dates)} dates, blend={W60}x60d/{W180}x180d")

# ── Optional: SMA smoothing ──
if args.sma_window > 1:
    orig_len = len(sig)
    sig = sig.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    sig["score"] = sig.groupby("ts_code")["score"].transform(
        lambda s: s.rolling(window=args.sma_window, min_periods=1).mean()
    )
    sig = sig[sig["trade_date"].between(START_DATE, END_DATE)].reset_index(drop=True)
    dates = sorted(sig["trade_date"].unique())
    print(f"  SMA({args.sma_window}): {orig_len} → {len(sig)} rows, {len(dates)} dates")

# Per-date TopN lookup
daily_top20 = {}
for dt, grp in sig.groupby("trade_date"):
    daily_top20[dt] = set(grp.sort_values("score", ascending=False).head(TOP_N)["ts_code"])

# ═══════════════════════════════════════════════════════════════════
# 2. Price data
# ═══════════════════════════════════════════════════════════════════
print("Loading prices...")
stocks = sig["ts_code"].unique().tolist()
pf = D.features(D.instruments("csi800"), ["$close","$open"], start_time=START_DATE, end_time=END_DATE, freq="day")
pf = pf.reset_index().rename(columns={"instrument":"ts_code","datetime":"trade_date"})
pf["trade_date"] = pf["trade_date"].astype(str).str[:10]
print(f"  Prices: {len(pf)} rows")

# ═══════════════════════════════════════════════════════════════════
# 3. Position book
# ═══════════════════════════════════════════════════════════════════
class Position:
    __slots__ = ("qty", "cost", "peak")
    def __init__(self, qty, cost):
        self.qty = qty
        self.cost = cost
        self.peak = cost

def sell_stock(ts_code, price, qty):
    """Sell qty shares at price, return net cash."""
    gross = qty * price
    fee = max(MIN_COMMISSION, gross * COMMISSION)
    stamp = gross * STAMP_DUTY
    return gross - fee - stamp  # slippage already in price

def buy_cost(price, qty):
    """Return total cost (incl fees) for buying qty at price."""
    gross = qty * price
    fee = max(MIN_COMMISSION, gross * COMMISSION)
    stamp = gross * STAMP_DUTY
    return gross + fee + stamp

# ═══════════════════════════════════════════════════════════════════
# 4. Main loop
# ═══════════════════════════════════════════════════════════════════
print("Running backtest...")

portfolio: dict[str, Position] = {}
cash = INITIAL_CAPITAL
pending_cash = 0.0          # cash from today's stop sales, avail next day
last_trade_week = None
daily_log = []

for idx, this_date in enumerate(dates):
    today_px = pf[pf["trade_date"] == this_date]
    close_map = dict(zip(today_px["ts_code"], today_px["$close"]))
    open_map  = dict(zip(today_px["ts_code"], today_px["$open"]))

    # ── (1) Open: make yesterday's stop cash available ──
    cash += pending_cash
    pending_cash = 0.0

    # ── (2) Open: weekly rebalance — buy using open prices ──
    iso = pd.Timestamp(this_date).isocalendar()
    current_week = (iso[0], iso[1])
    is_rebalance = (current_week != last_trade_week)

    if is_rebalance and cash > 0:
        top_set = daily_top20.get(this_date, set())
        current_set = set(portfolio.keys())
        n_current = len(current_set)

        if n_current < TOP_N:
            slot_count = TOP_N - n_current
            sorted_scores = sig[sig["trade_date"] == this_date].sort_values("score", ascending=False)
            bcodes = sorted_scores[~sorted_scores["ts_code"].isin(current_set)].head(slot_count)["ts_code"].tolist()

            if bcodes:
                alloc_per_stock = cash / len(bcodes)
                for code in bcodes:
                    opx = open_map.get(code)
                    if opx is None or np.isnan(opx) or opx <= 0:
                        continue
                    buy_px = opx * (1 + SLIPPAGE)
                    qty = int(alloc_per_stock / buy_px / 100) * 100
                    while qty > 0:
                        total = buy_cost(buy_px, qty)
                        if total <= cash:
                            break
                        qty -= 100
                    if qty <= 0:
                        continue
                    cash -= total
                    portfolio[code] = Position(qty, buy_px)

        last_trade_week = current_week

    # ── (3) Close: MTM + update peaks ──
    for code, pos in portfolio.items():
        px = close_map.get(code)
        if px is not None and not np.isnan(px) and px > 0:
            pos.peak = max(pos.peak, px)

    # ── (4) Close: check stops, sell if triggered ──
    stop_sales = []
    for code, pos in list(portfolio.items()):
        px = close_map.get(code)
        if px is None or np.isnan(px) or px <= 0:
            continue
        pnl = px / pos.cost - 1

        if pnl < -STOP_LOSS:
            qty = pos.qty
            sell_price = px * (1 - SLIPPAGE)
            pending_cash += sell_stock(code, sell_price, qty)
            stop_sales.append(code)
        elif pnl > 0 and px < pos.peak * (1 - TRAILING_STOP):
            qty = pos.qty
            sell_price = px * (1 - SLIPPAGE)
            pending_cash += sell_stock(code, sell_price, qty)
            stop_sales.append(code)

    for code in stop_sales:
        del portfolio[code]

    # ── NAV: cash + pending_cash + market_value ──
    total_mv = sum(max(0, close_map.get(c, 0) or 0) * pos.qty for c, pos in portfolio.items())
    tv = cash + pending_cash + total_mv

    daily_log.append({
        "trade_date": this_date,
        "available_cash": round(cash, 2),
        "pending_cash": round(pending_cash, 2),
        "total_cash": round(cash + pending_cash, 2),
        "market_value": round(total_mv, 2),
        "total_value": round(tv, 2),
        "pos_count": len(portfolio),
        "action": "rebalance" if is_rebalance else "mtm_only",
        "stops": len(stop_sales),
    })

    if (idx + 1) % 100 == 0:
        print(f"  [{idx+1}/{len(dates)}] TV={tv:,.0f} avail_cash={cash:,.0f} pos={len(portfolio)}")

# ═══════════════════════════════════════════════════════════════════
# 5. Result
# ═══════════════════════════════════════════════════════════════════
rdf = pd.DataFrame(daily_log)
tr = rdf.iloc[-1]["total_value"] / INITIAL_CAPITAL - 1

print(f"\n{'=' * 60}")
print(f"  Backtest: trailing stop + stop-loss (TOP{TOP_N}, {DESC})")
print(f"  Signal: 60d +financial rc")
print(f"  Period: {START_DATE} → {END_DATE} ({len(dates)} days)")
print(f"{'=' * 60}")
print(f"  Initial capital: {INITIAL_CAPITAL:,.0f}")
print(f"  Final value:     {rdf.iloc[-1]['total_value']:,.0f}")
print(f"  Total return:    {tr:.2%}")
print(f"  Available cash:  {rdf.iloc[-1]['available_cash']:,.0f}")
print(f"  Pending cash:    {rdf.iloc[-1]['pending_cash']:,.0f}")
print(f"  Total cash:      {rdf.iloc[-1]['total_cash']:,.0f}")
print(f"  Positions:       {rdf.iloc[-1]['pos_count']}")
print(f"  Max TV:          {rdf['total_value'].max():,.0f}")
print(f"  Stop events:     {int(rdf['stops'].sum())}")
print()
print(f"  Baseline (no stop, standard backtest): 87.10%")
print(f"  With trailing stop + stop-loss:         {tr:.2%}")

OUT.mkdir(parents=True, exist_ok=True)
rdf.to_csv(OUT / f"backtest_top{TOP_N}_{DESC}.csv", index=False)
print(f"\n  → {OUT}/backtest_top{TOP_N}_{DESC}.csv")
