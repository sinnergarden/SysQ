#!/usr/bin/env python3
"""
Alpha V1 Production Candidate — Rolling Weekly Backtest
======================================================
Strategy: qsys_alpha_v1_candidate_blend20_weekly_top20_buffer
         --start DATE          Training data start (default: 2022-01-01)
         --end DATE            Backtest end date (default: run to data end)
         --data-end DATE       Data fetch end date (default: equals --end)
         --price-mode MODE     "open" (fail-fast if $open missing) or "close_fallback"

Logic:
  - clean_5d 主信号 + clean_20d 中周期稳定信号
  - blended_score = 0.8 * zscore(pred_5d) + 0.2 * zscore(pred_20d)
  - Weekly rebalance, top20, buffer hold60_buy40
  - rank_weight_capped, single stock cap 7%

Each window: 2yr train → predict 1wk test → backtest → aggregate.
Account persists across windows (continuous equity curve).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
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
from qsys.reports.backtest import BacktestReport
from qsys.reports.base import ReportSection, ReportStatus, save_report
from qsys.trader.account import Account
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine

# ── Constants (overridable via --universe) ──
UNIVERSE = "csi300"
TRAIN_DAYS = 504         # ~2 trading years
TEST_DAYS = 5            # 1 trading week
STEP_DAYS = 5            # non-overlapping weekly steps
TOP_N = 20
TARGET_CASH = 10_000_000
SINGLE_STOCK_CAP = 0.07
BLEND_5D = 0.8
BLEND_20D = 0.2
BUFFER_HOLD = 60         # hold if rank <= 60
BUFFER_BUY = 40          # buy from rank <= 40
REBALANCE_FREQ = "weekly"

LGB_PARAMS = {
    "objective": "regression", "metric": "mse",
    "colsample_bytree": 0.8879, "learning_rate": 0.0421,
    "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
    "max_depth": 8, "num_leaves": 210, "num_threads": 8,
    "verbosity": -1, "seed": 42,
}
N_ESTIMATORS = 200

HARMFUL_GROUPS = {"Fundamental", "VolumeAmt", "Valuation", "Margin", "PricePattern"}

CBP = {  # cost baseline params
    "commission": 0.0003, "stamp_duty": 0.001, "slippage": 0.001,
    "min_commission": 5.0,
}

HEALTH_THRESHOLDS = {
    "rankic_warn": 0.01, "rankic_crit": 0.0,
    "excess_20d_warn": -0.05, "excess_60d_crit": -0.08,
    "dd_warn": -0.15, "dd_crit": -0.20,
    "feature_missing_warn": 0.05, "failed_trade_warn": 0.10,
}

OUTPUT_DIR = Path("experiments/alpha_v1_candidate_csi300")
UI_REPORTS_DIR = Path("experiments/reports")

# ── CLI-overridable config (set via argparse, defaults match original values) ──
# TODO: Extract all config into AlphaV1Config dataclass at qsys/strategy/alpha_v1/spec.py (post-PR #71)
STRATEGY_VERSION = "alpha_v1_candidate_202605"
PRICE_MODE = "open"          # "open" (fail-fast) or "close_fallback"
CLI_START = "2022-01-01"
CLI_END = None
CLI_DATA_END = None

# ── Helpers (reused from Phase 4) ──

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

def daily_ic(pred, target, groupby):
    df = pd.DataFrame({"pred": np.asarray(pred), "target": np.asarray(target), "g": np.asarray(groupby)})
    return df.dropna().groupby("g").apply(lambda g: g["pred"].corr(g["target"], method="spearman"))

def compute_ic_stats(ic_s):
    ic_s = ic_s.dropna()
    mean_ic = float(ic_s.mean()); std_ic = float(ic_s.std())
    icir = mean_ic / std_ic if std_ic > 0 else 0.0
    pos = float((ic_s > 0).mean())
    return {"Mean_IC": mean_ic, "ICIR": icir, "Pos%": pos}

def make_zs_label(horizon):
    def label_fn(df):
        g = df.groupby("instrument")["$close"]
        fwd = g.shift(-horizon) / df["$close"] - 1.0
        temp = df.copy(); temp["_r"] = np.asarray(fwd)
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
    assigned = set(); [assigned.update(v) for v in groups.values()]
    unassigned = [f for f in all_features if f not in assigned]
    if unassigned: groups["Other"] = unassigned
    return {k: v for k, v in groups.items() if len(v) >= 3}

def get_clean_features(all_features):
    groups = get_feature_groups(all_features)
    to_remove = set()
    for grp_name in HARMFUL_GROUPS: to_remove.update(groups.get(grp_name, []))
    return [f for f in all_features if f not in to_remove]

def train_model(X_train, y_train, tag, n_est=None):
    if n_est is None: n_est = N_ESTIMATORS
    center, scale = robust_zscore_fit(X_train)
    Xz = robust_zscore_transform(X_train, center, scale)
    N = len(Xz); vs = min(20000, int(N * 0.15))
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_train.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_train.iloc[-vs:].values)
    model = lgb.train(LGB_PARAMS, train_data, num_boost_round=n_est, valid_sets=[val_data],
                      callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
    pred = pd.Series(model.predict(Xz.values), index=Xz.index)
    valid = pred.notna() & y_train.notna()
    if valid.sum() > 0:
        ric = float(pred[valid].corr(y_train[valid], method="spearman"))
        print(f"    [{tag}] Train RankIC={ric:.5f}, trees={model.best_iteration}")
    return model, center, scale

def predict_model(model, center, scale, X):
    Xz = robust_zscore_transform(X, center, scale)
    return pd.Series(model.predict(Xz.values), index=X.index)


# ── Data Loading ──
# TODO: Extract into AlphaV1Backtest._load_data() at qsys/research/alpha_v1_backtest.py (post-PR #71)

def load_data(start_time=None, end_time=None, data_end=None, price_mode="open"):
    """Load all data upfront. Returns full DataFrame + clean_features list."""
    print("[Data] Loading...")
    t0 = time.time()
    if start_time is None: start_time = CLI_START
    if end_time is None: end_time = CLI_END
    if data_end is None:
        data_end = CLI_DATA_END
        if data_end is None:
            data_end = end_time
        if data_end is None:
            data_end = datetime.now().strftime("%Y-%m-%d")
    adapter = QlibAdapter(); adapter.init_qlib()
    all_features = FeatureLibrary.get_semantic_all_features_config()
    fetch_end = data_end

    raw = adapter.get_features(UNIVERSE, all_features + ["$close"],
                               start_time=start_time, end_time=fetch_end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]

    # $open — TODO: Extract price_mode logic into AlphaV1ExecutionConfig (post-PR #71)
    try:
        insts = D.instruments(UNIVERSE)
        open_raw = D.features(insts, ["$open"], start_time=start_time, end_time=fetch_end)
        open_df = open_raw.reset_index().rename(columns={"datetime": "trade_date"})
        open_df = open_df[["trade_date", "instrument", "$open"]].dropna(subset=["$open"])
        open_df = open_df.drop_duplicates(subset=["trade_date", "instrument"])
        frame = frame.merge(open_df, on=["trade_date", "instrument"], how="left")
        if "$open" in frame.columns:
            n_nonnull = frame["$open"].notna().sum()
            print(f"  $open loaded (non-null: {n_nonnull})")
            if n_nonnull == 0:
                raise ValueError("$open is entirely null")
        else:
            raise ValueError("$open column missing")
    except Exception as e:
        if price_mode == "open":
            print(f"  ERROR: $open unavailable and --price-mode=open. "
                  f"Use --price-mode close_fallback to allow $close fallback.")
            raise RuntimeError(f"$open fetch failed in price_mode='open': {e}") from e
        print(f"  WARN: $open failed ({e}), using $close (price_mode=close_fallback)")
        frame["$open"] = frame["$close"]

    # VWAP
    if "$amount" in frame.columns and "$volume" in frame.columns:
        vol_safe = frame["$volume"].replace(0, np.nan)
        frame["$vwap"] = frame["$amount"] / vol_safe
    else:
        frame["$vwap"] = frame["$close"]

    # Industry
    db_paths = [Path("data/meta.db"), Path("data/meta/meta.db")]
    for dp in db_paths:
        if dp.exists():
            with sqlite3.connect(dp) as conn:
                sb = pd.read_sql("select ts_code, industry from stock_basic", conn)
            sb = sb.rename(columns={"ts_code": "instrument"})
            frame = frame.merge(sb, on="instrument", how="left"); break

    frame = frame.sort_values(["instrument", "trade_date"]).reset_index(drop=True)
    clean_features = get_clean_features(all_features)
    print(f"  Full data: {len(frame)} rows, {frame['trade_date'].nunique()}d, clean features={len(clean_features)}")
    print(f"  Time: {time.time()-t0:.1f}s")
    make_forward_returns(frame, horizons=[1, 5, 20])
    return frame, clean_features

def compute_trade_flags(frame):
    frame = frame.copy()
    pc = frame.groupby("instrument")["$close"].shift(1)
    frame["is_suspended"] = ((frame["$volume"].fillna(0) <= 0) | frame["$close"].isna()).astype(int)
    pct = frame["$open"] / pc.replace(0, np.nan) - 1.0
    frame["is_limit_up"] = (pct >= 0.095).fillna(False).astype(int)
    frame["is_limit_down"] = (pct <= -0.095).fillna(False).astype(int)
    return frame


# ── Rolling Window Builder ──

def build_trading_day_windows(all_dates, train_days=TRAIN_DAYS, test_days=TEST_DAYS, step_days=STEP_DAYS):
    """Build non-overlapping rolling windows from sorted trading dates."""
    dates = sorted(all_dates)
    windows = []
    for i in range(0, len(dates), step_days):
        test_end_idx = i + test_days - 1
        if test_end_idx >= len(dates):
            break
        test_start = dates[i]
        test_end = dates[test_end_idx]
        train_start_idx = i - train_days
        if train_start_idx < 0:
            continue  # not enough history
        train_start = dates[train_start_idx]
        train_end = dates[i - 1] if i > 0 else dates[0]
        windows.append({
            "window_id": f"w{i//step_days:04d}",
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": test_start.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })
    return windows


# ── Portfolio Construction (Alpha V1 Rules) ──
# TODO: Extract into AlphaV1PortfolioBuilder at qsys/strategy/alpha_v1/portfolio.py (post-PR #71)

def build_alpha_v1_portfolio(scores, account, prices, ind_s):
    """
    Alpha V1 portfolio builder:
    - Buffer: hold if rank <= BUFFER_HOLD, buy from rank <= BUFFER_BUY
    - Target size: TOP_N
    - Weight: rank_weight_capped (linear decay), single stock <= SINGLE_STOCK_CAP
    - Industry-aware selection (not hard cap, just soft limit)
    """
    ranked = scores.sort_values(ascending=False)
    ranks = pd.Series(range(1, len(ranked) + 1), index=ranked.index)
    held = set(account.positions.keys())

    # Keep current holdings within buffer hold threshold
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
            # Soft filter: skip if suspended, limit-up (checked at execution)
            buys.append(inst)
            if len(buys) >= remaining:
                break

    selected = list(keep.keys()) + buys
    if not selected:
        return {}

    # Rank weight (linear decay)
    ws = {}
    tr = sum(range(1, len(selected) + 1))
    for ri, s in enumerate(selected):
        raw_w = (len(selected) - ri) / tr
        ws[s] = min(raw_w, SINGLE_STOCK_CAP)

    # Redistribute excess from capped positions
    excess = sum(raw_w for s, raw_w in zip(selected, [sum(range(1, len(selected)+1))]) if False)  # placeholder
    # Actually calculate excess
    total_raw = 0
    for ri, s in enumerate(selected):
        raw_w = (len(selected) - ri) / tr
        total_raw += raw_w
        if raw_w > SINGLE_STOCK_CAP:
            excess_amt = raw_w - SINGLE_STOCK_CAP
            ws[s] = SINGLE_STOCK_CAP
        else:
            ws[s] = raw_w

    # Normalize to sum=1
    wt = sum(ws.values())
    if wt > 0:
        ws = {k: v / wt for k, v in ws.items()}

    return ws


# ── Continuous Rolling Backtest Loop ──
# TODO: Extract into AlphaV1Backtest.run() at qsys/research/alpha_v1_backtest.py (post-PR #71)

def run_continuous_backtest(frame, windows, clean_features, account, order_gen, matcher,
                             zc_account=None, zc_matcher=None):
    """
    Single continuous daily loop across all test dates.
    Models are retrained at each window boundary.
    Pending orders persist naturally across windows.
    zc_account/zc_matcher: optional zero-cost account for fee-free tracking.
    """
    # Build set of all test dates and a map of window starts
    all_test_dates = set()
    window_start_map = {}  # test_start -> window
    for w in windows:
        for d in pd.date_range(w["test_start"], w["test_end"], freq="D"):
            all_test_dates.add(d.strftime("%Y-%m-%d"))
        window_start_map[w["test_start"]] = w

    # Get actual trading dates from the data that fall in our test range
    all_dates = sorted(
        d for d in frame["trade_date"].unique()
        if d.strftime("%Y-%m-%d") in all_test_dates or
           any(w["test_start"] <= d.strftime("%Y-%m-%d") <= w["test_end"] for w in windows)
    )
    all_dates_str = set(d.strftime("%Y-%m-%d") if hasattr(d, 'strftime') else str(d)[:10] for d in all_dates)

    # Filter windows: test_start must be a real trading date
    valid_windows = [w for w in windows if w["test_start"] in all_dates_str]
    # Build next retrain date map
    retrain_schedule = {w["test_start"]: w for w in valid_windows}

    daily_rows, trade_rows, pending = [], [], []
    prev_equity = account.get_total_equity({})
    current_models = None
    current_window_id = None
    rebal_dates = _get_rebalance_dates(all_dates, REBALANCE_FREQ)
    n_retrains = 0
    # Predictions cache: {(date, inst): blended_score}
    pred_cache = {}
    score_cache = {}
    # Per-window signal metrics (IC, RankIC)
    all_signal_rows = []
    # Quintile portfolio NAV tracking for group returns
    quintile_log = []  # [{group, date, nav}]
    current_quintile_insts = {}  # {1: [insts], ...} set at each rebalance
    quintile_nav = {g: 1.0 for g in range(1, 6)}

    for i, date in enumerate(all_dates):
        date_str = date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(date)

        # Retrain models if this is a window start
        if date_str in retrain_schedule:
            w = retrain_schedule[date_str]
            train_mask = (frame["trade_date"] >= w["train_start"]) & (frame["trade_date"] <= w["train_end"])
            train = frame[train_mask].copy()
            if len(train) >= 1000:
                models_ok = True
                models = {}
                for tag, h in [("5d", 5), ("20d", 20)]:
                    y_train = make_zs_label(h)(train)
                    X_tr = train[clean_features].astype(np.float32).fillna(0.0)
                    y_tr = y_train[pd.notna(y_train)]
                    valid_rows = y_tr.index
                    X_tr_valid = X_tr.loc[valid_rows]
                    if len(X_tr_valid) < 500:
                        models_ok = False
                        break
                    try:
                        models[tag] = train_model(X_tr_valid, y_tr, f"{w['window_id']}_{tag}")
                    except Exception as e:
                        print(f"    [{w['window_id']}] ERROR {tag}: {e}")
                        models_ok = False
                        break

                if models_ok:
                    # Pre-compute predictions for this window's test range
                    test_mask = (frame["trade_date"] >= w["test_start"]) & (frame["trade_date"] <= w["test_end"])
                    test_data = frame[test_mask].copy()
                    if len(test_data) > 0:
                        X_test = test_data[clean_features].astype(np.float32).fillna(0.0)
                        for tag in ["5d", "20d"]:
                            test_data[f"pred_{tag}"] = predict_model(*models[tag], X_test).values
                        # Blend scores per date
                        for d in test_data["trade_date"].unique():
                            dm = test_data["trade_date"] == d
                            sub = test_data[dm]
                            z5 = cs_zscore(pd.Series(sub["pred_5d"].values))
                            z20 = cs_zscore(pd.Series(sub["pred_20d"].values))
                            test_data.loc[dm, "blended_score"] = (BLEND_5D * z5.values + BLEND_20D * z20.values)
                        # Store predictions in cache
                        keep_cols = ["trade_date", "instrument", "pred_5d", "pred_20d", "blended_score"]
                        preds_df = test_data[keep_cols].dropna(subset=["blended_score"])
                        for _, row in preds_df.iterrows():
                            td = row["trade_date"]
                            date_key = td.strftime("%Y-%m-%d") if hasattr(td, 'strftime') else str(td)[:10]
                            key = (date_key, row["instrument"])
                            score_cache[key] = {
                                "pred_5d": row["pred_5d"],
                                "pred_20d": row["pred_20d"],
                                "blended_score": row["blended_score"],
                            }
                        # Compute per-window IC/RankIC from blended scores vs forward returns
                        window_ics = []
                        for ic_date in test_data["trade_date"].unique():
                            ic_mask = test_data["trade_date"] == ic_date
                            ic_sub = test_data[ic_mask].dropna(subset=["blended_score", "fwd_5d"])
                            if len(ic_sub) >= 10:
                                ic_v = float(ic_sub["blended_score"].corr(ic_sub["fwd_5d"]))
                                ric_v = float(ic_sub["blended_score"].corr(ic_sub["fwd_5d"], method="spearman"))
                                window_ics.append({"IC": ic_v, "RankIC": ric_v})
                        if window_ics:
                            all_signal_rows.append({
                                "window_id": w["window_id"],
                                "IC_mean": float(np.mean([x["IC"] for x in window_ics])),
                                "RankIC_mean": float(np.mean([x["RankIC"] for x in window_ics])),
                                "IC_std": float(np.std([x["IC"] for x in window_ics])),
                                "RankIC_std": float(np.std([x["RankIC"] for x in window_ics])),
                            })
                        current_models = models
                        current_window_id = w["window_id"]
                        n_retrains += 1
                        print(f"  [{w['window_id']}] models ready ({w['test_start']}~{w['test_end']})")

        if current_models is None:
            continue

        # Get today's data
        mask = frame["trade_date"] == date
        today = frame[mask]
        if today.empty:
            continue

        # Execute pending orders
        if pending:
            exec_prices = {r["instrument"]: r["$open"] for _, r in today.iterrows()
                           if pd.notna(r["$open"]) and r["$open"] > 0}
            status_df = pd.DataFrame({
                "is_suspended": today["is_suspended"].values,
                "is_limit_up": today["is_limit_up"].values,
                "is_limit_down": today["is_limit_down"].values,
            }, index=today["instrument"].values)
            results = matcher.match(pending, account, status_df, exec_prices)
            for r in results:
                if r["status"] == "filled":
                    o = r["order"]
                    trade_rows.append({
                        "window_id": current_window_id, "date": str(date),
                        "symbol": o["symbol"], "side": o["side"],
                        "amount": r["filled_amount"], "price": r["deal_price"],
                        "fee": r["fee"],
                    })
            # Also execute through zero-cost matcher if available
            if zc_matcher is not None and zc_account is not None:
                zc_results = zc_matcher.match(pending, zc_account, status_df, exec_prices)
            pending = []

        account.settlement()
        if zc_account is not None:
            zc_account.settlement()

        # Mark to market
        cp = {r["instrument"]: r["$close"] for _, r in today.iterrows()
              if pd.notna(r["$close"]) and r["$close"] > 0}
        equity = account.get_total_equity(cp)
        ret = (equity / prev_equity - 1) if prev_equity > 0 else 0.0
        prev_equity = equity

        zc_equity = zc_account.get_total_equity(cp) if zc_account is not None else None

        daily_rows.append({
            "window_id": current_window_id, "date": str(date),
            "equity": equity, "cash": account.cash,
            "mv": account.get_market_value(cp), "npos": len(account.positions),
            "ret": ret, "zc_equity": zc_equity,
        })

        # Update quintile portfolio NAVs using daily close-to-close returns
        if current_quintile_insts:
            for g in range(1, 6):
                g_insts = current_quintile_insts.get(g, [])
                g_ret = 0.0
                g_count = 0
                for inst in g_insts:
                    inst_row = today[today["instrument"] == inst]
                    if len(inst_row) > 0:
                        r_val = inst_row["daily_ret"].iloc[0]
                        if pd.notna(r_val):
                            g_ret += r_val
                            g_count += 1
                if g_count > 0:
                    avg_ret = g_ret / g_count
                    quintile_nav[g] *= (1 + avg_ret)
                    quintile_log.append({"group": g, "date": date_str, "nav": quintile_nav[g]})

        # Rebalance?
        if date not in rebal_dates:
            continue

        date_key = date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(date)
        inst_scores = {}
        for _, r in today.iterrows():
            key = (date_key, r["instrument"])
            if key in score_cache:
                inst_scores[r["instrument"]] = score_cache[key]["blended_score"]
        if not inst_scores:
            continue
        scores = pd.Series(inst_scores).dropna()
        if len(scores) < 5:
            continue

        ind_s = pd.Series(today["industry"].values, index=today["instrument"].values) if "industry" in today.columns else None
        tw = build_alpha_v1_portfolio(scores, account, cp, ind_s)
        if not tw:
            continue

        # Track signal quintile portfolios for group returns
        if len(scores) >= 30:
            nq = max(len(scores) // 5, 1)
            sorted_insts = scores.sort_values(ascending=False).index.tolist()
            current_quintile_insts = {}
            for g in range(1, 6):
                start_idx = (g - 1) * nq
                end_idx = g * nq if g < 5 else len(sorted_insts)
                current_quintile_insts[g] = sorted_insts[start_idx:end_idx]
        else:
            current_quintile_insts = {}

        orders = order_gen.generate_orders(tw, account, cp)
        pending = orders if orders else []

    print(f"  Models retrained: {n_retrains}x")
    return daily_rows, trade_rows, all_signal_rows, quintile_log


def _get_rebalance_dates(dates, freq):
    dates = sorted(dates)
    if freq == "weekly":
        rb = {d for d in dates if d.weekday() == 4}
        if not rb:  # fallback
            seen = set()
            for d in reversed(dates):
                w = d.isocalendar()[1]
                if w not in seen: seen.add(w); rb.add(d)
        return rb
    if freq == "daily_full" or freq == "daily_partial":
        return set(dates)
    return set(dates)


# ── Health Monitor ──

def build_health_report(daily_df, rolling_metrics):
    """Check health thresholds and generate alerts."""
    alerts = []
    # RankIC from daily pred_5d scores vs forward returns
    if "rankic_60d" in rolling_metrics.columns:
        mean_rankic = rolling_metrics.get("rankic_mean", pd.Series([0.05]))
        mean_val = float(mean_rankic.mean()) if len(mean_rankic) > 0 else 0
        if mean_val < HEALTH_THRESHOLDS["rankic_crit"]:
            alerts.append({"severity": "critical", "metric": "RankIC", "value": round(mean_val, 4), "threshold": 0.0})
        elif mean_val < HEALTH_THRESHOLDS["rankic_warn"]:
            alerts.append({"severity": "warning", "metric": "RankIC", "value": round(mean_val, 4), "threshold": 0.01})

    # Max drawdown
    if not daily_df.empty:
        eq = daily_df["equity"].values
        peak = np.maximum.accumulate(eq)
        dd = (eq - peak) / peak
        max_dd = float(np.min(dd))
        if max_dd < HEALTH_THRESHOLDS["dd_crit"]:
            alerts.append({"severity": "critical", "metric": "MaxDD", "value": round(max_dd, 4), "threshold": -0.20})
        elif max_dd < HEALTH_THRESHOLDS["dd_warn"]:
            alerts.append({"severity": "warning", "metric": "MaxDD", "value": round(max_dd, 4), "threshold": -0.15})

    # Window win rate
    if not rolling_metrics.empty and "total_return" in rolling_metrics.columns:
        pos = int((rolling_metrics["total_return"] > 0).sum())
        wr = pos / max(len(rolling_metrics), 1)
        if wr < 0.4:
            alerts.append({"severity": "critical", "metric": "WinRate", "value": round(wr, 4), "threshold": 0.40})
        elif wr < 0.5:
            alerts.append({"severity": "warning", "metric": "WinRate", "value": round(wr, 4), "threshold": 0.50})

    # Window excess return
    if not rolling_metrics.empty and "excess_return" in rolling_metrics.columns:
        mean_ex = float(rolling_metrics["excess_return"].mean())
        if mean_ex < -0.08:
            alerts.append({"severity": "critical", "metric": "ExcessReturn", "value": round(mean_ex, 4), "threshold": -0.08})

    return {
        "n_windows": len(rolling_metrics),
        "n_alerts": len(alerts),
        "alerts": alerts,
        "healthy": len(alerts) == 0,
    }


# ── Metrics Computation ──

def compute_window_metrics(daily_rows, trade_rows, test_data):
    """Compute performance metrics for the aggregated results."""
    ddf = pd.DataFrame(daily_rows) if daily_rows else pd.DataFrame()
    tdf = pd.DataFrame(trade_rows) if trade_rows else pd.DataFrame()

    if ddf.empty or len(ddf) < 5:
        return {"error": "insufficient_data"}, ddf, tdf

    eq = ddf["equity"].values
    tr = eq[-1] / eq[0] - 1.0
    nd = len(ddf)
    ann = (1 + tr) ** (252 / nd) - 1 if nd > 0 else 0.0
    dr = ddf["ret"].values
    ds = max(np.nanstd(dr), 1e-10)
    av = ds * np.sqrt(252)
    sp = (np.nanmean(dr) / ds) * np.sqrt(252)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    mdd = float(np.min(dd))
    cal = ann / abs(mdd) if mdd < 0 else 0.0

    # Excess return vs benchmark
    idx = test_data.groupby("trade_date")["fwd_1d"].mean().rename("idx")
    idx = idx.reindex(pd.to_datetime(ddf["date"])).fillna(0.0)
    ex = dr - idx.values
    ex_ann = (1 + np.nanmean(ex)) ** 252 - 1
    ex_sp = (np.nanmean(ex) / max(np.nanstd(ex), 1e-10)) * np.sqrt(252)

    # Turnover
    ttf = float(tdf["fee"].sum()) if not tdf.empty and "fee" in tdf.columns else 0.0
    ae = eq.mean()
    ato = 0.0
    if not tdf.empty and "amount" in tdf.columns and "price" in tdf.columns:
        ato = ((tdf["amount"] * tdf["price"]).sum() / max(ae, 1)) * (252 / max(nd, 1))

    # Win rate
    wd = int(np.sum(dr > 0)); ld = int(np.sum(dr < 0))
    wr = wd / (wd + ld) if wd + ld > 0 else 0.0

    return {
        "total_ret": round(tr, 6), "ann_ret": round(ann, 6), "ann_vol": round(av, 6),
        "sharpe": round(sp, 4), "max_dd": round(mdd, 6), "calmar": round(cal, 4),
        "ex_ann": round(ex_ann, 6), "ex_sharpe": round(ex_sp, 4),
        "win_rate": round(wr, 4), "avg_pos": round(float(ddf["npos"].mean()), 1),
        "ann_to": round(ato, 4), "cost": round(ttf, 2), "ndays": nd,
    }, ddf, tdf


# ── Output Writers ──
# TODO: Extract report generation into AlphaV1BacktestResult / separate reporter (post-PR #71)

def save_outputs(daily_df, trade_df, rolling_metrics, health_report, window_results):
    """Save all operational output files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "trades").mkdir(exist_ok=True)
    (OUTPUT_DIR / "reports").mkdir(exist_ok=True)

    # Daily equity
    daily_df.to_csv(OUTPUT_DIR / "daily_equity.csv", index=False)
    print(f"  → {OUTPUT_DIR / 'daily_equity.csv'}")

    # Trade log
    if not trade_df.empty:
        trade_df.to_csv(OUTPUT_DIR / "trades" / "trade_log.csv", index=False)
        print(f"  → {OUTPUT_DIR / 'trades' / 'trade_log.csv'}")

    # Rolling metrics
    pd.DataFrame(rolling_metrics).to_csv(OUTPUT_DIR / "rolling_metrics.csv", index=False)
    print(f"  → {OUTPUT_DIR / 'rolling_metrics.csv'}")

    # Health monitor
    with open(OUTPUT_DIR / "reports" / "health_monitor.json", "w") as f:
        json.dump(health_report, f, indent=2, default=str)
    print(f"  → {OUTPUT_DIR / 'reports' / 'health_monitor.json'}")


