#!/usr/bin/env python3
"""60d top-weighted LightGBM — sample weight experiment.

Trains 4 variants with different label-based sample weights:
  baseline_no_weight, top10pct_weight_3x, top20pct_weight_2x, top10pct_3x_top20pct_2x

Uses per-window cache → no feature recomputation.
Outputs predictions to artifacts/diagnostics/60d_top_weighted/predictions/
"""
from __future__ import annotations

import hashlib, json, sys, time, warnings
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
from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform

P = Path(__file__).resolve().parents[2]
CACHE = P / "data/feature_cache/per_window"
OUT = P / "artifacts/diagnostics/60d_top_weighted"
PRED_DIR = OUT / "predictions"
PRED_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────
FEATURE_LIST = "v3a_plus_liquidity_financial_rc"
LABEL_ID = "fwd_ret_60d_raw"
UNIVERSE = "csi800"
N_ESTIMATORS = 300
WINDOW_FILE = "data/research/experiments/60d_v3a_plus_liquidity_pure/rolling_windows.csv"

LGB_PARAMS = {
    "objective": "regression", "metric": "mse",
    "colsample_bytree": 0.8879, "learning_rate": 0.0421,
    "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
    "max_depth": 8, "num_leaves": 210, "num_threads": 8,
    "verbosity": -1, "seed": 42,
}

WEIGHT_SCHEMES = {
    "baseline_no_weight": None,
    "top10pct_weight_3x": [(0.90, 3.0)],
    "top20pct_weight_2x": [(0.80, 2.0)],
    "top10pct_3x_top20pct_2x": [(0.90, 3.0), (0.80, 2.0)],
}

# ═══════════════════════════════════════════════════════════════════
# 1. Load shared data
# ═══════════════════════════════════════════════════════════════════
print("=" * 60)
print("Loading shared data...")
print("=" * 60)

adapter = QlibAdapter()
adapter.init_qlib()

clean_features = FeatureListRegistry.load(FEATURE_LIST)
print(f"  Features: {len(clean_features)}")

all_labels = LabelStore(root=str(P / "data/research")).load_labels(LABEL_ID)
all_labels["trade_date"] = all_labels["trade_date"].astype(str).str[:10]
all_labels["ts_code"] = all_labels["instrument"]

cal = [str(c)[:10] for c in D.calendar(end_time="2026-06-30", freq="day")]
cal_idx = {d: i for i, d in enumerate(cal)}

windows = pd.read_csv(P / WINDOW_FILE)
print(f"  Windows: {len(windows)}")

# Label date range
label_max = all_labels["trade_date"].max()
print(f"  Label range: {all_labels['trade_date'].min()} → {label_max}")


# ═══════════════════════════════════════════════════════════════════
# 2. Helpers
# ═══════════════════════════════════════════════════════════════════

def _prev_trading_date(d: str, n: int) -> str:
    idx = cal_idx.get(d)
    return cal[max(0, idx - n)] if idx is not None else d


def load_cache(start: str, end: str) -> pd.DataFrame:
    """Load features from per-window cache. Raises if cache miss."""
    for raw in [
        f"__window__::{start}::{end}",
        f"__window__::{start}::{(pd.Timestamp(end) + pd.Timedelta(days=30)).strftime('%Y-%m-%d')}",
    ]:
        k = hashlib.sha256(raw.encode()).hexdigest()[:16]
        cp = CACHE / f"{k}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
            df["trade_date"] = df["trade_date"].astype(str).str[:10]
            keep = {"trade_date", "instrument"} | set(clean_features + ["$close"])
            df = df[[c for c in df.columns if c in keep]]
            df["ts_code"] = df["instrument"]
            return df
    raise FileNotFoundError(f"Cache miss for {start}..{end} — run 60d superset write-through first")


