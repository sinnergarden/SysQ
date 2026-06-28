#!/usr/bin/env python3
"""Train v3a+liquidity model on TWO separate labels then predict 2026-06-02.

Label train cutoffs (from LabelStore coverage):
  fwd_ret_180d_raw → train to 2025-08-27 (last available 180d label)
  fwd_ret_60d_raw  → train to 2026-03-03 (last available 60d label)

Output:
  experiments/v3a_liq_20251210/model_{tag}.txt  — saved Booster
  experiments/v3a_liq_20251210/center_{tag}.json / scale_{tag}.json — zscore params
  experiments/v3a_liq_20251210/features.json — feature list
  stdout — TOP 20 per label on 2026-06-02
"""
import sys, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd
import lightgbm as lgb

from qsys.data.adapter import QlibAdapter
from qsys.signal.alpha_v1.training import train_model, predict_model
from qsys.feature.registry import FeatureListRegistry
from qsys.label.store import LabelStore
from qsys.utils.logger import log

# ── Config ──────────────────────────────────────────────────────────────
UNIVERSE = "csi800"
N_ESTIMATORS = 300
PREDICT_DATE = "2026-06-02"
LOOKBACK_FOR_PREDICT = 300  # extra days before predict for rolling features

# Each (label_id, train_end, tag)
TRAIN_CONFIGS = [
    ("fwd_ret_180d_raw", "2025-08-27", "180d"),
    ("fwd_ret_60d_raw",  "2026-03-03", "60d"),
]

OUTPUT_DIR = Path("experiments/v3a_liq_20251210")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════
# 1. Load feature list
# ═══════════════════════════════════════════════════════════════════════
log.info("Loading feature list: v3a_plus_liquidity")
clean_features = FeatureListRegistry.load("v3a_plus_liquidity")
log.info(f"  → {len(clean_features)} features")

adapter = QlibAdapter()
adapter.init_qlib()


def load_frame(start: str, end: str) -> pd.DataFrame:
    """Load features from Qlib, return with trade_date + instrument + clean_features."""
    log.info(f"Loading Qlib data [{start}, {end}]")
    raw = adapter.get_features(UNIVERSE, clean_features + ["$close"],
                               start_time=start, end_time=end)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    if "instrument" not in frame.columns and "ts_code" in frame.columns:
        frame = frame.rename(columns={"ts_code": "instrument"})
    log.info(f"  → {len(frame)} rows, {len(frame.columns)} cols")
    return frame


def train_model_for_label(frame: pd.DataFrame, label_id: str, train_end: str, tag: str):
    """Train one LightGBM model."""
    log.info("=" * 50)
    log.info(f"Training: {tag} (label={label_id}, train_end={train_end})")

    label_df = LabelStore().load_labels(label_id)
    train = frame[frame["trade_date"] <= train_end].copy().merge(
        label_df[["trade_date", "instrument", "label_value"]],
        on=["trade_date", "instrument"], how="left",
    )
    n_label = train["label_value"].notna().sum()
    log.info(f"  Train samples with labels: {n_label}")

    y_valid = train["label_value"].notna()
    X_tr = train[clean_features].fillna(0.0).astype(np.float32)
    y_tr = train.loc[y_valid, "label_value"].astype(float)

    if len(y_tr) == 0:
        raise ValueError(f"Zero training labels for {label_id}")

    t0 = time.time()
    model, center, scale = train_model(
        X_tr.loc[y_tr.index], y_tr, tag,
        n_estimators=N_ESTIMATORS,
    )
    elapsed = time.time() - t0
    log.info(f"  Trained in {elapsed:.1f}s - {len(y_tr)} samples, {X_tr.shape[1]} features")

    # Save
    model.save_model(str(OUTPUT_DIR / f"model_{tag}.txt"))
    center.to_json(OUTPUT_DIR / f"center_{tag}.json")
    scale.to_json(OUTPUT_DIR / f"scale_{tag}.json")
    log.info(f"  Saved to {OUTPUT_DIR}/")
    return model, center, scale


def predict_date(frame: pd.DataFrame, target: str, model, center, scale, tag: str):
    """Predict for a single date; return DataFrame sorted by score descending."""
    sub = frame[frame["trade_date"] == target].copy()
    log.info(f"Predicting {tag} on {target}: {len(sub)} stocks")

    scores = predict_model(
        model, center, scale,
        sub[clean_features].fillna(0.0).astype(np.float32)
    ).values

    result = pd.DataFrame({
        "instrument": sub["instrument"].values,
        "score": scores,
    })

    # Attach stock name (non-critical, best-effort)
    try:
        names = pd.read_parquet("data/tushare/stock_basic.parquet",
                                columns=["ts_code", "name"])
        names["ts_code"] = names["ts_code"].str.replace(".", "", regex=False)
        result = result.merge(names.rename(columns={"ts_code": "instrument"}),
                              on="instrument", how="left")
    except Exception:
        result["name"] = ""

    return result.sort_values("score", ascending=False).reset_index(drop=True)


def main():
    from qlib.data import D

    # Build calendar once
    cal = D.calendar(start_time="2010-01-01", end_time="2026-06-30", freq="day")
    cal_str = [str(c)[:10] for c in cal]

    # ── Phase 1: train each label ──
    results = {}
    for label_id, train_end, tag in TRAIN_CONFIGS:
        # Determine train_start (504 trading days before train_end)
        train_end_idx = cal_str.index(train_end) if train_end in cal_str else len(cal_str) - 1
        train_start_idx = max(0, train_end_idx - 504 - 1)
        train_start = cal_str[train_start_idx]
        log.info(f"Train window: {train_start} → {train_end} ({train_end_idx - train_start_idx} days)")

        # Load data once for all labels if possible
        frame = load_frame(train_start, train_end)
        model, center, scale = train_model_for_label(frame, label_id, train_end, tag)

        # ── Phase 2: load predict data and infer ──
        # Need lookback for rolling features
        pred_start_idx = max(0, cal_str.index(PREDICT_DATE) - LOOKBACK_FOR_PREDICT)
        pred_start = cal_str[pred_start_idx]

        pred_frame = load_frame(pred_start, PREDICT_DATE)
        result = predict_date(pred_frame, PREDICT_DATE, model, center, scale, tag)
        results[tag] = result

    # Save feature list
    json.dump(clean_features, open(OUTPUT_DIR / "features.json", "w"), indent=2)

    # ── Output TOP 20 ──
    print("\n" + "=" * 72)
    print(f"📊 v3a+liquidity TOP 20 Prediction — {PREDICT_DATE}")
    print("=" * 72)

    for tag in ["180d", "60d"]:
        if tag not in results:
            continue
        result = results[tag]
        print(f"\n{'─' * 72}")
        print(f"🏷️  {'Label: 180d delayed return' if tag == '180d' else 'Label: 60d delayed return'}")
        print(f"   Model trained to: {[c[1] for c in TRAIN_CONFIGS if c[2]==tag][0]}")
        print(f"{'─' * 72}")
        print(f"  {'Rank':>4}  {'Stock':<12}  {'Name':<14}  {'Score':>10}")
        print(f"  {'────':>4}  {'────':<12}  {'────':<14}  {'─────':>10}")
        for i, (_, r) in enumerate(result.head(20).iterrows()):
            name = str(r.get("name", ""))[:12] if pd.notna(r.get("name", "")) else ""
            print(f"  {i+1:>4}  {r['instrument']:<12}  {name:<14}  {r['score']:>10.4f}")

    print(f"\n✅ Models saved to {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