def _compute_benchmark_equity(equity_series, universe, init_cap):
    """Compute benchmark equity curve from universe average close price."""
    try:
        from qsys.data.adapter import QlibAdapter
        from qlib.data import D
        adapter = QlibAdapter()
        adapter.init_qlib()
        dates = [d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10] for d in pd.to_datetime(equity_series.index)]
        benchmark_prices = {}
        for date_str in dates:
            try:
                features = adapter.get_features(universe, ["$close"], start_time=date_str, end_time=date_str)
                if features is not None and not features.empty:
                    closes = features["$close"].dropna()
                    if len(closes) > 0:
                        benchmark_prices[date_str] = float(closes.mean())
            except Exception:
                pass
        if benchmark_prices:
            sorted_dates = sorted(benchmark_prices.keys())
            base = benchmark_prices[sorted_dates[0]]
            if base > 0:
                return pd.Series({d: init_cap * benchmark_prices[d] / base for d in sorted_dates}, name="benchmark_equity")
    except Exception:
        pass
    return pd.Series(init_cap, index=equity_series.index, name="benchmark_equity")


def save_ui_report(daily_df, rolling_metrics, perf, total_time, signal_rows=None, quintile_log=None, feature_count=0):
    """Generate UI-visible BacktestReport in experiments/reports/."""
    UI_REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    run_id = f"alpha_v1_candidate_{UNIVERSE}_blend20_weekly_top20_buffer"
    report_path = UI_REPORTS_DIR / f"backtest_{run_id}.json"
    daily_path = UI_REPORTS_DIR.parent / f"backtest_result_{run_id}.csv"
    windows_path = UI_REPORTS_DIR.parent / f"rolling_windows_{run_id}.csv"

    # Prepare daily CSV with all UI-ready columns
    daily_csv_save = daily_df.copy()
    if "zc_equity" in daily_csv_save.columns:
        daily_csv_save = daily_csv_save.rename(columns={"zc_equity": "zero_cost_total_assets"})

    # Drawdown from equity
    eq_vals = daily_csv_save["equity"].values
    peak = np.maximum.accumulate(eq_vals)
    daily_csv_save["drawdown"] = (eq_vals - peak) / peak

    # Benchmark equity (universe avg return starting at same init capital)
    daily_csv_save["date_dt"] = pd.to_datetime(daily_csv_save["date"])
    daily_csv_save = daily_csv_save.set_index("date_dt")
    benchmark = _compute_benchmark_equity(daily_csv_save["equity"], UNIVERSE, TARGET_CASH)
    daily_csv_save["benchmark_equity"] = benchmark
    daily_csv_save = daily_csv_save.reset_index(drop=True)

    daily_csv_save.to_csv(daily_path, index=False)
    rolling_metrics.to_csv(windows_path, index=False)
    pos_windows = int((rolling_metrics["total_return"] > 0).sum()) if "total_return" in rolling_metrics.columns else 0

    # Weekly returns from daily equity (per the user's request over monthly)
    ddf = daily_df.copy()
    ddf["date"] = pd.to_datetime(ddf["date"])
    ddf["week"] = ddf["date"].dt.to_period("W").astype(str)
    weekly_grp = ddf.groupby("week").agg(
        start_assets=("equity", "first"), end_assets=("equity", "last")
    )
    weekly_grp["return"] = weekly_grp["end_assets"] / weekly_grp["start_assets"] - 1
    weekly_returns = [{"week": idx, "return": round(float(row["return"]), 6)} for idx, row in weekly_grp.iterrows()]
    pos_weeks = sum(1 for w in weekly_returns if w["return"] > 0)

    # Also keep monthly for backward compat
    ddf["month"] = ddf["date"].dt.to_period("M").astype(str)
    monthly_grp = ddf.groupby("month").agg(
        start_assets=("equity", "first"), end_assets=("equity", "last")
    )
    monthly_grp["return"] = monthly_grp["end_assets"] / monthly_grp["start_assets"] - 1
    monthly_returns = [{"month": idx, "return": round(float(row["return"]), 6)} for idx, row in monthly_grp.iterrows()]

    # Rename equity→total_assets for BacktestReport compatibility
    report_df = daily_df.copy()
    report_df = report_df.rename(columns={"equity": "total_assets"})

    # Build full audit manifest (stored in report.plan_summary)
    experiment_spec = {
        "strategy_id": "alpha_v1",
        "strategy_version": STRATEGY_VERSION,
        "rolling": {
            "window_count": len(rolling_metrics),
            "test_window_days": TEST_DAYS,
            "step_days": STEP_DAYS,
            "windows_completed": len(rolling_metrics),
            "windows_failed": 0,
            "retrain_per_window": True,
            "label_type": "blended_5d_20d",
        },
        "initial_cash": TARGET_CASH,
        "universe": UNIVERSE,
        "top_k": TOP_N,
        "strategy": "alpha_v1_candidate_blend20_weekly_top20_buffer",
        "date_range": {
            "start": CLI_START,
            "end": CLI_END,
            "data_end": CLI_DATA_END,
        },
        "price_mode": PRICE_MODE,
        "live_like": PRICE_MODE == "open",
        "warnings": ["price_mode=close_fallback: $close used for execution"] if PRICE_MODE == "close_fallback" else [],
        "blend_ratio": {"5d": BLEND_5D, "20d": BLEND_20D},
        "feature_set": f"clean_{feature_count}",
        "label": {
            "type": "cross_sectional_zscore",
            "horizons": [5, 20],
            "clip": 3.0,
        },
        "cost_model": CBP,
        "portfolio": {
            "top_n": TOP_N,
            "buffer_hold": BUFFER_HOLD,
            "buffer_buy": BUFFER_BUY,
            "single_stock_cap": SINGLE_STOCK_CAP,
            "rebalance_freq": REBALANCE_FREQ,
        },
        "training": {
            "train_days": TRAIN_DAYS,
            "test_days": TEST_DAYS,
            "step_days": STEP_DAYS,
            "n_estimators": N_ESTIMATORS,
            "lgb_params": LGB_PARAMS,
        },
        "health_thresholds": HEALTH_THRESHOLDS,
    }
    report = BacktestReport.from_backtest_result(
        result_df=report_df,
        model_path=str(Path(".").resolve() / "data" / "models" / "alpha_v1_candidate"),
        start_date=str(daily_df["date"].iloc[0]) if not daily_df.empty else "",
        end_date=str(daily_df["date"].iloc[-1]) if not daily_df.empty else "",
        top_k=TOP_N,
        universe=UNIVERSE,
        duration_seconds=total_time,
        daily_result_path=str(daily_path),
        experiment_spec=experiment_spec,
    )
    report.run_id = run_id
    report.plan_summary = experiment_spec
    report.model_info.update({
        "model_name": "alpha_v1_candidate_ensemble",
        "feature_set": f"clean_{feature_count}",
        "label_type": "blended_5d_20d",
        "blend_ratio": f"{BLEND_5D}:{BLEND_20D}",
        "strategy": "alpha_v1_candidate_blend20_weekly_top20_buffer",
        "strategy_version": STRATEGY_VERSION,
    })

    sections = []

    def sec(name, metrics):
        return ReportSection(name=name, status=ReportStatus.SUCCESS, metrics=metrics)

    # Simplified Performance section: core metrics only
    sections.append(sec("Performance", {
        "total_return": f"{perf['total_ret']*100:.2f}%",
        "annual_return": f"{perf['ann_ret']*100:.2f}%",
        "sharpe": f"{perf['sharpe']:.3f}",
        "max_drawdown": f"{perf['max_dd']*100:.2f}%",
        "calmar": f"{perf['calmar']:.3f}",
    }))

    sections.append(sec("Cost Analysis", {
        "total_fees": f"{perf.get('cost', 0):.2f}",
        "annualized_turnover": f"{perf.get('ann_to', 0):.1f}x",
    }))

    sections.append(sec("Rolling Windows", {
        "window_count": str(len(rolling_metrics)),
        "window_win_rate": f"{pos_windows / max(len(rolling_metrics), 1) * 100:.1f}%",
        "mean_window_return": f"{rolling_metrics['total_return'].mean() * 100:.2f}%" if "total_return" in rolling_metrics.columns else "",
    }))

    sections.append(sec("Weekly Returns", {
        "total_weeks": str(len(weekly_returns)),
        "positive_weeks": f"{pos_weeks}/{len(weekly_returns)}",
        "weekly_win_rate": f"{pos_weeks / max(len(weekly_returns), 1) * 100:.1f}%",
        "best_week": f"{max(w['return'] for w in weekly_returns) * 100:.2f}%" if weekly_returns else "",
        "worst_week": f"{min(w['return'] for w in weekly_returns) * 100:.2f}%" if weekly_returns else "",
    }))

    report.sections = sections

    # ── Build signal_metrics.json for UI charts ──
    signal_metrics_payload = {"status": "available", "aggregate": {}}
    if signal_rows and len(signal_rows) > 0:
        ic_vals = [r.get("IC_mean", np.nan) for r in signal_rows if "IC_mean" in r]
        ric_vals = [r.get("RankIC_mean", np.nan) for r in signal_rows if "RankIC_mean" in r]
        ic_vals = [v for v in ic_vals if not np.isnan(v)]
        ric_vals = [v for v in ric_vals if not np.isnan(v)]
        if ic_vals:
            ic_m = float(np.mean(ic_vals))
            ic_s = float(np.std(ic_vals)) if len(ic_vals) > 1 else 1.0
            ric_m = float(np.mean(ric_vals))
            ric_s = float(np.std(ric_vals)) if len(ric_vals) > 1 else 1.0
            signal_metrics_payload["IC"] = round(ic_m, 6)
            signal_metrics_payload["RankIC"] = round(ric_m, 6)
            signal_metrics_payload["ICIR"] = round(ic_m / ic_s, 4) if ic_s > 1e-10 else 0
            signal_metrics_payload["RankICIR"] = round(ric_m / ric_s, 4) if ric_s > 1e-10 else 0
            signal_metrics_payload["aggregate"]["IC"] = {"values": ic_vals}
            signal_metrics_payload["aggregate"]["RankIC"] = {"values": ric_vals}
    else:
        signal_metrics_payload["status"] = "not_available"

    # total_return per window for distribution charts
    if "total_return" in rolling_metrics.columns:
        tr_vals = [float(v) for v in rolling_metrics["total_return"].tolist() if pd.notna(v)]
        signal_metrics_payload["aggregate"]["total_return"] = {"values": tr_vals}
        if "IC" not in signal_metrics_payload:
            signal_metrics_payload["IC"] = 0
            signal_metrics_payload["RankIC"] = 0
            signal_metrics_payload["ICIR"] = 0
            signal_metrics_payload["RankICIR"] = 0
    signal_metrics_payload["long_short_spread"] = 0
    signal_metrics_payload["aggregate"]["long_short_spread"] = {"values": []}
    signal_metrics_payload["aggregate"]["turnover"] = {"values": [0.0]}

    signal_path = UI_REPORTS_DIR.parent / f"signal_metrics_{run_id}.json"
    with open(signal_path, "w") as f:
        json.dump(signal_metrics_payload, f, indent=2)

    # ── Build group_returns.csv from quintile NAV data ──
    group_path = UI_REPORTS_DIR.parent / f"group_returns_{run_id}.csv"
    if quintile_log and len(quintile_log) > 0:
        group_df = pd.DataFrame(quintile_log)
        group_df = group_df.sort_values(["group", "date"])
        group_df["ret"] = group_df.groupby("group")["nav"].transform(lambda x: x.pct_change())
        group_means = group_df.groupby("group")["ret"].mean().to_dict()
        group_df["mean_return"] = group_df["group"].map(group_means)
        group_df["label_horizon"] = "5d"
        group_df.to_csv(group_path, index=False)
    else:
        pd.DataFrame().to_csv(group_path, index=False)

    # Weekly returns JSON
    weekly_path = UI_REPORTS_DIR.parent / f"weekly_returns_{run_id}.json"
    with open(weekly_path, "w") as f:
        json.dump(weekly_returns, f, indent=2)

    # Monthly returns JSON (backward compat)
    monthly_path = UI_REPORTS_DIR.parent / f"monthly_returns_{run_id}.json"
    with open(monthly_path, "w") as f:
        json.dump(monthly_returns, f, indent=2)

    report.artifacts = {
        "daily_result": str(daily_path),
        "signal_metrics": str(signal_path),
        "group_returns": str(group_path),
        "execution_audit": str(UI_REPORTS_DIR.parent / f"execution_audit_{run_id}.csv"),
        "rolling_windows": str(windows_path),
        "weekly_returns": str(weekly_path),
        "monthly_returns": str(monthly_path),
        "trades": str(OUTPUT_DIR / "trades" / "trade_log.csv"),
    }
    pd.DataFrame().to_csv(UI_REPORTS_DIR.parent / f"execution_audit_{run_id}.csv", index=False)

    saved = save_report(report, output_dir=str(UI_REPORTS_DIR))
    print(f"  → {saved}")

    return run_id


