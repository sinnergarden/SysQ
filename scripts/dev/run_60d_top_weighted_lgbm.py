#!/usr/bin/env python3
"""60d top-weighted LightGBM — sample weight experiment.

Research artifact-level signal storage.
NOT written to production SignalStore.
Each scheme is an independent model idea; same scheme rerun overwrites.
No timestamp in filename.

Usage:
    python scripts/dev/run_60d_top_weighted_lgbm.py              # full run
    python scripts/dev/run_60d_top_weighted_lgbm.py --smoke      # last 2 windows

Output:
    artifacts/diagnostics/60d_top_weighted/
    ├── predictions/{scheme}.parquet       (full) or predictions_smoke/ (smoke)
    └── weight_scheme_summary.csv
"""
from __future__ import annotations

import argparse, hashlib, sys, time, warnings
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

# Sorted by threshold DESC — highest threshold last, overwrites lower ones
WEIGHT_SCHEMES = {
    "baseline_no_weight": None,
    "top10pct_weight_3x": [(0.90, 3.0)],
    "top20pct_weight_2x": [(0.80, 2.0)],
    "top10pct_3x_top20pct_2x": [(0.80, 2.0), (0.90, 3.0)],  # low → high: high overwrites
}


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════
parser = argparse.ArgumentParser()
parser.add_argument("--smoke", action="store_true", help="Run only last 2 windows for testing")
args = parser.parse_args()

OUT_SUFFIX = "predictions_smoke" if args.smoke else "predictions"
OUT = P / "artifacts/diagnostics/60d_top_weighted"
PRED_DIR = OUT / OUT_SUFFIX
PRED_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════
# 1. Load shared data
# ═══════════════════════════════════════════════════════════════════
print("=" * 60)
print("Loading shared data..." + (" (SMOKE)" if args.smoke else ""))
print("=" * 60)

adapter = QlibAdapter()
adapter.init_qlib()

clean_features = FeatureListRegistry.load(FEATURE_LIST)
print(f"  Features: {len(clean_features)} from {FEATURE_LIST}")

all_labels = LabelStore(root=str(P / "data/research")).load_labels(LABEL_ID)
all_labels["trade_date"] = all_labels["trade_date"].astype(str).str[:10]
all_labels["ts_code"] = all_labels["instrument"]

cal = [str(c)[:10] for c in D.calendar(end_time="2026-06-30", freq="day")]
cal_idx = {d: i for i, d in enumerate(cal)}

windows = pd.read_csv(P / WINDOW_FILE)
if args.smoke:
    windows = windows.iloc[-2:].reset_index(drop=True)
print(f"  Windows: {len(windows)}")


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
    """Compute per-row sample weight based on label rank within each trade_date.

    Weight config is sorted by threshold ASC (low→high) so that higher
    thresholds overwrite lower ones, ensuring graduated schemes work correctly
    (e.g. top10% gets 3x, not overwritten to 2x).
    """
    if scheme_name == "baseline_no_weight":
        w = pd.Series(1.0, index=y_tr.index)
        return w

    weight_config = sorted(WEIGHT_SCHEMES[scheme_name], key=lambda x: x[0])
    w = pd.Series(1.0, index=y_tr.index)
    tmp = pd.DataFrame({"label_value": y_tr, "trade_date": train_dates})
    for dt in tmp["trade_date"].unique():
        mask = tmp["trade_date"] == dt
        sub = tmp.loc[mask]
        if len(sub) < 10:
            continue
        pct = sub["label_value"].rank(pct=True)
        for threshold, weight_val in weight_config:
            # Higher threshold overwrites — applies to rows >= threshold
            w.loc[mask & (pct >= threshold)] = weight_val
    return w


# ═══════════════════════════════════════════════════════════════════
# 3. Train + predict per window
# ═══════════════════════════════════════════════════════════════════

