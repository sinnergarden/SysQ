#!/usr/bin/env python3
"""Backtest from SignalStore with stop-loss / trailing-stop rules.

Same signal and rebalance as `backtest_from_signal.py`, but adds:

1. **Trailing stop (盈利保护)**: 如果 position 处于盈利状态，从最高点回撤 ≥10% 时卖出。
2. **Stop-loss (亏损止损)**: 如果 position 亏损 ≥7%，立即卖出。

卖出释放的现金留到下次 rebalance 日按排名买入。
不修改生产 backtest 代码。

Usage:
    python scripts/dev/backtest_with_trailing_stop.py
"""
from __future__ import annotations

import sys, json
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import numpy as np
from qsys.signal.store import SignalStore
from qsys.trader.account import Account
from qsys.trader.matcher import MatchEngine
from qsys.backtest._execution import execute_trade_day

# ── Config ────────────────────────────────────────────────────────────
INITIAL_CAPITAL = 10_000_000
TOP_N = 20
COMMISSION = 0.0003
STAMP_DUTY = 0.001
MIN_COMMISSION = 5.0
SLIPPAGE = 0.001
STOP_LOSS = 0.07        # 止损线: -7%
TRAILING_STOP = 0.10     # 盈利回撤线: 从最高点跌 10%

SIGNAL_ID = "fwd_ret_60d_raw__daily_zscore"
SIGNAL_RUN = "rolling__60d_v3a_growth_financial__v3a_growth_financial_60d__fwd_ret_60d_raw__daily_zscore__2020-01-01_2025-12-31"
START_DATE = "2021-01-01"
END_DATE = "2025-08-27"

OUT = Path("artifacts/diagnostics/60d_backtest")

# ═══════════════════════════════════════════════════════════════════
# 1. Load signal
# ═══════════════════════════════════════════════════════════════════
print("Loading signal...")
sig = SignalStore().load_signal_run(SIGNAL_ID, SIGNAL_RUN)
if sig is None:
    raise RuntimeError(f"Signal not found: {SIGNAL_RUN}")

sig["ts_code"] = sig["instrument"]
sig["trade_date"] = sig["trade_date"].astype(str).str[:10]
sig = sig[sig["trade_date"].between(START_DATE, END_DATE)].sort_values("trade_date").reset_index(drop=True)
print(f"  Signal: {len(sig)} rows, {sig['trade_date'].nunique()} dates")

# Daily scores: group by date, pick top N
daily_top = {}
for dt, grp in sig.groupby("trade_date"):
    top = grp.sort_values("score", ascending=False).head(TOP_N)
    daily_top[dt] = set(top["ts_code"].tolist())


# ═══════════════════════════════════════════════════════════════════
# 2. Price data from qlib
# ═══════════════════════════════════════════════════════════════════
print("Loading price data...")
from qlib.data import D
from qsys.data.adapter import QlibAdapter
QlibAdapter().init_qlib()

all_stocks = sig["ts_code"].unique().tolist()
price_df = D.features(
    D.instruments("csi800"),
    ["$close", "$open"],
    start_time=START_DATE, end_time=END_DATE, freq="day",
).reset_index()
price_df = price_df.rename(columns={"instrument": "ts_code", "datetime": "trade_date"})
price_df["trade_date"] = price_df["trade_date"].astype(str).str[:10]
print(f"  Prices: {len(price_df)} rows")


# ═══════════════════════════════════════════════════════════════════
# 3. Backtest with trailing stop + stop-loss
# ═══════════════════════════════════════════════════════════════════
print("Running backtest with trailing stop...")

account = Account(initial_cash=INITIAL_CAPITAL)
portfolio = {}  # ts_code -> {"cost": float, "peak": float, "qty": int}

dates = sorted(sig["trade_date"].unique())
daily_log = []
last_trade_week = None