# ── Main ──

def main():
    t_start = time.time()
    parser = argparse.ArgumentParser(description="Alpha V1 Production Candidate — Rolling Weekly Backtest")
    parser.add_argument("--universe", default="csi300", choices=["csi300", "csi800"],
                        help="Trading universe (default: csi300)")
    parser.add_argument("--start", default="2022-01-01",
                        help="Training data start date (default: 2022-01-01)")
    parser.add_argument("--end", default=None,
                        help="Backtest end date (exclusive, default: run to data_end)")
    parser.add_argument("--data-end", default=None,
                        help="Data fetch end date (default: equals --end)")
    parser.add_argument("--price-mode", default="open", choices=["open", "close_fallback"],
                        help="Execution price: 'open' (fail-fast if $open missing) "
                             "or 'close_fallback' ($close used when $open missing, with warning)")
    args = parser.parse_args()

    # Override module-level universe and output dir
    global UNIVERSE, OUTPUT_DIR
    UNIVERSE = args.universe
    OUTPUT_DIR = Path(f"experiments/alpha_v1_candidate_{UNIVERSE}")

    # Propagate CLI overrides to module-level config
    global PRICE_MODE, CLI_START, CLI_END, CLI_DATA_END
    PRICE_MODE = args.price_mode
    CLI_START = args.start
    CLI_END = args.end
    CLI_DATA_END = args.data_end

    print("=" * 70)
    print(f"QSYS Alpha V1 — Production Candidate Rolling Backtest ({UNIVERSE})")
    print("Strategy: qsys_alpha_v1_candidate_blend20_weekly_top20_buffer")
    print("=" * 70)

    # 1. Load data
    frame, clean_features = load_data(start_time=CLI_START, end_time=CLI_END, data_end=CLI_DATA_END, price_mode=PRICE_MODE)
    frame = compute_trade_flags(frame)

    # Compute daily close-to-close returns for quintile portfolio tracking
    frame["daily_ret"] = frame.groupby("instrument")["$close"].pct_change()

    # 2. Build rolling windows
    all_dates = sorted(frame["trade_date"].unique())
    all_dates_dt = [pd.Timestamp(d) for d in all_dates]
    windows = build_trading_day_windows(all_dates_dt)

    # Filter windows to only run to CLI_END
    if CLI_END is not None:
        windows = [w for w in windows if w["test_end"] <= CLI_END]
    print(f"\n[Windows] {len(windows)} total ({windows[0]['test_start']} ~ {windows[-1]['test_end']})")

    # Train model parameters
    train_params = dict(LGB_PARAMS)
    n_est = N_ESTIMATORS

    # Persistent account (continuous equity curve)
    account = Account(init_cash=TARGET_CASH)
    zc_account = Account(init_cash=TARGET_CASH)  # zero-cost tracking
    order_gen = OrderGenerator()
    matcher = MatchEngine(
        commission=CBP["commission"], stamp_duty=CBP["stamp_duty"],
        min_commission=CBP["min_commission"], slippage=CBP["slippage"],
    )
    zc_matcher = MatchEngine(
        commission=0.0, stamp_duty=0.0, min_commission=0.0, slippage=0.0,
    )

    all_daily, all_trades = [], []
    window_results = []

    # 3. Run continuous rolling backtest
    print(f"\n{'='*70}")
    print(f"Continuous Rolling Backtest ({len(windows)} windows)")
    print(f"{'='*70}")
    all_daily, all_trades, signal_rows, quintile_log = run_continuous_backtest(
        frame, windows, clean_features, account, order_gen, matcher,
        zc_account=zc_account, zc_matcher=zc_matcher,
    )

    total_time = time.time() - t_start

    print(f"\n{'='*70}")
    print("Aggregating Results...")
    print(f"{'='*70}")

    # Build rolling metrics from daily data (grouped by window_id)
    daily_df = pd.DataFrame(all_daily) if all_daily else pd.DataFrame()
    trade_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()
    rm_rows = []
    if not daily_df.empty:
        for wid, grp in daily_df.groupby("window_id"):
            if len(grp) > 1:
                eq = grp["equity"].values
                tr = eq[-1] / eq[0] - 1.0
                n_trades = len(trade_df[trade_df["window_id"] == wid]) if not trade_df.empty else 0
                rm_rows.append({
                    "window_id": wid,
                    "test_start": str(grp["date"].iloc[0]),
                    "test_end": str(grp["date"].iloc[-1]),
                    "total_return": tr,
                    "n_trades": n_trades,
                })
    rm_df = pd.DataFrame(rm_rows) if rm_rows else pd.DataFrame()

    # Overall performance
    perf, _, _ = compute_window_metrics(all_daily, all_trades, frame)

    # 5. Health report
    health = build_health_report(daily_df, rm_df)

    # 6. Year-by-year
    if not daily_df.empty:
        daily_df["date_dt"] = pd.to_datetime(daily_df["date"])
        print(f"\n{'='*70}")
        print("Year-by-Year Performance")
        print(f"{'='*70}")
        for yr in sorted(daily_df["date_dt"].dt.year.unique()):
            yr_df = daily_df[daily_df["date_dt"].dt.year == yr]
            if len(yr_df) < 5:
                continue
            yr_eq = yr_df["equity"].values
            yr_tr = yr_eq[-1] / yr_eq[0] - 1
            yr_sharpe = np.mean(yr_df["ret"]) / max(np.std(yr_df["ret"]), 1e-10) * np.sqrt(252)
            print(f"  {yr}: Ret={yr_tr:.2%}, Sharpe={yr_sharpe:.2f}")

    # 7. Save outputs
    print(f"\n{'='*70}")
    print("Saving Outputs...")
    print(f"{'='*70}")
    save_outputs(daily_df, trade_df, rm_df, health, [])

    # 8. UI report
    print(f"\n{'='*70}")
    print("Generating UI Report...")
    print(f"{'='*70}")
    run_id = save_ui_report(daily_df, rm_df, perf, total_time, signal_rows, quintile_log, feature_count=len(clean_features))

    # 9. Console summary
    print(f"\n{'='*70}")
    print("ALPHA V1 — FINAL RESULTS")
    print(f"{'='*70}")
    n_windows_completed = len(rm_df)
    print(f"  Windows: {n_windows_completed} completed")
    print(f"  Period:  {daily_df['date'].iloc[0] if not daily_df.empty else 'N/A'} ~ {daily_df['date'].iloc[-1] if not daily_df.empty else 'N/A'}")
    print(f"  Returns: {perf['total_ret']*100:.2f}% total, {perf['ann_ret']*100:.2f}% ann")
    print(f"  Vol:     {perf['ann_vol']*100:.2f}% ann")
    print(f"  Sharpe:  {perf['sharpe']:.4f}")
    print(f"  Max DD:  {perf['max_dd']*100:.2f}%")
    print(f"  Calmar:  {perf['calmar']:.4f}")
    print(f"  Excess:  {perf['ex_ann']*100:.2f}% ann, {perf['ex_sharpe']:.4f} Sharpe")
    print(f"  TO:      {perf['ann_to']:.1f}x")
    print(f"  Cost:    ¥{perf['cost']:,.0f}")
    print(f"  Avg Pos: {perf['avg_pos']:.0f}")
    print(f"  WinRate: {perf['win_rate']*100:.1f}%")

    pos_w = int((rm_df["total_return"] > 0).sum()) if not rm_df.empty else 0
    print(f"\n  Window WinRate: {pos_w}/{n_windows_completed} ({pos_w/max(n_windows_completed,1)*100:.1f}%)")

    print(f"\n  Health: {'✅ PASS' if health['healthy'] else '⚠️ ALERTS'}")
    for a in health.get("alerts", []):
        print(f"    [{a['severity']}] {a['metric']} = {a['value']} (threshold: {a['threshold']})")

    # Check for data issues (t+1 open wasn't available in close_fallback mode)
    if PRICE_MODE == "close_fallback":
        print(f"\n  ⚠ Note: ran with --price-mode=close_fallback ($close used where $open unavailable)")
    else:
        print(f"\n  ✓ Price mode: open (executed at $open, fail-fast if missing)")

    print(f"\n  Total time: {total_time:.0f}s")
    print(f"  UI Report: experiments/reports/backtest_{run_id}.json")
    print(f"  UI ID:     {run_id}")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
