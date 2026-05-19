#!/usr/bin/env python3
"""
Alpha V1 Shadow Trading Script
===============================
Trains dual LightGBM models (clean_5d + clean_20d) each week,
scores the CSI800 universe, applies alpha_v1 portfolio rules,
and generates shadow orders + Telegram notification.

Usage:
  python scripts/run_alpha_v1_trading.py --date 2026-05-18 --execution_date 2026-05-19

Flow:
  1. Data refresh, load CSI800 + features
  2. Train models on rolling 2yr window
  3. Predict, blend, zscore
  4. Build portfolio (top20, buffer, rank_weight_capped)
  5. Generate shadow orders (CSV)
  6. Telegram notification (prediction summary + buy plan)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
warnings.filterwarnings("ignore")

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.library import FeatureLibrary
from qsys.ops.telegram import send_telegram_message
from qsys.trader.account import Account
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine

# ── Constants ──
UNIVERSE = "csi800"
TRAIN_DAYS = 504
TOP_N = 20
TARGET_CASH = 500_000
SINGLE_STOCK_CAP = 0.07
BLEND_5D = 0.8
BLEND_20D = 0.2
BUFFER_HOLD = 60
BUFFER_BUY = 40

LGB_PARAMS = {
    "objective": "regression", "metric": "mse",
    "colsample_bytree": 0.8879, "learning_rate": 0.0421,
    "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
    "max_depth": 8, "num_leaves": 210, "num_threads": 8,
    "verbosity": -1, "seed": 42,
}
N_ESTIMATORS = 200

HARMFUL_GROUPS = {"Fundamental", "VolumeAmt", "Valuation", "Margin", "PricePattern"}

CBP = {
    "commission": 0.0003, "stamp_duty": 0.001, "slippage": 0.001,
    "min_commission": 5.0,
}

OUTPUT_DIR = Path("experiments/alpha_v1_trading")


# ── Helpers (reused from backtest) ──

def cs_zscore(s):
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return ((s - s.mean()) / std).clip(-3, 3)


def robust_zscore_fit(X):
    center = X.median()
    scale = (X - center).abs().median().replace(0, 1.0)
    return center, scale


def robust_zscore_transform(X, center, scale):
    return ((X.astype(np.float32) - center) / scale).clip(-3, 3).fillna(0.0)


def make_zs_label(horizon):
    def label_fn(df):
        g = df.groupby("instrument")["$close"]
        fwd = g.shift(-horizon) / df["$close"] - 1.0
        temp = df.copy()
        temp["_r"] = np.asarray(fwd)
        return temp.groupby("trade_date")["_r"].transform(cs_zscore)
    return label_fn


def make_forward_returns(df, horizons=(1, 5, 20)):
    g = df.groupby("instrument")["$close"]
    for h in horizons:
        df[f"fwd_{h}d"] = g.shift(-h) / df["$close"] - 1.0


def get_feature_groups(all_features):
    groups = {}
    groups["Size"] = [f for f in all_features if any(k in f for k in ("$total_mv", "$circ_mv", "log_mktcap", "$total_assets", "$equity", "equity "))]
    groups["Valuation"] = [f for f in all_features if any(k in f for k in ("$pe", "$pb", "pe_ttm", "pb_raw", "pcf", "ps_ttm", "operating_cf_to_profit"))]
    groups["Fundamental"] = [f for f in all_features if any(k in f for k in ("roa", "$roe", "net_margin", "$grossprofit_margin", "grossprofit", "$revenue", "$net_income", "$op_cashflow", "revenue_yoy", "profit_yoy", "$debt_to_assets", "$current_ratio"))]
    groups["Margin"] = [f for f in all_features if any(k in f for k in ("lend_volume", "margin_balance", "margin_buy", "margin_repay", "margin_total"))]
    groups["PriceVol"] = [f for f in all_features if "std(" in f.lower() and "close" in f.lower() and "abs" not in f.lower()]
    groups["DollarVol"] = [f for f in all_features if "std(abs" in f.lower() or ("std($" in f.lower() and "volume" in f.lower()) or ("std(" in f.lower() and "abs(" in f.lower())]
    groups["VolumeAmt"] = [f for f in all_features if any(k in f for k in ("turnover_rate", "amount_mean", "vol_mean", "$amount", "$volume", "high_limit", "low_limit", "illiquidity"))]
    groups["Momentum"] = [f for f in all_features if any(k in f for k in ("_ret_", "Slope(", "Rsquare(", "Resi(", "stock_minus_index_ret"))]
    groups["PricePattern"] = [f for f in all_features if any(k in f for k in ("Max(", "Min(", "IdxMax", "IdxMin", "Quantile(", "distance_to", "open_to_close", "close_to_open", "$open/$close", "($close-$open)/$open"))]
    groups["Correlation"] = [f for f in all_features if f.startswith("Corr(")]
    assigned = set()
    for v in groups.values():
        assigned.update(v)
    unassigned = [f for f in all_features if f not in assigned]
    if unassigned:
        groups["Other"] = unassigned
    return {k: v for k, v in groups.items() if len(v) >= 3}


def get_clean_features(all_features):
    groups = get_feature_groups(all_features)
    to_remove = set()
    for grp_name in HARMFUL_GROUPS:
        to_remove.update(groups.get(grp_name, []))
    return [f for f in all_features if f not in to_remove]


def train_model(X_train, y_train, tag, n_est=None):
    if n_est is None:
        n_est = N_ESTIMATORS
    center, scale = robust_zscore_fit(X_train)
    Xz = robust_zscore_transform(X_train, center, scale)
    N = len(Xz)
    vs = min(20000, int(N * 0.15))
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_train.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_train.iloc[-vs:].values)
    model = lgb.train(LGB_PARAMS, train_data, num_boost_round=n_est,
                      valid_sets=[val_data],
                      callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
    pred = pd.Series(model.predict(Xz.values), index=Xz.index)
    valid = pred.notna() & y_train.notna()
    ric = None
    if valid.sum() > 0:
        ric = float(pred[valid].corr(y_train[valid], method="spearman"))
        print(f"    [{tag}] Train RankIC={ric:.5f}, trees={model.best_iteration}")
    return model, center, scale, ric


def predict_model(model, center, scale, X):
    Xz = robust_zscore_transform(X, center, scale)
    return pd.Series(model.predict(Xz.values), index=X.index)


def previous_trading_day(anchor_date: str) -> str:
    QlibAdapter().init_qlib()
    ts = pd.Timestamp(anchor_date)
    calendar = D.calendar(start_time=ts - pd.Timedelta(days=10), end_time=ts)
    candidates = [pd.Timestamp(x) for x in calendar if pd.Timestamp(x) < ts]
    if not candidates:
        return (ts - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    return max(candidates).strftime("%Y-%m-%d")


# ── Alpha V1 Portfolio Builder ──

def build_alpha_v1_portfolio(scores, account):
    ranked = scores.sort_values(ascending=False)
    ranks = pd.Series(range(1, len(ranked) + 1), index=ranked.index)
    held = set(account.positions.keys())

    keep = {}
    for inst in held:
        if inst in ranks.index and ranks[inst] <= BUFFER_HOLD:
            keep[inst] = scores.get(inst, 0.0)

    remaining = max(0, TOP_N - len(keep))
    buys = []
    if remaining > 0:
        for inst in ranked.index:
            if inst in held:
                continue
            if ranks[inst] > BUFFER_BUY:
                continue
            buys.append(inst)
            if len(buys) >= remaining:
                break

    selected = list(keep.keys()) + buys
    if not selected:
        return {}

    tr = sum(range(1, len(selected) + 1))
    ws = {}
    for ri, s in enumerate(selected):
        raw_w = (len(selected) - ri) / tr
        ws[s] = min(raw_w, SINGLE_STOCK_CAP)

    wt = sum(ws.values())
    if wt > 0:
        ws = {k: v / wt for k, v in ws.items()}
    return ws


# ── Data Loading ──

def load_data(end_date: str):
    print(f"[Data] Loading CSI800 data up to {end_date}...")
    t0 = time.time()
    adapter = QlibAdapter()
    adapter.init_qlib()
    all_features = FeatureLibrary.get_semantic_all_features_config()
    fetch_end = end_date

    raw = adapter.get_features(UNIVERSE, all_features + ["$close"],
                               start_time="2022-01-01", end_time=fetch_end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]

    try:
        insts = D.instruments(UNIVERSE)
        open_raw = D.features(insts, ["$open"], start_time="2022-01-01", end_time=fetch_end)
        open_df = open_raw.reset_index().rename(columns={"datetime": "trade_date"})
        open_df = open_df[["trade_date", "instrument", "$open"]].dropna(subset=["$open"])
        open_df = open_df.drop_duplicates(subset=["trade_date", "instrument"])
        frame = frame.merge(open_df, on=["trade_date", "instrument"], how="left")
    except Exception:
        frame["$open"] = frame["$close"]

    if "$amount" in frame.columns and "$volume" in frame.columns:
        vol_safe = frame["$volume"].replace(0, np.nan)
        frame["$vwap"] = frame["$amount"] / vol_safe
    else:
        frame["$vwap"] = frame["$close"]

    db_paths = [Path("data/meta.db"), Path("data/meta/meta.db")]
    for dp in db_paths:
        if dp.exists():
            import sqlite3
            with sqlite3.connect(dp) as conn:
                sb = pd.read_sql("select ts_code, industry from stock_basic", conn)
            sb = sb.rename(columns={"ts_code": "instrument"})
            frame = frame.merge(sb, on="instrument", how="left")
            break

    frame = frame.sort_values(["instrument", "trade_date"]).reset_index(drop=True)
    clean_features = get_clean_features(all_features)
    print(f"  {len(frame)} rows, {frame['trade_date'].nunique()}d, clean_features={len(clean_features)}")
    print(f"  Time: {time.time() - t0:.1f}s")
    make_forward_returns(frame, horizons=[1, 5, 20])
    return frame, clean_features


# ── Main ──

def main():
    t_start = time.time()
    parser = argparse.ArgumentParser(description="Alpha V1 Shadow Trading")
    parser.add_argument("--date", type=str, default=datetime.now().strftime("%Y-%m-%d"),
                        help="Signal date. If not provided, uses today.")
    parser.add_argument("--execution_date", type=str,
                        help="Execution date (default: signal_date)")
    parser.add_argument("--cash", type=float, default=TARGET_CASH,
                        help=f"Shadow cash (default: {TARGET_CASH})")
    parser.add_argument("--output", type=str, default=None,
                        help="Output CSV path for orders (default: auto)")
    parser.add_argument("--skip_data_refresh", action="store_true",
                        help="Skip qlib data refresh")
    args = parser.parse_args()

    signal_date = pd.Timestamp(args.date).strftime("%Y-%m-%d")
    execution_date = args.execution_date or signal_date
    target_cash = args.cash

    print("=" * 70)
    print(f"Alpha V1 Shadow Trading")
    print(f"  Signal date:    {signal_date}")
    print(f"  Execution date: {execution_date}")
    print(f"  Cash:           ¥{target_cash:,.0f}")
    print("=" * 70)

    # 1. Data refresh
    if not args.skip_data_refresh:
        print("\n[Data Refresh]")
        try:
            adapter = QlibAdapter()
            adapter.refresh_qlib_date()
            print("  qlib data refreshed")
        except Exception as e:
            print(f"  WARN: refresh failed ({e}), continuing with existing data")

    # 2. Load data
    frame, clean_features = load_data(execution_date)
    all_dates = sorted(frame["trade_date"].unique())

    # Determine training window: last 2 years of trading dates ending before execution_date
    train_end_idx = None
    for i, d in enumerate(all_dates):
        if str(d)[:10] >= execution_date:
            train_end_idx = i
            break
    if train_end_idx is None:
        train_end_idx = len(all_dates)
    train_start_idx = max(0, train_end_idx - TRAIN_DAYS)

    train_start = all_dates[train_start_idx]
    train_end = all_dates[train_end_idx - 1] if train_end_idx > 0 else all_dates[-1]

    print(f"\n[Training] {train_start.strftime('%Y-%m-%d') if hasattr(train_start, 'strftime') else train_start} ~ {train_end.strftime('%Y-%m-%d') if hasattr(train_end, 'strftime') else train_end}")
    print(f"  Feature count: {len(clean_features)}")

    train_mask = (frame["trade_date"] >= train_start) & (frame["trade_date"] <= train_end)
    train_data = frame[train_mask].copy()

    # 3. Train dual models
    models = {}
    for tag, h in [("5d", 5), ("20d", 20)]:
        print(f"\n  Training clean_{tag}...")
        y_train = make_zs_label(h)(train_data)
        X_tr = train_data[clean_features].astype(np.float32).fillna(0.0)
        y_tr = y_train[pd.notna(y_train)]
        valid_rows = y_tr.index
        X_tr_valid = X_tr.loc[valid_rows]
        models[tag] = train_model(X_tr_valid, y_tr, tag)

    # 4. Predict on the latest available data
    print(f"\n[Predicting]")
    latest_mask = frame["trade_date"] == train_end
    predict_data = frame[latest_mask].copy()
    X_pred = predict_data[clean_features].astype(np.float32).fillna(0.0)

    for tag in ["5d", "20d"]:
        predict_data[f"pred_{tag}"] = predict_model(*models[tag][:3], X_pred).values

    z5 = cs_zscore(pd.Series(predict_data["pred_5d"].values))
    z20 = cs_zscore(pd.Series(predict_data["pred_20d"].values))
    predict_data["blended_score"] = (BLEND_5D * z5.values + BLEND_20D * z20.values)

    scores = predict_data[["instrument", "blended_score", "pred_5d", "pred_20d"]].copy()
    scores = scores.dropna(subset=["blended_score"]).set_index("instrument")
    print(f"  Scored {len(scores)} instruments")

    # 5. Build portfolio
    print(f"\n[Portfolio]")
    account = Account(init_cash=target_cash)

    # Set up current positions (at signal date, assume empty since this is shadow start)
    # For ongoing runs, positions persist from previous state
    tw = build_alpha_v1_portfolio(scores["blended_score"], account)

    if not tw:
        print("  ERROR: No portfolio generated")
        sys.exit(1)

    print(f"  Selected {len(tw)} stocks:")
    for inst, w in sorted(tw.items(), key=lambda x: -x[1]):
        est_value = w * target_cash
        score = scores.loc[inst, "blended_score"] if inst in scores.index else 0
        rank = scores["blended_score"].rank(ascending=False).loc[inst] if inst in scores.index else 0
        print(f"    {inst}: weight={w:.1%} est=¥{est_value:,.0f} score={score:.4f} rank={int(rank)}")

    # 6. Generate orders
    cp = predict_data[["instrument", "$close"]].dropna().set_index("instrument")["$close"].to_dict()
    order_gen = OrderGenerator()
    orders = order_gen.generate_orders(tw, account, cp)

    if not orders:
        print("  No orders generated (portfolio unchanged)")
    else:
        print(f"\n[Orders] {len(orders)} orders generated")

    # 7. Save orders CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    orders_csv_path = args.output or str(OUTPUT_DIR / f"orders_{signal_date}_{execution_date}.csv")
    if orders:
        order_df = pd.DataFrame(orders)
        order_df["signal_date"] = signal_date
        order_df["execution_date"] = execution_date
        order_df.to_csv(orders_csv_path, index=False)
        print(f"  Orders saved: {orders_csv_path}")
    else:
        order_df = pd.DataFrame()
        pd.DataFrame(columns=["symbol", "side", "amount", "price", "signal_date", "execution_date"]
                     ).to_csv(orders_csv_path, index=False)
        print(f"  Empty orders saved: {orders_csv_path}")

    # 8. Build detailed Telegram message
    print(f"\n[Telegram]")
    pos_lines = []
    sorted_tw = sorted(tw.items(), key=lambda x: -x[1])
    for rank, (inst, w) in enumerate(sorted_tw, 1):
        est_value = w * target_cash
        s = scores.loc[inst] if inst in scores.index else None
        sc = f"{s['blended_score']:.3f}" if s is not None else "N/A"
        p5 = f"{s['pred_5d']:.3f}" if s is not None else "N/A"
        p20 = f"{s['pred_20d']:.3f}" if s is not None else "N/A"
        pos_lines.append(
            f"  #{rank} {inst} | w={w:.1%} | ¥{est_value:,.0f} | "
            f"score={sc} | pred5={p5} | pred20={p20}"
        )

    # Check if these are new buys vs holds
    held_symbols = set()
    buy_symbols = [inst for inst, _ in sorted_tw if inst not in held_symbols]

    ric_5d = models.get("5d", (None, None, None, None))[3]
    ric_20d = models.get("20d", (None, None, None, None))[3]
    tree_5d = models["5d"][0].best_iteration if "5d" in models else "?"
    tree_20d = models["20d"][0].best_iteration if "20d" in models else "?"
    total_time = time.time() - t_start

    model_5d_line = f"Model 5d : trees={tree_5d}"
    if ric_5d is not None:
        model_5d_line += f" | RankIC={ric_5d:.4f}"
    model_20d_line = f"Model 20d: trees={tree_20d}"
    if ric_20d is not None:
        model_20d_line += f" | RankIC={ric_20d:.4f}"

    msg_lines = [
        f"🤖 <b>Alpha V1 Pre-open Signal</b>",
        f"Signal: {signal_date} | Execute: {execution_date}",
        f"Universe: {UNIVERSE} | Top: {TOP_N} | Cash: ¥{target_cash:,.0f}",
        f"⏱ {total_time:.0f}s",
        f"",
        f"<b>Training</b>",
        model_5d_line,
        model_20d_line,
        f"",
        f"<b>Prediction</b>",
        f"Scored: {len(scores)} stocks | Selected: {len(tw)} positions",
        f"",
        f"<b>Holdings</b>",
    ]
    msg_lines.extend(pos_lines)
    msg_lines.extend([
        f"",
        f"<b>Trade Plan</b>",
        f"Buy: {len(buy_symbols)} positions",
    ])
    if buy_symbols:
        msg_lines.append(f"Buy list: {', '.join(buy_symbols[:10])}" +
                         (f" +{len(buy_symbols)-10} more" if len(buy_symbols) > 10 else ""))

    msg_text = "\n".join(msg_lines)

    # Send Telegram
    try:
        result = send_telegram_message(msg_text)
        if result.get("status") == "success":
            print(f"  Telegram sent successfully")
        else:
            print(f"  Telegram skipped: {result.get('error', 'unknown')}")
    except Exception as e:
        print(f"  Telegram failed: {e}")

    # 9. Summary
    total_time = time.time() - t_start
    print(f"\n{'=' * 70}")
    print(f"Done in {total_time:.0f}s")
    print(f"Orders: {orders_csv_path}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
