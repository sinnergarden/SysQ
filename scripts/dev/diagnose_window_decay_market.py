#!/usr/bin/env python3
"""诊断实验 3+4 — 训练窗口衰减 + market state features (修复版).

复用 qsys.signal.alpha_v1.training.train_model / predict_model.
使用 per-window cache 加载 feature.
强制 label maturity: train data <= predict_start - 180 trading days.

实验 3: baseline_current, train_3y, train_5y, decay_2y
实验 4: baseline+market_state

只诊断, 不改生产代码.
"""
from __future__ import annotations

import sys, time, warnings, hashlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.label.store import LabelStore
from qsys.signal.store import SignalStore
from qsys.signal.alpha_v1.training import train_model, predict_model

OUT = Path("artifacts/diagnostics/v3a_liq_regime")
OUT.mkdir(parents=True, exist_ok=True)

BASELINE_FEATURES = "v3a_plus_liquidity_pure"
LABEL_ID = "fwd_ret_180d_raw"
UNIVERSE = "csi800"
N_ESTIMATORS = 300

# ═══════════════════════════════════════════════════════════════════
# 1. Load shared data
# ═══════════════════════════════════════════════════════════════════
print("Loading shared data...")

adapter = QlibAdapter()
adapter.init_qlib()

clean_features = FeatureListRegistry.load(BASELINE_FEATURES)
print(f"  Features: {len(clean_features)}")

all_labels = LabelStore().load_labels(LABEL_ID)
all_labels["trade_date"] = all_labels["trade_date"].astype(str).str[:10]
all_labels["ts_code"] = all_labels["instrument"]

# Industry mapping
ind_raw = D.features(D.instruments("csi800"), ["$industry"],
                     start_time="2024-01-01", end_time="2024-01-02", freq="day")
ind_map = ind_raw.reset_index()[["instrument", "$industry"]].drop_duplicates()\
    .rename(columns={"instrument": "ts_code", "$industry": "industry"})

# Rolling windows
windows = pd.read_csv("data/research/experiments/180d_v3a_plus_liquidity/rolling_windows.csv")
print(f"  Windows: {len(windows)}")

# Full calendar for index lookups
cal_str = [str(c)[:10] for c in D.calendar(end_time="2026-01-01", freq="day")]

# Baseline from store
BASELINE_RUN = "rolling__180d_v3a_plus_liquidity_pure__v3a_liq_pure_180d__fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31"
base_sig = SignalStore().load_signal_run("fwd_ret_180d_raw__daily_zscore", BASELINE_RUN)
base_preds = base_sig[["trade_date", "ts_code", "score"]].copy()


# ═══════════════════════════════════════════════════════════════════
# 2. Cache loader
# ═══════════════════════════════════════════════════════════════════

def load_features(start: str, end: str) -> pd.DataFrame:
    """Load features from per-window cache. Falls back to qlib if cache miss."""
    for raw_key in [
        f"__window__::{start}::{end}",
        f"__window__::{start}::{(pd.Timestamp(end) + pd.Timedelta(days=30)).strftime('%Y-%m-%d')}",
    ]:
        key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
        cp = Path("data/feature_cache/per_window") / f"{key}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
            df["trade_date"] = df["trade_date"].astype(str).str[:10]
            keep = {"trade_date", "instrument"} | set(clean_features + ["$close", "$volume", "$amount"])
            avail = [c for c in df.columns if c in keep]
            df = df[avail].rename(columns={"instrument": "ts_code"})
            return df
    # Fallback
    raw = adapter.get_features(UNIVERSE, clean_features + ["$close", "$volume", "$amount"],
                               start_time=start, end_time=end)
    df = raw.reset_index().rename(columns={"datetime": "trade_date"})
    df = df.loc[:, ~df.columns.duplicated()]
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    df["ts_code"] = df["instrument"] if "instrument" in df.columns else df["ts_code"]
    return df


# ═══════════════════════════════════════════════════════════════════
# 3. Label maturity guard
# ═══════════════════════════════════════════════════════════════════

def _is_trading_date(d: str) -> bool:
    return d in cal_str

def _prev_trading_dates(d: str, n: int) -> list[str]:
    """Return the Nth trading day before d (exclusive)."""
    try:
        idx = cal_str.index(d)
    except ValueError:
        return [d]
    start = max(0, idx - n)
    return cal_str[start:idx]

def get_maturity_cutoff(predict_start: str) -> str:
    """180d label maturity: predict_start - 180 trading days."""
    prev = _prev_trading_dates(predict_start, 180)
    if prev:
        # Last trading day that has matured label
        maturity = prev[0]
        assert maturity < predict_start, f"maturity {maturity} >= predict_start {predict_start}"
        return maturity
    return predict_start