def compute_sample_weight(y_tr: pd.Series, train_dates: pd.Series, scheme_name: str) -> pd.Series:
    """Compute per-row sample weight based on label rank within each trade_date."""
    if scheme_name == "baseline_no_weight":
        return pd.Series(1.0, index=y_tr.index)

    weight_config = WEIGHT_SCHEMES[scheme_name]
    w = pd.Series(1.0, index=y_tr.index)
    tmp = pd.DataFrame({"label_value": y_tr, "trade_date": train_dates})
    for dt in tmp["trade_date"].unique():
        mask = tmp["trade_date"] == dt
        sub = tmp.loc[mask]
        if len(sub) < 10:
            continue
        pct = sub["label_value"].rank(pct=True)
        for threshold, weight_val in weight_config:
            mask2 = mask.copy()
            mask2[mask] = pct >= threshold
            w.loc[mask & mask2] = weight_val
    return w


# ═══════════════════════════════════════════════════════════════════
# 3. Train + predict per window and variant
# ═══════════════════════════════════════════════════════════════════

def train_and_predict(
    train_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    scheme: str,
) -> pd.DataFrame:
    """Train one window with sample_weight; return predictions."""
    train = train_df.merge(
        all_labels[["trade_date", "ts_code", "label_value"]],
        on=["trade_date", "ts_code"], how="left",
    )
    has_y = train["label_value"].notna()
    X_tr = train.loc[has_y, clean_features].fillna(0.0).astype(np.float32)
    y_tr = train.loc[has_y, "label_value"].astype(float)
    t_dates = train.loc[has_y, "trade_date"]

    if len(y_tr) < 50:
        return pd.DataFrame()

    sample_weight = compute_sample_weight(y_tr, t_dates, scheme)

    center, scale = robust_zscore_fit(X_tr)
    Xz = robust_zscore_transform(X_tr, center, scale)
    N = len(Xz)
    vs = min(20000, int(N * 0.15))

    train_data = lgb.Dataset(
        Xz.iloc[:-vs].values, label=y_tr.iloc[:-vs].values,
        weight=sample_weight.iloc[:-vs].values,
    )
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_tr.iloc[-vs:].values)

    model = lgb.train(
        LGB_PARAMS, train_data,
        num_boost_round=N_ESTIMATORS,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
    )

    X_pred = pred_df[clean_features].fillna(0.0).astype(np.float32)
    Xp_z = robust_zscore_transform(X_pred, center, scale)
    scores = model.predict(Xp_z.values)

    return pd.DataFrame({
        "trade_date": pred_df["trade_date"].values,
        "ts_code": pred_df["ts_code"].values,
        "score": scores,
    })


# ═══════════════════════════════════════════════════════════════════
# 4. Run all windows for each scheme
# ═══════════════════════════════════════════════════════════════════

results = {}
for scheme in WEIGHT_SCHEMES:
    print(f"\n{'=' * 60}")
    print(f"  Scheme: {scheme}")
    print(f"{'=' * 60}")
    t0 = time.time()
    all_preds = []

    for i, w in windows.iterrows():
        ts_orig, te, ps, pe = w["train_start"], w["train_end"], w["predict_start"], w["predict_end"]

        # ── Label maturity: train only until predict_start - 60d ──
        maturity_cutoff = _prev_trading_date(ps, 60)
        load_end = (pd.Timestamp(pe) + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        frame = load_cache(ts_orig, load_end)

        train = frame[frame["trade_date"].between(ts_orig, maturity_cutoff)].copy()
        pred = frame[frame["trade_date"].between(ps, pe)].copy()

        if pred.empty:
            continue

        result = train_and_predict(train, pred, scheme)
        if not result.empty:
            all_preds.append(result)

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(windows)}] ({time.time()-t0:.0f}s)", flush=True)

    if all_preds:
        combined = pd.concat(all_preds, ignore_index=True)
        out_path = PRED_DIR / f"{scheme}.parquet"
        combined.to_parquet(out_path, index=False)
        print(f"  Saved: {out_path} ({len(combined)} rows)", flush=True)
    else:
        print(f"  NO predictions for {scheme}", flush=True)

    # Weight distribution summary
    print(f"  Total time: {time.time()-t0:.0f}s", flush=True)

print(f"\n{'=' * 60}")
print("  Done — predictions saved to", PRED_DIR)
print(f"{'=' * 60}")