def train_and_predict(
    train_df: pd.DataFrame,
    pred_df: pd.DataFrame,
    scheme: str,
) -> pd.DataFrame:
    """Train one window with sample_weight; return predictions + weight stats."""
    train = train_df.merge(
        all_labels[["trade_date", "ts_code", "label_value"]],
        on=["trade_date", "ts_code"], how="left",
    )
    has_y = train["label_value"].notna()
    X_tr = train.loc[has_y, clean_features].fillna(0.0).astype(np.float32)
    y_tr = train.loc[has_y, "label_value"].astype(float)
    t_dates = train.loc[has_y, "trade_date"]

    if len(y_tr) < 50:
        return pd.DataFrame(), {}

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

    # Weight distribution stats for this window
    w1 = (sample_weight == 1.0).sum()
    w2 = (sample_weight == 2.0).sum()
    w3 = (sample_weight == 3.0).sum()

    return pd.DataFrame({
        "trade_date": pred_df["trade_date"].values,
        "ts_code": pred_df["ts_code"].values,
        "score": scores,
    }), {"n_rows": len(sample_weight), "w1": int(w1), "w2": int(w2), "w3": int(w3),
         "mean_w": float(sample_weight.mean()), "max_w": float(sample_weight.max())}


# ═══════════════════════════════════════════════════════════════════
# 4. Run all schemes
# ═══════════════════════════════════════════════════════════════════

weight_summary_rows = []

for scheme in WEIGHT_SCHEMES:
    print(f"\n{'=' * 60}")
    print(f"  Scheme: {scheme}")
    print(f"{'=' * 60}")
    t0 = time.time()
    all_preds = []
    scheme_w_acc = {"n_rows": 0, "w1": 0, "w2": 0, "w3": 0, "mean_w_sum": 0.0, "max_w": 0.0, "wind_count": 0}

    for i, w in windows.iterrows():
        ts_orig, te, ps, pe = w["train_start"], w["train_end"], w["predict_start"], w["predict_end"]

        maturity_cutoff = _prev_trading_date(ps, 60)
        load_end = (pd.Timestamp(pe) + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        frame = load_cache(ts_orig, load_end)

        train = frame[frame["trade_date"].between(ts_orig, maturity_cutoff)].copy()
        pred = frame[frame["trade_date"].between(ps, pe)].copy()
        if pred.empty:
            continue

        result, w_stats = train_and_predict(train, pred, scheme)
        if not result.empty:
            all_preds.append(result)
            scheme_w_acc["n_rows"] += w_stats["n_rows"]
            scheme_w_acc["w1"] += w_stats["w1"]
            scheme_w_acc["w2"] += w_stats["w2"]
            scheme_w_acc["w3"] += w_stats["w3"]
            scheme_w_acc["mean_w_sum"] += w_stats["mean_w"] * w_stats["n_rows"]
            scheme_w_acc["max_w"] = max(scheme_w_acc["max_w"], w_stats["max_w"])
            scheme_w_acc["wind_count"] += 1

        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(windows)}] ({time.time()-t0:.0f}s)", flush=True)

    if all_preds:
        combined = pd.concat(all_preds, ignore_index=True)
        out_path = PRED_DIR / f"{scheme}.parquet"
        combined.to_parquet(out_path, index=False)
        print(f"  Saved: {out_path} ({len(combined)} rows)", flush=True)

        weight_summary_rows.append({
            "scheme": scheme, "windows": scheme_w_acc["wind_count"],
            "n_rows_total": scheme_w_acc["n_rows"],
            "weight_1_count": scheme_w_acc["w1"],
            "weight_2_count": scheme_w_acc["w2"],
            "weight_3_count": scheme_w_acc["w3"],
            "mean_weight": scheme_w_acc["mean_w_sum"] / scheme_w_acc["n_rows"] if scheme_w_acc["n_rows"] > 0 else 0,
            "max_weight": scheme_w_acc["max_w"],
        })
        print(f"  Weight dist: 1x={scheme_w_acc['w1']} 2x={scheme_w_acc['w2']} 3x={scheme_w_acc['w3']}",
              flush=True)
    else:
        print(f"  NO predictions for {scheme}", flush=True)

    print(f"  Time: {time.time()-t0:.0f}s", flush=True)

# ── Weight summary CSV ──
ws_df = pd.DataFrame(weight_summary_rows)
ws_path = OUT / "weight_scheme_summary.csv"
ws_df.to_csv(ws_path, index=False)
print(f"\nWeight summary → {ws_path}")
print(ws_df.to_string(index=False))

# ── Storage note ──
print(f"\n{'=' * 60}")
print("  Storage: research artifact-level signal (NOT production SignalStore)")
print("  Each scheme = independent model idea; same scheme rerun overwrites")
print(f"  Predictions → {PRED_DIR}")
print(f"{'=' * 60}")
