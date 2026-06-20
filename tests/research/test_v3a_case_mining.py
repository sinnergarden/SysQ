#!/usr/bin/env python3
"""Test case mining logic with synthetic data."""
import sys, pandas as pd, numpy as np
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

np.random.seed(42)
N = 1000
df = pd.DataFrame({
    "trade_date": pd.date_range("2025-01-01", periods=100, freq="B").repeat(10)[:N],
    "instrument": [f"A{i:04d}" for i in range(100) for _ in range(10)][:N],
    "score": np.random.randn(N),
    "label": np.random.randn(N),
})
df["score_pct"] = df.groupby("trade_date")["score"].rank(pct=True)
df["label_pct"] = df.groupby("trade_date")["label"].rank(pct=True)

tp = df[(df["score_pct"] >= 0.90) & (df["label_pct"] >= 0.80)]
fp = df[(df["score_pct"] >= 0.90) & (df["label_pct"] <= 0.30)]
fn = df[(df["score_pct"] <= 0.50) & (df["label_pct"] >= 0.90)]
tn = df[(df["score_pct"] <= 0.30) & (df["label_pct"] <= 0.30)]

assert len(tp) > 0, "TP not empty"
assert len(fp) > 0, "FP not empty"
assert len(fn) > 0, "FN not empty"
assert len(tn) > 0, "TN not empty"
print(f"Test passed: TP={len(tp)} FP={len(fp)} FN={len(fn)} TN={len(tn)}")