# ═══════════════════════════════════════════════════════════════════
# 4. Market state features
# ═══════════════════════════════════════════════════════════════════

MARKET_FEATURES = ["market_ret_60d", "market_ret_120d",
                   "market_amount_percentile_60d",
                   "market_breadth_60d", "industry_dispersion_60d"]

def add_market_state_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add 5 market-level features. Must be PIT safe (only use data <= trade_date)."""
    out = frame.copy()
    for f in MARKET_FEATURES:
        out[f] = np.nan

    # Merge industry for dispersion
    if "industry" not in out.columns:
        out = out.merge(ind_map, on="ts_code", how="left")

    dates = sorted(out["trade_date"].unique())
    for dt in dates:
        hist = out[out["trade_date"] <= dt]
        if len(hist) < 60:
            continue

        # Daily equal-weight market return (CSI800)
        daily_close = hist.groupby("trade_date")["$close"].mean().sort_index()
        daily_rets = daily_close.pct_change().dropna()

        if len(daily_rets) >= 60:
            out.loc[out["trade_date"] == dt, "market_ret_60d"] = daily_rets.iloc[-60:].sum()
        if len(daily_rets) >= 120:
            out.loc[out["trade_date"] == dt, "market_ret_120d"] = daily_rets.iloc[-120:].sum()

        # Market amount percentile (60d)
        daily_amt = hist.groupby("trade_date")["$amount"].sum().sort_index()
        if len(daily_amt) >= 60:
            recent_60 = daily_amt.iloc[-60:]
            pct = (recent_60 < daily_amt.iloc[-1]).mean()
            out.loc[out["trade_date"] == dt, "market_amount_percentile_60d"] = pct

        # Market breadth: mean of daily up_ratio over past 60d
        # up_ratio on day t = fraction of stocks with close[t] > close[t-1]
        looks = _prev_trading_dates(dt, 60)
        breadth_vals = []
        for ld in looks:
            today = out[out["trade_date"] == ld]
            prev_d = _prev_trading_dates(ld, 1)
            if not prev_d:
                continue
            yesterday = out[out["trade_date"] == prev_d[0]]
            merged = today.merge(yesterday, on="ts_code", suffixes=("", "_y"))
            if len(merged) > 10:
                up_frac = (merged["$close"] > merged["$close_y"]).mean()
                breadth_vals.append(up_frac)
        if breadth_vals:
            out.loc[out["trade_date"] == dt, "market_breadth_60d"] = np.mean(breadth_vals)

        # Industry dispersion: std of industry equal-weight returns over 60d
        ind_rets = []
        for ld in looks:
            day = out[out["trade_date"] == ld]
            if "industry" not in day.columns:
                continue
            prev_d = _prev_trading_dates(ld, 1)
            if not prev_d:
                continue
            prev_day = out[out["trade_date"] == prev_d[0]]
            merged = day.merge(prev_day, on="ts_code", suffixes=("", "_y"))
            if "industry" in merged.columns:
                ind_ret = merged.groupby("industry").apply(
                    lambda g: (g["$close"].mean() / g["$close_y"].mean() - 1), include_groups=False
                )
                if len(ind_ret) > 2:
                    ind_rets.append(ind_ret)
        if ind_rets:
            ind_panel = pd.DataFrame(ind_rets)
            ind_disp = ind_panel.std(axis=1).mean()
            out.loc[out["trade_date"] == dt, "industry_dispersion_60d"] = ind_disp

    return out


# ═══════════════════════════════════════════════════════════════════
# 5. Training + prediction per window (reuses train_model/predict_model)
# ═══════════════════════════════════════════════════════════════════

def train_predict(
    train_df: pd.DataFrame,
    predict_df: pd.DataFrame,
    sample_weight: pd.Series | None = None,
    extra_feats: list[str] | None = None,
) -> pd.DataFrame:
    """Train using train_model(), predict with predict_model(). Returns scores."""
    feats = clean_features + (extra_feats or [])
    train = train_df.merge(
        all_labels[["trade_date", "ts_code", "label_value"]],
        on=["trade_date", "ts_code"], how="left",
    )
    has_lab = train["label_value"].notna()
    X_tr = train.loc[has_lab, feats].fillna(0.0).astype(np.float32)
    y_tr = train.loc[has_lab, "label_value"].astype(float)
    if len(y_tr) < 50:
        return pd.DataFrame()

    if sample_weight is not None:
        # train_model doesn't support weights; wrap with weighted lgb
        import lightgbm as lgb
        from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform
        center, scale = robust_zscore_fit(X_tr)
        Xz = robust_zscore_transform(X_tr, center, scale)
        N = len(Xz); vs = min(20000, int(N * 0.15))
        w = sample_weight.loc[X_tr.index].values
        params = {"objective": "regression", "metric": "mse",
                  "colsample_bytree": 0.8879, "learning_rate": 0.0421,
                  "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
                  "max_depth": 8, "num_leaves": 210, "num_threads": 8, "verbosity": -1, "seed": 42}
        train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_tr.iloc[:-vs].values,
                                 weight=w[:-vs])
        val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_tr.iloc[-vs:].values)
        model = lgb.train(params, train_data, num_boost_round=N_ESTIMATORS,
                          valid_sets=[val_data],
                          callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])
        X_pred = predict_df[feats].fillna(0.0).astype(np.float32)
        Xp_z = robust_zscore_transform(X_pred, center, scale)
        scores = model.predict(Xp_z.values)
    else:
        # Reuse canonical train_model
        model, center, scale = train_model(X_tr, y_tr, "diag", n_estimators=N_ESTIMATORS)
        scores = predict_model(model, center, scale, predict_df[feats].fillna(0.0).astype(np.float32)).values

    return pd.DataFrame({"trade_date": predict_df["trade_date"].values,
                         "ts_code": predict_df["ts_code"].values,
                         "score": scores})


# ═══════════════════════════════════════════════════════════════════
# 6. Evaluation (per-date TopK)
# ═══════════════════════════════════════════════════════════════════

def evaluate(df: pd.DataFrame, tag: str) -> dict:
    """Evaluate IC/ICIR + per-date TopK quality."""
    df = df.dropna(subset=["label_value"]).copy()
    if len(df) < 100:
        return {"tag": tag, "error": "no data"}

    # Daily IC
    daily = df.groupby("trade_date").apply(
        lambda g: pd.Series({"ic": g["score"].corr(g["label_value"]),
                             "rank_ic": g["score"].rank().corr(g["label_value"].rank())}),
        include_groups=False
    ).reset_index().dropna(subset=["ic"])

    res = {"tag": tag, "n_days": len(daily), "n_obs": len(df)}
    res["ic"] = daily["ic"].mean()
    res["icir"] = daily["ic"].mean() / daily["ic"].std() if daily["ic"].std() > 0 else 0
    res["rank_ic"] = daily["rank_ic"].mean()
    res["rank_icir"] = daily["rank_ic"].mean() / daily["rank_ic"].std() if daily["rank_ic"].std() > 0 else 0

    # Per-date TopK then aggregate
    dates = sorted(df["trade_date"].unique())
    for k in [20, 50, 100]:
        all_k = []
        for dt in dates:
            topk = df[df["trade_date"] == dt].sort_values("score", ascending=False).head(k)
            all_k.extend(topk["label_value"].tolist())
        vals = pd.Series(all_k).dropna()
        if len(vals) > 0:
            res[f"top{k}_mean"] = vals.mean()
            res[f"top{k}_hit"] = (vals > 0).mean()
            res[f"top{k}_bad"] = (vals < 0).mean()
            res[f"top{k}_gt30"] = (vals > 0.3).mean()

    # Yearly IC
    for year in sorted(set(d[:4] for d in dates)):
        yr = df[df["trade_date"].str[:4] == year]
        if len(yr) < 200:
            continue
        yr_daily = yr.groupby("trade_date").apply(
            lambda g: pd.Series({"ic": g["score"].corr(g["label_value"]),
                                 "rank_ic": g["score"].rank().corr(g["label_value"].rank())}),
            include_groups=False
        ).reset_index().dropna(subset=["ic"])
        res[f"ic_{year}"] = yr_daily["ic"].mean()
        res[f"icir_{year}"] = yr_daily["ic"].mean() / yr_daily["ic"].std() if yr_daily["ic"].std() > 0 else 0
        res[f"rank_ic_{year}"] = yr_daily["rank_ic"].mean()
        res[f"rank_icir_{year}"] = yr_daily["rank_ic"].mean() / yr_daily["rank_ic"].std() if yr_daily["rank_ic"].std() > 0 else 0

    return res


# ═══════════════════════════════════════════════════════════════════
# 7. Run experiments
# ═══════════════════════════════════════════════════════════════════

# Only last windows for 2024-2025 focus (smoke test: first 5 of those)
TAIL = 15
tail_windows = windows.iloc[-TAIL:].reset_index(drop=True)
print(f"\nUsing last {TAIL} windows (focus 2024-2025)")

EXPERIMENTS = [
    ("baseline_current", None, None),       # from store
    ("train_3y", None, 756),                 # 756 trading days ~3y
    ("train_5y", None, 1260),               # 1260 trading days ~5y
    ("decay_2y", None, 504),                # 2y window + decay hl=730
    ("baseline+market", None, 504),         # 2y + market features
]

results = []
for tag, _, limit_days in EXPERIMENTS:
    print(f"\n{'=' * 60}")
    print(f"Running: {tag}")
    print(f"{'=' * 60}")
    t0 = time.time()
    all_preds = []
    use_market = "market" in tag
    use_decay = "decay" in tag

    for i, w in tail_windows.iterrows():
        train_start, train_end = w["train_start"], w["train_end"]
        pred_start, pred_end = w["predict_start"], w["predict_end"]

        # ── Label maturity: training data must be at predict_start - 180d ──
        maturity_cutoff = get_maturity_cutoff(predict_start)
        # Compute adjusted train start for limited history
        if limit_days:
            end_idx = cal_str.index(train_end) if train_end in cal_str else len(cal_str) - 1
            start_idx = max(0, end_idx - limit_days)
            adj_train_start = cal_str[start_idx]
        else:
            adj_train_start = train_start

        # Load features (need enough lookback for rolling features)
        load_start = min(adj_train_start, maturity_cutoff)
        load_start = min(load_start, "2018-01-02")
        frame = load_features(load_start, pred_end)

        if use_market:
            frame = add_market_state_features(frame)
            extra_feats = MARKET_FEATURES
        else:
            extra_feats = None

        # Split: train only on maturity-safe data
        train = frame[frame["trade_date"].between(adj_train_start, maturity_cutoff)].copy()
        pred = frame[frame["trade_date"].between(pred_start, pred_end)].copy()

        # Verify no leakage
        if len(train) > 0:
            assert train["trade_date"].max() <= maturity_cutoff, \
                f"Leakage! max_train={train['trade_date'].max()} cutoff={maturity_cutoff}"

        if pred.empty:
            continue

        # Sample weight for decay
        sw = None
        if use_decay and len(train) > 0:
            train_dates = train["trade_date"].unique()
            last_date_idx = cal_str.index(maturity_cutoff) if maturity_cutoff in cal_str else -1
            train_idx = [cal_str.index(d) for d in train_dates if d in cal_str]
            if train_idx:
                last_idx = max(train_idx)
                ages = cal_str[last_idx] - pd.to_datetime(train_dates)
                sw = pd.Series(np.exp(-ages.dt.days / 730), index=train.index)

        result = train_predict(train, pred, sample_weight=sw, extra_feats=extra_feats)
        if not result.empty:
            all_preds.append(result)

        if (i + 1) % 5 == 0:
            print(f"  [{i+1}/{len(tail_windows)}] windows ({time.time()-t0:.0f}s)")

    if all_preds:
        combined = pd.concat(all_preds, ignore_index=True)
        combined = combined.merge(
            all_labels[["trade_date", "ts_code", "label_value"]],
            on=["trade_date", "ts_code"], how="left",
        )
        r = evaluate(combined, tag)
        results.append(r)
        print(f"  {tag}: IC={r['ic']:.4f} ICIR={r['icir']:.3f} RankIC={r['rank_ic']:.4f}")
    else:
        print(f"  {tag}: NO predictions")
    print(f"  Time: {time.time()-t0:.0f}s")

# ═══════════════════════════════════════════════════════════════════
# 8. Baseline
# ═══════════════════════════════════════════════════════════════════

bl_df = base_preds.merge(all_labels[["trade_date", "ts_code", "label_value"]],
                          on=["trade_date", "ts_code"], how="left")
bl = evaluate(bl_df, "baseline_current")
# Deduplicate if already in results
if not any(r["tag"] == "baseline_current" for r in results):
    results.insert(0, bl)
print(f"\nBaseline: IC={bl['ic']:.4f} ICIR={bl['icir']:.3f}")

# ═══════════════════════════════════════════════════════════════════
# 9. Output
# ═══════════════════════════════════════════════════════════════════

summary = pd.DataFrame(results)
summary.to_csv(OUT / "window_decay_summary.csv", index=False)
print(f"\nSaved: {OUT}/window_decay_summary.csv")

# Table
print(f"\n{'─' * 72}")
print(f"{'Variant':<25s} {'IC':>8s} {'ICIR':>8s} {'RankIC':>8s} {'Top20':>8s} {'Top20hit':>9s}")
print(f"{'─' * 72}")
for _, r in summary.iterrows():
    print(f"{r['tag']:<25s} {r.get('ic',0):>8.4f} {r.get('icir',0):>8.3f} "
          f"{r.get('rank_ic',0):>8.4f} {r.get('top20_mean',0):>8.4f} {r.get('top20_hit',0):>9.2%}")

print(f"\n{'─' * 20} Yearly IC ──")
for _, r in summary.iterrows():
    line = f"{r['tag']:<25s}"
    for y in ["2024", "2025"]:
        line += f"  IC{y}={r.get(f'ic_{y}',0):.4f}"
    print(line)

print("\n✅ Done")
