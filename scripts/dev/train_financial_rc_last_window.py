#!/usr/bin/env python3
"""Train 60d/180d financial_rc model once on latest window + save ckpt for inference."""
import json, sys, hashlib, copy
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np
import pandas as pd
import lightgbm as lgb

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.label.store import LabelStore
from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform
from qsys.signal.alpha_v1.training import train_model

TRADE_DATE = "2026-07-24"
TRAIN_WINDOW_DAYS = 504
FEATURE_LIST = "v3a_plus_liquidity_financial_rc"
UNIVERSE = "csi800"
MODEL_ROOT = Path("data/research/models")

SPECS = [
    {
        "tag": "60d",
        "exp_id": "60d_v3a_growth_financial",
        "label_id": "fwd_ret_60d_raw",
        "horizon": 60,
        "n_estimators": 300,
    },
    {
        "tag": "180d",
        "exp_id": "180d_v3a_growth_financial",
        "label_id": "fwd_ret_180d_raw",
        "horizon": 180,
        "n_estimators": 300,
    },
]

QlibAdapter().init_qlib()
features = FeatureListRegistry.load(FEATURE_LIST)
print(f"Features: {len(features)}")

# Calendar
cal = [str(c)[:10] for c in D.calendar(start_time="2020-01-01", end_time=TRADE_DATE, freq="day")]
if TRADE_DATE not in cal:
    raise ValueError(f"{TRADE_DATE} not in calendar")
td_idx = cal.index(TRADE_DATE)

for spec in SPECS:
    tag = spec["tag"]
    horizon = spec["horizon"]
    exp_id = spec["exp_id"]
    label_id = spec["label_id"]

    print(f"\n{'='*60}")
    print(f"Training {tag} ({exp_id})")
    print(f"  Label: {label_id}, Horizon: {horizon}d")

    # Training end: must respect label maturity (trade_date - horizon trading days)
    train_end_idx = td_idx - horizon
    if train_end_idx < 0:
        print(f"  ❌ Not enough calendar before {TRADE_DATE} for {horizon}d horizon")
        continue
    train_end = cal[train_end_idx]
    train_start_idx = max(0, train_end_idx - TRAIN_WINDOW_DAYS)
    train_start = cal[train_start_idx]
    print(f"  Train window: [{train_start}, {train_end}] ({train_end_idx - train_start_idx + 1} days)")

    # Load features
    raw = QlibAdapter().get_features(UNIVERSE, features + ["$close"],
                                      start_time=train_start, end_time=TRADE_DATE)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["ts_code"] = frame["instrument"]
    frame = frame.sort_values("ts_code").reset_index(drop=True)

    # Load labels
    label_df = LabelStore().load_labels(label_id)
    train_df = frame[frame["trade_date"].between(train_start, train_end)].copy().merge(
        label_df[["trade_date", "instrument", "label_value"]],
        on=["trade_date", "instrument"], how="left",
    )
    y_valid = train_df["label_value"].notna()
    X_tr = train_df[features].fillna(0.0).astype(np.float32)
    y_tr = train_df.loc[y_valid, "label_value"]

    print(f"  Train samples: {len(y_tr)}  (non-null labels)")

    if y_tr.empty:
        print(f"  ❌ No training data")
        continue

    # Train
    model, center, scale = train_model(X_tr.loc[y_tr.index], y_tr, label_id,
                                        n_estimators=spec["n_estimators"], mode="regression")
    print(f"  Model trained, center/scale shape: {len(center)}")

    # Hash = sha256 of model, center, scale
    h = hashlib.sha256()
    h.update(model.model_to_string().encode())
    model_hash = h.hexdigest()[:15]

    # Save model artifact
    out_dir = MODEL_ROOT / exp_id / model_hash
    out_dir.mkdir(parents=True, exist_ok=True)

    model.save_model(str(out_dir / "model.txt"))
    center.to_json(out_dir / "center.json")
    scale.to_json(out_dir / "scale.json")

    meta = {
        "model_hash": model_hash,
        "exp_id": exp_id,
        "tag": tag,
        "label_id": label_id,
        "feature_list_id": FEATURE_LIST,
        "universe": UNIVERSE,
        "n_estimators": spec["n_estimators"],
        "train_window_days": TRAIN_WINDOW_DAYS,
        "train_start": train_start,
        "train_end": train_end,
        "horizon": horizon,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))

    print(f"  ✅ Saved: {out_dir}")
