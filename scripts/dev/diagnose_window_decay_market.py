#!/usr/bin/env python3
"""诊断实验 3+4 — 训练窗口衰减 + market state features.

实验 3: train_3y (756d), train_5y (1260d), decay_1y, decay_2y
实验 4: baseline + market_state features

方式: 对每个 rolling window 分别训练 + 预测, 逐步填充.
已用 rolling_windows.csv 走窗口循环, cache ht 自动 / qlib fallback.
输出到 artifacts/diagnostics/v3a_liq_regime/
"""
from __future__ import annotations

import sys, time, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import lightgbm as lgb

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.label.store import LabelStore
from qsys.signal.store import SignalStore

OUT = Path("artifacts/diagnostics/v3a_liq_regime")
OUT.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────
BASELINE_FEATURES = "v3a_plus_liquidity_pure"
LABEL_ID = "fwd_ret_180d_raw"
UNIVERSE = "csi800"
N_ESTIMATORS = 300

# ═══════════════════════════════════════════════════════════════════
# 1. Load shared data
# ═══════════════════════════════════════════════════════════════════
print("=" * 60)
print("Loading shared data...")
print("=" * 60)

adapter = QlibAdapter()
adapter.init_qlib()

clean_features = FeatureListRegistry.load(BASELINE_FEATURES)
print(f"  Clean features: {len(clean_features)}")

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

# Calendar for limiting train size
cal_str = [str(c)[:10] for c in D.calendar(start_time="2010-01-01", end_time="2026-01-01", freq="day")]

# ── Load baseline from SignalStore ──
print("\nLoading baseline from SignalStore...")
BASELINE_RUN = "rolling__180d_v3a_plus_liquidity_pure__v3a_liq_pure_180d__fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31"
sig_store = SignalStore()
base_sig = sig_store.load_signal_run("fwd_ret_180d_raw__daily_zscore", BASELINE_RUN)
if base_sig is not None:
    base_sig["ts_code"] = base_sig["instrument"]
    base_preds = base_sig[["trade_date", "ts_code", "score"]].merge(
        all_labels[["trade_date", "ts_code", "label_value"]], on=["trade_date", "ts_code"], how="left",
    )
else:
    print("  ERROR: baseline signal not found!")
    base_preds = pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════
# 2. Evaluation helpers
# ═══════════════════════════════════════════════════════════════════

def calc_daily_ics(df: pd.DataFrame) -> pd.DataFrame:
    """Per trade_date IC + RankIC."""
    return df.groupby("trade_date").apply(
        lambda g: pd.Series({
            "ic": g["score"].corr(g["label_value"]),
            "rank_ic": g["score"].rank().corr(g["label_value"].rank()),
        }), include_groups=False
    ).reset_index().dropna(subset=["ic"])


def evaluate_predictions(df: pd.DataFrame, tag: str) -> dict:
    df = df.dropna(subset=["label_value"]).copy()
    if df.empty or len(df) < 100:
        return {"tag": tag, "error": "no data"}

    # For TopK: score is already daily_zscore, so groupby date top-k
    df_dates = df["trade_date"].unique()
    topk_results = []
    for k in [20, 50, 100]:
        topk_vals = []
        for dt in df_dates:
            day_slice = df[df["trade_date"] == dt].sort_values("score", ascending=False).head(k)
            topk_vals.extend(day_slice["label_value"].tolist())
        vals = pd.Series(topk_vals).dropna()
        if len(vals) > 0:
            topk_results.append({
                "k": k, "mean_ret": vals.mean(), "median_ret": vals.median(),
                "hit_rate": (vals > 0).mean(), "bad_rate": (vals < 0).mean(),
                "gt30": (vals > 0.3).mean(),
            })

    daily = calc_daily_ics(df)
    res = {"tag": tag, "n_days": len(daily), "n_obs": len(df)}
    res["ic"] = daily["ic"].mean()
    res["icir"] = daily["ic"].mean() / daily["ic"].std() if daily["ic"].std() > 0 else 0
    res["rank_ic"] = daily["rank_ic"].mean()
    res["rank_icir"] = daily["rank_ic"].mean() / daily["rank_ic"].std() if daily["rank_ic"].std() > 0 else 0
    for tr in topk_results:
        res[f"top{tr['k']}_mean"] = tr["mean_ret"]
        res[f"top{tr['k']}_hit"] = tr["hit_rate"]
        res[f"top{tr['k']}_bad"] = tr["bad_rate"]

    for year in sorted(set(d.str[:4] for d in df_dates if isinstance(d, str))):
        yr = df[df["trade_date"].str[:4] == year]
        if len(yr) < 200: continue
        yr_daily = calc_daily_ics(yr)
        res[f"ic_{year}"] = yr_daily["ic"].mean()
        res[f"icir_{year}"] = yr_daily["ic"].mean() / yr_daily["ic"].std() if yr_daily["ic"].std() > 0 else 0
        res[f"rank_ic_{year}"] = yr_daily["rank_ic"].mean()
    return res


