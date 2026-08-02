#!/usr/bin/env python3
"""Generate candidate list for a trade date: 50/50 blend 60d + 180d model predictions + 5d maxdd prob.

Usage:
    python scripts/dev/gen_candidate_top200.py                           # top 200 (minimal, no SHAP)
    python scripts/dev/gen_candidate_top200.py --top-n 5                 # top 5 with SHAP contributions

Output format:
  - source.models: model provenance (hash, train range, label, etc.) — not repeated per candidate
  - candidates[].models: only {tag, weight, score} for top-N > 10
  - candidates[].models: includes feature_contrib (SHAP) for top-N <= 10
"""
import json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import numpy as np
import pandas as pd
import lightgbm as lgb

from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.signal.alpha_v1.labels import robust_zscore_transform

parser = argparse.ArgumentParser()
parser.add_argument("--trade-date", default="2026-07-03")
parser.add_argument("--top-n", type=int, default=200)
parser.add_argument("--w60", type=float, default=0.5)
parser.add_argument("--w180", type=float, default=0.5)
args = parser.parse_args()

TRADE_DATE = args.trade_date
TOP_K = args.top_n
W60 = args.w60
W180 = args.w180

FEATURE_LIST = "v3a_plus_liquidity_financial_rc"
MODEL_ROOT = Path("data/research/models")

# Pin specific model hashes for reproducibility
MODELS = [
    {"tag": "60d",  "exp_id": "60d_v3a_growth_financial",  "hash": "4da8cf460c855f4", "weight": W60},
    {"tag": "180d", "exp_id": "180d_v3a_growth_financial", "hash": "27cd0cbb36688ee", "weight": W180},
]

QlibAdapter().init_qlib()
features = FeatureListRegistry.load(FEATURE_LIST)
needs_shap = TOP_K <= 10

# ── Load features ──
raw = QlibAdapter().get_features("csi800", features + ["$close"],
                                 start_time=TRADE_DATE, end_time=TRADE_DATE)
frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
frame = frame.loc[:, ~frame.columns.duplicated()]
frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
frame["ts_code"] = frame["instrument"]
frame = frame.sort_values("ts_code").reset_index(drop=True)
print(f"Stocks: {len(frame)}  Features: {len(features)}  Top: {TOP_K}  SHAP: {needs_shap}")

# ── Load models, predict, collect provenance ──
scores = {}
model_prov = {}
shap_data = {} if needs_shap else None

for m in MODELS:
    tag = m["tag"]
    model_dir = MODEL_ROOT / m["exp_id"] / m["hash"]
    meta = json.loads((model_dir / "meta.json").read_text()) if (model_dir / "meta.json").exists() else {}

    model = lgb.Booster(model_file=str(model_dir / "model.txt"))
    center = pd.read_json(model_dir / "center.json", typ="series")
    scale = pd.read_json(model_dir / "scale.json", typ="series")
    Xp = frame[features].fillna(0).astype(np.float32)
    Xp_z = robust_zscore_transform(Xp, center, scale)
    pred = model.predict(Xp_z.values)
    pred_z = (pred - pred.mean()) / max(pred.std(), 1e-8)
    scores[tag] = pd.Series(pred_z, index=frame["ts_code"])

    # SHAP only for top N <= 10
    if needs_shap:
        import shap
        explainer = shap.TreeExplainer(model, data=Xp_z[:100])
        sv = explainer.shap_values(Xp_z)
        sd = {}
        for i, code in enumerate(frame["ts_code"]):
            vals = {}
            idxs = np.argsort(np.abs(sv[i]))[::-1][:15]
            for j in idxs:
                vals[features[j]] = round(float(sv[i][j]), 4)
            sd[code] = vals
        stats = {}
        for feat in features:
            arr = sv[:, features.index(feat)]
            stats[feat] = {
                "min": round(float(arr.min()), 4), "max": round(float(arr.max()), 4),
                "mean": round(float(arr.mean()), 4), "p50": round(float(np.median(arr)), 4),
                "p75": round(float(np.percentile(arr, 75)), 4),
                "p90": round(float(np.percentile(arr, 90)), 4),
            }
        shap_data[tag] = (sd, stats)

    model_prov[tag] = {
        "tag": tag,
        "weight": m["weight"],
        "model_hash": m["hash"],
        "model_dir": "data/research/models/" + m["exp_id"] + "/" + m["hash"],
        "label_id": meta.get("label_id", ""),
        "feature_list_id": meta.get("feature_list_id", ""),
        "train_start": meta.get("train_start", ""),
        "train_end": meta.get("train_end", ""),
    }

