#!/usr/bin/env python3
"""Diagnose 180d pure baseline — window/decay/market using per-window cache.

Reads cache directly (秒级), trains with train_model() from qsys.
3 experiments: retrain_2y(504d), train_3y(756d), train_5y(1260d).
"""
import sys, time, hashlib, warnings
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
warnings.filterwarnings("ignore")

import numpy as np, pandas as pd
from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.label.store import LabelStore
from qsys.signal.store import SignalStore
from qsys.signal.alpha_v1.training import train_model, predict_model

P = Path(__file__).resolve().parents[2]
OUT = P / "artifacts/diagnostics/v3a_liq_regime"
OUT.mkdir(parents=True, exist_ok=True)
CACHE = P / "data/feature_cache/per_window"

adapter = QlibAdapter(); adapter.init_qlib()
clean = FeatureListRegistry.load("v3a_plus_liquidity_pure")
al = LabelStore(root=str(P/"data/research")).load_labels("fwd_ret_180d_raw")
al["trade_date"] = al["trade_date"].astype(str).str[:10]; al["ts_code"] = al["instrument"]
cal = [str(c)[:10] for c in D.calendar(end_time="2026-06-30", freq="day")]

# Precompute calendar index for fast lookups
cal_idx = {d: i for i, d in enumerate(cal)}

def n_trading_days_before(d: str, n: int) -> str:
    idx = cal_idx.get(d)
    if idx is None or idx < n: return cal[0]
    return cal[idx - n]

def load_cache(start: str, end: str, fallback_orig: str | None = None) -> pd.DataFrame:
    """Load features from cache. If exact (start,end) misses, try fallback_orig then slice."""
    candidates = [f"__window__::{start}::{end}",
                  f"__window__::{start}::{(pd.Timestamp(end)+pd.Timedelta(days=30)).strftime('%Y-%m-%d')}"]
    if fallback_orig:
        candidates += [f"__window__::{fallback_orig}::{end}",
                       f"__window__::{fallback_orig}::{(pd.Timestamp(end)+pd.Timedelta(days=30)).strftime('%Y-%m-%d')}"]
    for raw in candidates:
        k = hashlib.sha256(raw.encode()).hexdigest()[:16]
        cp = CACHE / f"{k}.parquet"
        if cp.exists():
            df = pd.read_parquet(cp)
            df["trade_date"] = df["trade_date"].astype(str).str[:10]
            keep = {"trade_date","instrument"} | set(clean + ["$close"])
            df = df[[c for c in df.columns if c in keep]]
            df["ts_code"] = df["instrument"]
            df = df.drop(columns=["instrument"])
            # Filter to requested range
            df = df[df["trade_date"] >= start]
            return df
    raise FileNotFoundError(f"Cache miss for {start}..{end}")

def evaluate(df, tag):
    df = df.dropna(subset=["label_value"]); n = len(df)
    if n < 100: return {"tag": tag, "error": "no data"}
    daily = df.groupby("trade_date").apply(lambda g: pd.Series({"ic":g["score"].corr(g["label_value"]),"rk":g["score"].rank().corr(g["label_value"].rank())}), include_groups=False).dropna()
    r = {"tag":tag,"ic":daily["ic"].mean(),"icir":daily["ic"].mean()/daily["ic"].std() if daily["ic"].std()>0 else 0,
         "rank_ic":daily["rk"].mean(),"rank_icir":daily["rk"].mean()/daily["rk"].std() if daily["rk"].std()>0 else 0}
    ds = sorted(df["trade_date"].unique())
    for k in [20,50,100]:
        v = pd.concat([df[df["trade_date"]==dt].sort_values("score",ascending=False).head(k)["label_value"] for dt in ds]).dropna()
        if len(v)>0: r[f"top{k}_mean"]=v.mean(); r[f"top{k}_hit"]=(v>0).mean()
    for y in sorted(set(dd[:4] for dd in ds)):
        yr=df[df["trade_date"].str[:4]==y]
        if len(yr)<200: continue
        yd=yr.groupby("trade_date").apply(lambda g: pd.Series({"ic":g["score"].corr(g["label_value"])}), include_groups=False).dropna()
        r[f"ic_{y}"]=yd["ic"].mean()
    return r

# ── Baseline from store ──
RUN = "rolling__180d_v3a_plus_liquidity_pure__v3a_liq_pure_180d__fwd_ret_180d_raw__daily_zscore__2020-01-01_2025-12-31"
sig = SignalStore(root=str(P/"data/research")).load_signal_run("fwd_ret_180d_raw__daily_zscore", RUN)
sig["ts_code"] = sig["instrument"]
base = evaluate(sig[["trade_date","ts_code","score"]].merge(al[["trade_date","ts_code","label_value"]],on=["trade_date","ts_code"],how="left"),"baseline_current")
print(f"Baseline: IC={base['ic']:.4f} ICIR={base['icir']:.3f}")
results = [base]

# ── Windows ──
windows = pd.read_csv(P / "data/research/experiments/180d_v3a_plus_liquidity/rolling_windows.csv")
print(f"Windows: {len(windows)}")

for name, train_days in [("retrain_2y",504)]:
    print(f"\n{name} ({train_days}d) ...", flush=True)
    t0 = time.time()
    all_preds = []
    for i, w in windows.iterrows():
        ps, pe = w["predict_start"], w["predict_end"]
        te = w["train_end"]
        ts_orig = w["train_start"]

        # Load cache — try with ts_new first, fall back to ts_orig
        load_end = (pd.Timestamp(pe) + pd.Timedelta(days=30)).strftime("%Y-%m-%d")
        ts_new = n_trading_days_before(te, train_days)
        frame = load_cache(ts_new, load_end, fallback_orig=ts_orig)

        train = frame[frame["trade_date"].between(ts_new, te)]
        pred = frame[frame["trade_date"].between(ps, pe)]
        if pred.empty: continue

        # Merge labels
        train_m = train.merge(al[["trade_date","ts_code","label_value"]],on=["trade_date","ts_code"],how="left")
        has_y = train_m["label_value"].notna()
        X_tr = train_m.loc[has_y, clean].fillna(0).astype(np.float32)
        y_tr = train_m.loc[has_y, "label_value"].astype(float)
        if len(y_tr) < 50: continue

        model, center, scale = train_model(X_tr, y_tr, "d", n_estimators=300)
        X_pred = pred[clean].fillna(0).astype(np.float32)
        score = predict_model(model, center, scale, X_pred)
        all_preds.append(pd.DataFrame({"trade_date": pred["trade_date"].values, "ts_code": pred["ts_code"].values, "score": score.values}))

        if (i+1)%20==0: print(f"  [{i+1}/{len(windows)}] ({time.time()-t0:.0f}s)", flush=True)

    if all_preds:
        c = pd.concat(all_preds,ignore_index=True).merge(al[["trade_date","ts_code","label_value"]],on=["trade_date","ts_code"],how="left")
        r = evaluate(c, name); results.append(r)
        print(f"  IC={r['ic']:.4f} ICIR={r['icir']:.3f} ({time.time()-t0:.0f}s)", flush=True)
    else:
        print(f"  NO PREDICTIONS", flush=True)

pd.DataFrame(results).to_csv(OUT/"window_decay_summary.csv",index=False)
print(f"\n{'─'*60}")
for r in results:
    print(f"{r['tag']:<20s} IC={r.get('ic',0):.4f} ICIR={r.get('icir',0):.3f}")
print(f"\nDone → {OUT/'window_decay_summary.csv'}")
