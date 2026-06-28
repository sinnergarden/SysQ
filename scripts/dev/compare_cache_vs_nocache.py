#!/usr/bin/env python3
"""Compare cache vs no-cache: features + signal for one window."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import numpy as np, pandas as pd
from scipy.stats import pearsonr

W = {"train_start": "2020-01-01", "train_end": "2021-12-01",
     "predict_start": "2021-12-02", "predict_end": "2022-01-31"}
UNIVERSE = "csi800"; FEATURE_SET = "value_growth_multibagger_v3a_features"
SOURCE_HASH = "qlib_ede151b9a0f5"

from qsys.data.adapter import QlibAdapter; adapter = QlibAdapter(); adapter.init_qlib()
from qsys.feature.registry import FeatureListRegistry
all_f = FeatureListRegistry.load(FEATURE_SET)
raw_f = list(all_f) + ["$close","$amount","$volume","$open","$high","$low","$high_limit","$low_limit"]
raw = adapter.get_features(UNIVERSE, raw_f, start_time=W["train_start"], end_time=W["predict_end"])

def _to_frame(df):
    f = df.reset_index().rename(columns={"datetime": "trade_date"})
    f = f.loc[:, ~f.columns.duplicated()]; f["trade_date"] = f["trade_date"].astype(str).str[:10]
    if "instrument" in f.columns: f = f.rename(columns={"instrument": "ts_code"})
    return f

frame = _to_frame(raw)
print(f"Raw panel: {len(frame)} rows")

# Path A: no-cache
t0 = time.time()
from qsys.feature.feature_compute_registry import compute_phase1_batch
ra = compute_phase1_batch(frame.copy(), all_f)
ra["trade_date"] = ra["trade_date"].astype(str).str[:10]
print(f"A (no-cache): {len(ra)} rows, {time.time()-t0:.1f}s")

# Path B: cache
t0 = time.time()
from qsys.feature.feature_matrix_builder import build_matrix_from_feature_store
from qsys.feature.resolver_v2 import discover_feature_sets
cf = frame.rename(columns={c: c[1:] for c in frame.columns if c.startswith("$")})
discover_feature_sets()
rb = build_matrix_from_feature_store(cf, feature_set_id=FEATURE_SET,
    universe=UNIVERSE, source_manifest_hash=SOURCE_HASH,
    compute_missing=False, allow_uncacheable=True, anchor_df=cf)
print(f"B (cache): {len(rb)} rows, {time.time()-t0:.1f}s")

# Compare features in predict window
mask = (rb["trade_date"] >= W["predict_start"]) & (rb["trade_date"] <= W["predict_end"])
aligned = ra[mask].merge(rb[mask], on=["trade_date", "ts_code"], suffixes=("_a", "_b"))
print(f"Predict rows: {len(aligned)}")

bad = 0
for feat in all_f:
    ca, cb = f"{feat}_a", f"{feat}_b"
    if ca not in aligned.columns or cb not in aligned.columns: continue
    va = pd.to_numeric(aligned[ca], errors="coerce").fillna(-99999).values.astype(float)
    vb = pd.to_numeric(aligned[cb], errors="coerce").fillna(-99999).values.astype(float)
    both = (va != -99999) & (vb != -99999)
    if both.sum() == 0: continue
    if np.abs(va[both] - vb[both]).max() > 1e-10:
        bad += 1

print(f"Feature diffs >= 1e-10: {bad}/{len(all_f)}")
if bad > 0:
    print("❌ MISMATCH")
    sys.exit(1)

print("✅ Features MATCH — now training models...")

# Train both models
from qsys.signal.alpha_v1.training import train_model, predict_model
from qsys.label.store import LabelStore
label = LabelStore().load_labels("fwd_ret_60d_raw").rename(columns={"instrument": "ts_code"})

results = {}
for name, fx in [("no-cache", ra), ("cache", rb)]:
    # Use only derived features (no $ prefix) for model training
    train_f = [c for c in all_f if c in fx.columns and not c.startswith("$")]
    if len(train_f) < 10:
        print(f"  {name}: too few features ({len(train_f)}), skip"); continue
    train = fx[(fx["trade_date"] >= W["train_start"]) & (fx["trade_date"] <= W["train_end"])].copy()
    train = train.merge(label[["trade_date", "ts_code", "label_value"]], on=["trade_date", "ts_code"], how="left")
    yv = train["label_value"].notna()
    X = train[train_f].fillna(0.0).astype(np.float32)
    y = train.loc[yv, "label_value"].astype(float)
    model, center, scale = train_model(X.loc[yv.index], y, "window", n_estimators=100)
    pred = fx[fx["trade_date"].between(W["predict_start"], W["predict_end"])].copy()
    pred["score"] = predict_model(model, center, scale, pred[train_f].fillna(0.0).astype(np.float32)).values
    ic = pred.merge(label[["trade_date", "ts_code", "label_value"]], on=["trade_date", "ts_code"]).groupby("trade_date").apply(
        lambda g: g["score"].corr(g["label_value"]), include_groups=False)
    results[name] = (pred, ic)
    print(f"  {name}: IC_mean={ic.mean():.4f} IC_std={ic.std():.4f}")

# Correlation between scores
pa, pb = results["no-cache"][0], results["cache"][0]
merged = pa[["trade_date", "ts_code", "score"]].merge(pb[["trade_date", "ts_code", "score"]],
    on=["trade_date", "ts_code"], suffixes=("_nocache", "_cache"))
r, _ = pearsonr(merged["score_nocache"], merged["score_cache"])
print(f"Score Pearson R: {r:.8f}")
if abs(r - 1.0) < 1e-10:
    print("✅ SIGNAL IDENTICAL")
elif r > 0.9999:
    print(f"✅ SIGNAL NEAR-IDENTICAL (R={r:.6f})")
else:
    print(f"❌ SIGNAL DIFFERS (R={r:.6f})")