# ── Blend ──
ranking = W60 * scores.get("60d", pd.Series(0)) + W180 * scores.get("180d", pd.Series(0))
ranking = ranking.sort_values(ascending=False).reset_index()
ranking.columns = ["ts_code", "ranking_score"]
ranking["rank"] = ranking.index + 1
top = ranking.head(TOP_K)

# ── Load maxdd prob ──
maxdd_probs = {}
raw_path = Path("outputs") / TRADE_DATE / "stop_loss_prob.json"
if raw_path.exists():
    rd = json.loads(raw_path.read_text())
    for p in rd.get("predictions", []):
        maxdd_probs[p["ts_code"]] = round(p.get("downside_prob", 0), 6)

# ── Stock names ──
try:
    tb = pd.read_parquet("data/tushare/stock_basic.parquet")
    tb["ck"] = tb["ts_code"].str.replace(".", "", regex=False)
    nm = dict(zip(tb["ck"], tb["name"]))
except Exception:
    nm = {}

# ── Build candidates ──
candidates = []
for _, r in top.iterrows():
    ck = r["ts_code"].replace(".", "")
    cand_models = []

    for tag in ["60d", "180d"]:
        sc = scores.get(tag, pd.Series())
        entry = {
            "tag": tag,
            "weight": [m["weight"] for m in MODELS if m["tag"] == tag][0],
            "score": round(float(sc.get(r["ts_code"], 0)), 4) if r["ts_code"] in sc.index else None,
        }
        if needs_shap and shap_data:
            sd, stats = shap_data.get(tag, ({}, {}))
            sh60v = sd.get(r["ts_code"], {})
            us60 = {f: stats[f] for f in sh60v if f in stats}
            if sh60v:
                entry["feature_contrib"] = {"method": "shap", "values": sh60v, "universe_stats": us60}
        cand_models.append(entry)

    c = {
        "ts_code": r["ts_code"],
        "name": nm.get(ck, ""),
        "rank": int(r["rank"]),
        "ranking_score": round(float(r["ranking_score"]), 4),
        "models": cand_models,
    }
    mp = maxdd_probs.get(r["ts_code"])
    if mp is not None:
        c["maxdd_5d_prob"] = mp
    candidates.append(c)

# ── Stats ──
score_vals = [c["ranking_score"] for c in candidates]
rss = {"min": round(float(np.min(score_vals)), 4), "max": round(float(np.max(score_vals)), 4),
       "mean": round(float(np.mean(score_vals)), 4),
       "p10": round(float(np.percentile(score_vals, 10)), 4),
       "p50": round(float(np.percentile(score_vals, 50)), 4),
       "p90": round(float(np.percentile(score_vals, 90)), 4)}

# ── Top-level provenance ──
now_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
run_id = f"infer_{TRADE_DATE}_{now_ts[:10]}_{TOP_K}"

payload = {
    "run_id": run_id,
    "trade_date": TRADE_DATE,
    "signal_date": TRADE_DATE,
    "execution_date": TRADE_DATE,
    "strategy_id": "financial_rc",
    "created_at": now_ts,
    "universe": "csi800",
    "top_k": TOP_K,
    "feature_list_id": FEATURE_LIST,
    "source": {
        "feature_list_id": FEATURE_LIST,
        "models": [model_prov[t] for t in ["60d", "180d"]],
    },
    "blend": {
        "weights": {"60d": W60, "180d": W180},
        "note": f"{W60}*60d+{W180}*180d",
        "ranking_score_stats": rss,
    },
    "candidates": candidates,
}

out_dir = Path("outputs") / TRADE_DATE
out_dir.mkdir(parents=True, exist_ok=True)
suffix = f"_top{TOP_K}"
out_path = out_dir / f"candidates{suffix}.json"
out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print(f"Done: {out_path}")
print(f"  Candidates: {len(candidates)}")
print(f"  Score range: {rss['min']:.2f} ~ {rss['max']:.2f} (mean={rss['mean']:.2f})")

# Quick check: top 5 provenance not bleeding into candidates
if not needs_shap:
    for c in candidates[:3]:
        for m in c["models"]:
            assert "feature_contrib" not in m, f"SHAP leaked into rank {c['rank']}"
    print("  ✓ SHAP not in top 200 (as designed)")