for idx, dt in enumerate(dates):
    # ── MTM first: update prices for all positions ──
    day_prices = price_df[price_df["trade_date"] == dt]
    px_map = dict(zip(day_prices["ts_code"], day_prices["$close"]))
    open_map = dict(zip(day_prices["ts_code"], day_prices["$open"]))

    # ── Check stop conditions for each position ──
    stop_sells = []
    for code, pos in portfolio.items():
        if code not in px_map or px_map[code] <= 0:
            continue
        current_price = px_map[code]
        cost = pos["cost"]
        pnl_pct = current_price / cost - 1
        pos["peak"] = max(pos["peak"], current_price)

        if pnl_pct < -STOP_LOSS:
            # Stop-loss triggered
            qty = pos["qty"]
            proceeds = qty * current_price * (1 - SLIPPAGE)
            commission_cost = max(MIN_COMMISSION, proceeds * COMMISSION)
            stamp = proceeds * STAMP_DUTY if proceeds > 0 else 0
            net = proceeds - commission_cost - stamp
            account.cash += net
            stop_sells.append(code)
            # print(f"  [STOP-LOSS] {code} on {dt}: PnL={pnl_pct:.1%}, sold {qty}@${current_price:.2f}")
        elif pnl_pct > 0 and current_price < pos["peak"] * (1 - TRAILING_STOP):
            # Trailing stop triggered
            qty = pos["qty"]
            proceeds = qty * current_price * (1 - SLIPPAGE)
            commission_cost = max(MIN_COMMISSION, proceeds * COMMISSION)
            stamp = proceeds * STAMP_DUTY if proceeds > 0 else 0
            net = proceeds - commission_cost - stamp
            account.cash += net
            stop_sells.append(code)
            # print(f"  [TRAIL-STOP] {code} on {dt}: peak={pos['peak']:.2f}->{current_price:.2f}, sold {qty}@${current_price:.2f}")

    for code in stop_sells:
        del portfolio[code]

    # ── Weekly rebalance decision ──
    iso = pd.Timestamp(dt).isocalendar()
    current_week = (iso[0], iso[1])
    is_rebalance = (current_week != last_trade_week)

    if is_rebalance:
        # Refresh scores: get top N for this date
        top_stocks = daily_top.get(dt, set())

        # Sell everything NOT in top N and not managed by stop rules
        sell_codes = [code for code in portfolio if code not in top_stocks]
        for code in sell_codes:
            if code in px_map and px_map[code] > 0:
                qty = portfolio[code]["qty"]
                price = px_map[code]
                proceeds = qty * price * (1 - SLIPPAGE)
                commission_cost = max(MIN_COMMISSION, proceeds * COMMISSION)
                stamp = proceeds * STAMP_DUTY if proceeds > 0 else 0
                account.cash += proceeds - commission_cost - stamp
            del portfolio[code]

        # Buy new stocks (fill up to TOP_N from cash)
        current_codes = set(portfolio.keys())
        buy_candidates = [c for c in top_stocks if c not in current_codes]
        sorted_scores = sig[sig["trade_date"] == dt].sort_values("score", ascending=False)
        buy_list = sorted_scores[~sorted_scores["ts_code"].isin(current_codes)].head(TOP_N)["ts_code"].tolist()

        if buy_list and account.cash > 0:
            alloc_per_stock = account.cash / len(buy_list)
            for code in buy_list:
                if code not in open_map or open_map[code] <= 0:
                    continue
                price = open_map[code] * (1 + SLIPPAGE)
                qty = int(alloc_per_stock / price)
                if qty <= 0:
                    continue
                cost = qty * price
                commission_cost = max(MIN_COMMISSION, cost * COMMISSION)
                stamp = cost * STAMP_DUTY
                total_cost = cost + commission_cost + stamp
                if total_cost > account.cash:
                    qty = int((account.cash - commission_cost - stamp) / price)
                    if qty <= 0: continue
                    cost = qty * price
                    commission_cost = max(MIN_COMMISSION, cost * COMMISSION)
                    stamp = cost * STAMP_DUTY
                    total_cost = cost + commission_cost + stamp
                account.cash -= total_cost
                portfolio[code] = {"cost": price, "peak": price, "qty": qty}

        last_trade_week = current_week

    # ── Daily summary ──
    total_mv = 0.0
    pos_details = []
    for code, pos in portfolio.items():
        px = px_map.get(code, 0)
        mv = px * pos["qty"]
        total_mv += mv
        pos_details.append({"ts_code": code, "qty": pos["qty"], "price": px, "mv": mv})

    total_value = account.cash + total_mv
    daily_log.append({
        "trade_date": dt,
        "cash": account.cash,
        "market_value": total_mv,
        "total_value": total_value,
        "pos_count": len(portfolio),
        "action": "rebalance" if is_rebalance else "mtm",
        "stops": len(stop_sells),
    })

    if (idx + 1) % 100 == 0:
        print(f"  [{idx+1}/{len(dates)}] TV={total_value:,.0f} cash={account.cash:,.0f} pos={len(portfolio)}")

# ═══════════════════════════════════════════════════════════════════
# 4. Result
# ═══════════════════════════════════════════════════════════════════
result_df = pd.DataFrame(daily_log)
total_return = (result_df.iloc[-1]["total_value"] / INITIAL_CAPITAL) - 1

print(f"\n{'=' * 60}")
print(f"  60d Trailing-Stop Backtest Result")
print(f"{'=' * 60}")
print(f"  Signal: {SIGNAL_RUN}")
print(f"  Period: {START_DATE} → {END_DATE} ({len(dates)} trading days)")
print(f"  Initial capital: {INITIAL_CAPITAL:,.0f}")
print(f"  Final value: {result_df.iloc[-1]['total_value']:,.0f}")
print(f"  Total return: {total_return:.2%}")
print(f"  Final cash: {result_df.iloc[-1]['cash']:,.0f}")
print(f"  Final positions: {result_df.iloc[-1]['pos_count']}")
print(f"  Max TV: {result_df['total_value'].max():,.0f}")
print(f"  Min TV: {result_df['total_value'].min():,.0f}")
print(f"\n  Stop-loss trigger count: {result_df['stops'].sum()}")
print(f"\n  Compare to baseline (no-stop) +financial: 87.10%")
print(f"  vs vanilla rank_weight_top20 without stops: {total_return:.2%}")

# Output path
OUT.mkdir(parents=True, exist_ok=True)
result_df.to_csv(OUT / "backtest_trailing_stop.csv", index=False)
print(f"\n  Saved: {OUT}/backtest_trailing_stop.csv")