# ═══════════════════════════════════════════════════════════════════
# 3. Window training helpers
# ═══════════════════════════════════════════════════════════════════

def load_window_feature_frame(start: str, end: str) -> pd.DataFrame:
    """Load features for [start, end] from per-window cache (秒级)."""
    import hashlib
    cache_keys = [
        f"__window__::{start}::{end}",
        f"__window__::{start}::{(pd.Timestamp(end) + pd.Timedelta(days=30)).strftime('%Y-%m-%d')}",
    ]
    for raw_key in cache_keys:
        key = hashlib.sha256(raw_key.encode()).hexdigest()[:16]
        cp = Path("data/feature_cache/per_window") / f"{key}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
            df["trade_date"] = df["trade_date"].astype(str).str[:10]
            keep = {"trade_date", "instrument"} | set(clean_features + ["$close", "$volume", "$amount"])
            avail = [c for c in df.columns if c in keep]
            df = df[avail]
            df["ts_code"] = df["instrument"]
            return df
    raw = adapter.get_features(UNIVERSE, clean_features + ["$close", "$volume", "$amount"],
                               start_time=start, end_time=end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["ts_code"] = frame["instrument"] if "ts_code" not in frame.columns else frame["ts_code"]
    return frame


def add_market_state_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add market-level features to frame (in-place or copy)."""
    out = frame.copy()
    features = ["market_ret_60d", "market_ret_120d", "market_amount_percentile_60d",
                "market_breadth_60d", "industry_dispersion_60d"]
    for f in features:
        out[f] = np.nan

    dates = sorted(out["trade_date"].unique())
    for dt in dates:
        hist = out[out["trade_date"] <= dt]
        if len(hist) < 60:
            continue

        # Market ret 60d/120d: equal-weight universe
        daily_close = hist.groupby("trade_date")["$close"].mean().sort_index()
        idx_vals = daily_close.values
        if len(idx_vals) > 60:
            m60 = idx_vals[-61] / idx_vals[-1] - 1  # alt: sum of pct_changes
            out.loc[out["trade_date"] == dt, "market_ret_60d"] = \
                pd.Series(idx_vals[-60:]).pct_change().sum()
        if len(idx_vals) > 120:
            out.loc[out["trade_date"] == dt, "market_ret_120d"] = \
                pd.Series(idx_vals[-120:]).pct_change().sum()

        # Market amount percentile
        daily_amt = hist.groupby("trade_date")["$amount"].sum().sort_index()
        if len(daily_amt) > 60:
            recent = daily_amt.iloc[-60:]
            pctile = (recent < daily_amt.iloc[-1]).mean()
            out.loc[out["trade_date"] == dt, "market_amount_percentile_60d"] = pctile

        # Breadth: fraction of stocks with today up
        dt_slice = hist[hist["trade_date"] == dt]
        if len(dt_slice) > 0:
            today_close = out[out["trade_date"] == dt][["ts_code", "$close"]].set_index("ts_code")["$close"]
            prev_date = hist[hist["trade_date"] < dt]["trade_date"].max()
            if prev_date is not None:
                prev_close = out[out["trade_date"] == prev_date][["ts_code", "$close"]].set_index("ts_code")["$close"]
                common = today_close.index.intersection(prev_close.index)
                if len(common) > 0:
                    up_frac = (today_close.loc[common] > prev_close.loc[common]).mean()
                    # Breadth over 60d
                    out.loc[out["trade_date"] == dt, "market_breadth_60d"] = up_frac

        # Industry dispersion placeholder
        if "industry" in out.columns:
            ind_ret = hist.groupby(["trade_date", "industry"]).apply(
                lambda g: g["$close"].mean(), include_groups=False).groupby("trade_date").std()
            if len(ind_ret) > 60:
                out.loc[out["trade_date"] == dt, "industry_dispersion_60d"] = \
                    ind_ret.iloc[-60:].mean()

    return out


def train_predict_window(train_df: pd.DataFrame, pred_df: pd.DataFrame,
                         time_decay: int | None = None,
                         extra_feats: list[str] | None = None,
                         n_estimators: int = N_ESTIMATORS) -> pd.DataFrame:
    """Train on train_df, predict on pred_df, return scores."""
    feats = clean_features + (extra_feats or [])

    train = train_df.merge(
        all_labels[["trade_date", "ts_code", "label_value"]],
        on=["trade_date", "ts_code"], how="left",
    )
    has_label = train["label_value"].notna()
    X_tr = train.loc[has_label, feats].fillna(0.0).astype(np.float32)
    y_tr = train.loc[has_label, "label_value"].astype(float)

    if len(y_tr) < 50:
        return pd.DataFrame()

    # Robust zscore
    from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform
    center, scale = robust_zscore_fit(X_tr)
    Xz = robust_zscore_transform(X_tr, center, scale)

    N = len(Xz)
    vs = min(20000, int(N * 0.15))
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_tr.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_tr.iloc[-vs:].values)

    # Sample weight for time-decay
    if time_decay is not None:
        train_dates = train.loc[has_label, "trade_date"]
        last_date = pd.Timestamp(train_dates.max())
        ages = train_dates.map(lambda d: (last_date - pd.Timestamp(d)).days)
        w = np.exp(-ages / time_decay)
        train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_tr.iloc[:-vs].values,
                                 weight=w.iloc[:-vs].values)
        # Also apply to val
        val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_tr.iloc[-vs:].values,
                               weight=np.ones(vs))

    params = {
        "objective": "regression",
        "metric": "mse",
        "colsample_bytree": 0.8879, "learning_rate": 0.0421,
        "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
        "max_depth": 8, "num_leaves": 210, "num_threads": 8,
        "verbosity": -1, "seed": 42,
    }
    model = lgb.train(params, train_data, num_boost_round=n_estimators,
                      valid_sets=[val_data],
                      callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)])

    # Predict
    X_pred = pred_df[feats].fillna(0.0).astype(np.float32)
    Xp_z = robust_zscore_transform(X_pred, center, scale)
    pred = pd.DataFrame({
        "trade_date": pred_df["trade_date"].values,
        "ts_code": pred_df["ts_code"].values,
        "score": model.predict(Xp_z.values),
    })
    return pred


# ═══════════════════════════════════════════════════════════════════
# 4. Run experiments
# ═══════════════════════════════════════════════════════════════════

all_configs = [
    ("baseline_current", None, None, None),  # from store
    ("train_3y", None, 756, None),
    ("train_5y", None, 1260, None),
    ("decay_1y", None, None, 365),
    ("decay_2y", None, None, 730),
    ("baseline+market", None, None, None),
    # ("decay_2y+market", None, None, 730),
]

results = []

# Baseline from store
print("\n" + "=" * 60)
print("Baseline from SignalStore")
print("=" * 60)
bl = evaluate_predictions(base_preds, "baseline_current")
results.append(bl)
print(f"  IC={bl.get('ic',0):.4f} ICIR={bl.get('icir',0):.3f} RankIC={bl.get('rank_ic',0):.4f}")

for tag, _, limit_days, decay_hl in all_configs[1:]:
    if tag == "baseline_current":
        continue
    if tag == "baseline+market":
        limit_days = 504  # same as baseline

    print(f"\n{'=' * 60}")
    print(f"Experiment: {tag}")
    print(f"  limit_days={limit_days}, decay_hl={decay_hl}, extra_feats=market")
    print(f"{'=' * 60}")
    t0 = time.time()
    all_preds = []

    use_market = "market" in tag

    for i, w in windows.iterrows():
        train_start, train_end = w["train_start"], w["train_end"]
        pred_start, pred_end = w["predict_start"], w["predict_end"]

        # Adjust train window if limiting
        if limit_days:
            # Count trading days back from train_end
            end_idx = cal_str.index(train_end) if train_end in cal_str else len(cal_str) - 1
            start_idx = max(0, end_idx - limit_days)
            adjusted_start = cal_str[start_idx]
        else:
            adjusted_start = train_start

        # Load features
        frame = load_window_feature_frame(adjusted_start, pred_end)

        if use_market:
            frame = add_market_state_features(frame)
            extra_feats_mkt = ["market_ret_60d", "market_ret_120d",
                               "market_amount_percentile_60d",
                               "market_breadth_60d", "industry_dispersion_60d"]
        else:
            extra_feats_mkt = None

        train = frame[frame["trade_date"].between(adjusted_start, train_end)]
        pred = frame[frame["trade_date"].between(pred_start, pred_end)]

        result = train_predict_window(train, pred, time_decay=decay_hl, extra_feats=extra_feats_mkt)
        if not result.empty:
            all_preds.append(result)

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(windows)}] windows done ({time.time()-t0:.0f}s)")

    if all_preds:
        combined = pd.concat(all_preds, ignore_index=True)
        combined = combined.merge(
            all_labels[["trade_date", "ts_code", "label_value"]],
            on=["trade_date", "ts_code"], how="left",
        )
        r = evaluate_predictions(combined, tag)
        results.append(r)
        print(f"  [{tag}] IC={r.get('ic',0):.4f} ICIR={r.get('icir',0):.3f} RankIC={r.get('rank_ic',0):.4f}")
    else:
        print(f"  [{tag}] NO PREDICTIONS")
    print(f"  Total time: {time.time() - t0:.0f}s")


# ═══════════════════════════════════════════════════════════════════
# 5. Output
# ═══════════════════════════════════════════════════════════════════

summary = pd.DataFrame(results)
out_path = OUT / "window_decay_summary.csv"
summary.to_csv(out_path, index=False)
print(f"\nSaved: {out_path}")

# Final table
ic_fields = ["tag", "ic", "icir", "rank_ic", "rank_icir",
             "top20_mean", "top20_hit", "top20_bad",
             "top50_mean", "top50_hit", "top100_mean",
             "ic_2024", "ic_2025"]

print(f"\n{'=' * 80}")
print("FINAL SUMMARY")
print(f"{'=' * 80}")
for _, r in summary.iterrows():
    print(f"\n{'─' * 60}")
    print(f"  {r['tag']}")
    print(f"  Overall: IC={r.get('ic',0):.4f} ICIR={r.get('icir',0):.3f} RankIC={r.get('rank_ic',0):.4f}")
    print(f"  2024: IC={r.get('ic_2024',0):.4f} | 2025: IC={r.get('ic_2025',0):.4f}")
    print(f"  Top20 mean={r.get('top20_mean',0):.4f} hit={r.get('top20_hit',0):.2%}")
    print(f"  Top50 mean={r.get('top50_mean',0):.4f}")
    print(f"  Top100 mean={r.get('top100_mean',0):.4f}")

print(f"\n{'=' * 80}")
print("✅ Done")
print(f"{'=' * 80}")
