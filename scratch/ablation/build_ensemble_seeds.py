#!/usr/bin/env python3
"""Same-window multi-seed S180 ensembles (multi-seed realization lottery study).

Builds, for each of the 4 phases (P0/P5/P10/P15), a bank of per-seed raw
predictions for seeds {42, 7, 77, 123, 456}, then composes three signal runs:

    single = zscore_no_clip(raw_s42)                      (must == stored rawrank)
    ens3   = zscore_no_clip(mean(raw_s42, raw_s7, raw_s77))
    ens5   = zscore_no_clip(mean(raw_5seeds))

Correctness fix over build_phase_signals.py (db539d6a): train with
`params = dict(_DEFAULT_LGB_PARAMS); params["seed"] = sd` — NOT
`dict(gen.lgb_params or {}) + seed` which silently drops every tuned
hyperparameter and yields a bare-params LightGBM (Spearman ~0.75 vs the stored
runs, not 1.0).  Only the stochastic seed differs between models; feature /
bagging fractions and every other hyperparameter are identical.

Pipeline (reuses the stored rawrank machinery):
  window schedule -> cache-frame slice per phase shift -> per-seed train/predict
  -> per-seed raw parquet (trade_date, data_date, instrument, raw)
  -> compose score = zscore_no_clip(mean raw) ; score_raw = clip(score, ±3)
  -> SignalStore.save_signal_run with seeds/provenance manifest.

Validation gate: composed seed-42 single must reproduce the stored
rr_{phase}__rawrank run (per-day Spearman == 1.0 and identical top5 on every
day); if it fails the build stops and reports instead of continuing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))

from qsys.data.calendar import get_trading_calendar  # noqa: E402
from qsys.label.store import LabelStore  # noqa: E402
from qsys.research.generators.lightgbm_single_label import (  # noqa: E402
    LightGBMSingleLabelGenerator,
)
from qsys.research.generators.utils import (  # noqa: E402
    build_next_trading_date_lookup,
    build_prev_trading_date_lookup,
    check_training_label_maturity,
    horizon_from_label_id,
)
from qsys.signal.alpha_v1.training import (  # noqa: E402
    _DEFAULT_LGB_PARAMS,
    train_model,
    predict_model,
)
from qsys.signal.store import SignalStore  # noqa: E402

from build_phase_signals import (  # noqa: E402
    EXPERIMENT,
    GEN_KWARGS,
    PHASES,
    SHIFT_CAL_START,
    SHIFT_CAL_END,
    SIG_ID,
    extended_end,
    load_windows,
    zscore_no_clip,
)

LABEL_ID = "fwd_ret_180d_raw"
HORIZON = horizon_from_label_id(LABEL_ID)
WINDOWS_CSV = ROOT / "data/research/experiments" / EXPERIMENT / "rolling_windows.csv"

# ── seed sets (no grid search; fixed by directive) ──
SEEDS = [42, 7, 77, 123, 456]
ENSEMBLES = {
    "single": [SEEDS[0]],
    "ens3": SEEDS[:3],
    "ens5": SEEDS[:5],
}

TMP = ROOT / "scratch" / "ablation" / "ens_tmp"
SEED_RAW_DIR = TMP / "seed_raw"          # {phase}/seed{sd}/w{i:04d}.parquet
RUN_OUT = TMP / "signal_runs"


def stored_run_id(phase: str) -> str:
    return f"{SIG_ID}__rr_{phase}__rawrank__{EXPERIMENT}"


def ensemble_run_id(phase: str, tag: str) -> str:
    return f"{SIG_ID}__rr_{phase}__{tag}__{EXPERIMENT}"


def build_shift_lookup(k: int) -> dict[str, str]:
    cal = get_trading_calendar(SHIFT_CAL_START, SHIFT_CAL_END)
    pos = {d: i for i, d in enumerate(cal)}
    out = {}
    for d, i in pos.items():
        j = i + k
        out[d] = cal[j] if 0 <= j < len(cal) else d
    return out


def shift_date(d: str, lookup: dict[str, str]) -> str:
    if d in lookup:
        return lookup[d]
    cal = get_trading_calendar(d, SHIFT_CAL_END)
    nxt = next((x for x in cal if x >= d), d)
    return lookup.get(nxt, nxt)


def train_predict_seed(
    win: dict,
    shift_lookup: dict[str, str],
    gen: LightGBMSingleLabelGenerator,
    label_df: pd.DataFrame,
    sd: int,
) -> tuple[pd.DataFrame | None, dict]:
    """Train seed `sd` on the phase-shifted window and return raw predictions
    keyed by (trade_date, data_date, instrument) with raw column."""
    shifted = {
        key: shift_date(win[key], shift_lookup)
        for key in ("train_start", "train_end", "predict_start", "predict_end")
    }
    frame, clean = gen._load_data(win["train_start"], extended_end(win["predict_end"]))

    next_td = build_next_trading_date_lookup(shifted["train_start"], shifted["train_end"])
    check_training_label_maturity(shifted["train_end"], shifted["predict_start"], HORIZON)
    train = frame[
        (frame["trade_date"] >= shifted["train_start"])
        & (frame["trade_date"] <= shifted["train_end"])
    ].copy()
    train["label_date"] = train["trade_date"].map(next_td)
    train = train.merge(
        label_df[["trade_date", "instrument", "label_value"]].rename(
            columns={"trade_date": "label_date"}),
        on=["label_date", "instrument"], how="left",
    )
    y_valid = train["label_value"].notna()
    X_tr = train[clean].fillna(0.0).astype(np.float32)
    y_tr = train.loc[y_valid, "label_value"].astype(float)
    if y_tr.empty:
        return None, {"error": "no training labels", "shifted": shifted}

    # FIXED params path: full tuned defaults + per-seed seed only
    params = dict(_DEFAULT_LGB_PARAMS)
    params["seed"] = sd
    tag = f"{win['window_id']}_s{sd}"
    model, center, scale = train_model(
        X_tr.loc[y_tr.index], y_tr, tag,
        n_estimators=gen.n_estimators, lgb_params=params,
    )

    # inference over the shifted predict span (feature date -> execution day)
    window_cal = get_trading_calendar(shifted["predict_start"], shifted["predict_end"])
    if not window_cal:
        return None, {"error": "empty shifted predict calendar", "shifted": shifted}
    prev_td = build_prev_trading_date_lookup(shifted["predict_start"], shifted["predict_end"])
    feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
    pred = frame[frame["trade_date"].isin(feature_dates)].copy()
    if pred.empty:
        return None, {"error": "no feature rows in shifted span", "shifted": shifted}
    X = pred[clean].fillna(0.0).astype(np.float32)
    raw = predict_model(model, center, scale, X).values

    pred = pred.copy()
    pred["raw"] = raw
    # F01 mapping: data_date = feature date, trade_date = next execution day
    f_to_d = {prev_td.get(d, d): d for d in window_cal}
    pred["data_date"] = pred["trade_date"]
    pred["trade_date"] = pred["trade_date"].map(f_to_d)
    out = pred[["trade_date", "data_date", "instrument", "raw"]]
    assert (out["data_date"].astype(str) < out["trade_date"].astype(str)).all()
    return out.reset_index(drop=True), {"shifted": shifted, "n_train": int(y_tr.sum())}


def build_seed_raw(phase: str, win_idx: int, sd: int) -> dict:
    """Worker: build + persist one (phase, window, seed) raw frame."""
    ckpt = SEED_RAW_DIR / phase / f"seed{sd}" / f"w{win_idx:04d}.parquet"
    if ckpt.exists():
        return {"phase": phase, "win": win_idx, "seed": sd, "status": "cached"}
    windows = load_windows()
    win = windows[win_idx]
    gen = LightGBMSingleLabelGenerator(**GEN_KWARGS)
    label_df = LabelStore().load_labels(LABEL_ID)
    lookup = build_shift_lookup(PHASES[phase])
    t0 = time.time()
    out, meta = train_predict_seed(win, lookup, gen, label_df, sd)
    if out is None:
        return {"phase": phase, "win": win_idx, "seed": sd, "status": "skipped",
                "error": meta.get("error")}
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(ckpt, index=False)
    meta.update({"status": "built", "rows": len(out), "secs": round(time.time() - t0, 1)})
    meta["phase"], meta["win"], meta["seed"] = phase, win_idx, sd
    return meta


def build_window_all_seeds(phase: str, win_idx: int, seeds: list[int]) -> list[dict]:
    """Worker: build every missing (phase, window, seed) in one frame load."""
    missing = []
    for sd in seeds:
        ckpt = SEED_RAW_DIR / phase / f"seed{sd}" / f"w{win_idx:04d}.parquet"
        if not ckpt.exists():
            missing.append(sd)
    if not missing:
        return [{"phase": phase, "win": win_idx, "seed": sd, "status": "cached"}
                for sd in seeds]
    windows = load_windows()
    win = windows[win_idx]
    gen = LightGBMSingleLabelGenerator(**GEN_KWARGS)
    label_df = LabelStore().load_labels(LABEL_ID)
    lookup = build_shift_lookup(PHASES[phase])
    results = []
    for sd in seeds:
        ckpt = SEED_RAW_DIR / phase / f"seed{sd}" / f"w{win_idx:04d}.parquet"
        if ckpt.exists():
            results.append({"phase": phase, "win": win_idx, "seed": sd, "status": "cached"})
            continue
        t0 = time.time()
        out, meta = train_predict_seed(win, lookup, gen, label_df, sd)
        if out is None:
            results.append({"phase": phase, "win": win_idx, "seed": sd,
                            "status": "skipped", "error": meta.get("error")})
            continue
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(ckpt, index=False)
        meta.update({"status": "built", "rows": len(out), "secs": round(time.time() - t0, 1)})
        meta["phase"], meta["win"], meta["seed"] = phase, win_idx, sd
        results.append(meta)
    return results


def load_seed_raw(phase: str, win_idx: int, sd: int) -> pd.DataFrame:
    return pd.read_parquet(SEED_RAW_DIR / phase / f"seed{sd}" / f"w{win_idx:04d}.parquet")


def compose_phase(phase: str, seeds: list[int], tag: str) -> pd.DataFrame:
    """Mean raw across seeds per (trade_date, instrument) -> score/score_raw."""
    windows = load_windows()
    frames = []
    for i, win in enumerate(windows):
        per_seed = []
        for sd in seeds:
            ckpt = SEED_RAW_DIR / phase / f"seed{sd}" / f"w{i:04d}.parquet"
            if not ckpt.exists():
                continue
            per_seed.append(load_seed_raw(phase, i, sd))
        if not per_seed:
            continue
        one = pd.concat(per_seed, ignore_index=True)
        g = one.groupby(["trade_date", "instrument"])["raw"].mean().reset_index()
        g["n_seed"] = len(per_seed)
        frames.append(g)
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["trade_date", "instrument"]).reset_index(drop=True)
    df["score"] = df.groupby("trade_date")["raw"].transform(zscore_no_clip)
    df["score_raw"] = df["score"].clip(-3.0, 3.0)
    # merge back data_date from any one seed's frame
    dd = pd.concat(
        [load_seed_raw(phase, i, seeds[0])[["trade_date", "data_date", "instrument"]]
         for i in range(len(windows))
         if (SEED_RAW_DIR / phase / f"seed{seeds[0]}" / f"w{i:04d}.parquet").exists()],
        ignore_index=True)
    dd = dd.drop_duplicates(["trade_date", "instrument"])
    df = df.merge(dd, on=["trade_date", "instrument"], how="left")
    df["signal_id"] = SIG_ID
    df["signal_run_id"] = ensemble_run_id(phase, tag)
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    df["data_date"] = df["data_date"].astype(str).str[:10]
    return df[["trade_date", "data_date", "instrument", "signal_id",
               "signal_run_id", "score", "score_raw"]]


def validate_single(phase: str, composed: pd.DataFrame) -> dict:
    """seed-42 single must equal the stored rawrank run (rank + top5)."""
    stored = pd.read_parquet(ROOT / "data/research/signals" / SIG_ID / stored_run_id(phase)
                             / "predictions.parquet")
    day_stats = []
    for day, m in composed.groupby("trade_date"):
        s = stored[stored["trade_date"] == day][["instrument", "score"]]
        m = m[["instrument", "score"]]
        common = set(m["instrument"]) & set(s["instrument"])
        if len(common) < 30:
            continue
        m = m[m["instrument"].isin(common)].set_index("instrument").loc[sorted(common)]
        s = s[s["instrument"].isin(common)].set_index("instrument").loc[sorted(common)]
        rho = m["score"].corr(s["score"], method="spearman")
        if not np.isfinite(rho):
            continue
        m5 = set(m.sort_values("score", ascending=False).head(5).index)
        s5 = set(s.sort_values("score", ascending=False).head(5).index)
        day_stats.append({"day": day, "rho": float(rho), "top5_ok": m5 == s5})
    ds = pd.DataFrame(day_stats)
    return {
        "phase": phase,
        "days": len(ds),
        "median_rho": float(ds["rho"].median()),
        "min_rho": float(ds["rho"].min()),
        "top5_identical_days": int(ds["top5_ok"].sum()),
        "top5_mismatch_days": int((~ds["top5_ok"]).sum()),
        "fail": bool(ds["top5_ok"].sum() != len(ds) or ds["rho"].min() < 0.999999),
    }


def save_run(phase: str, df: pd.DataFrame, seeds: list[int], tag: str) -> Path:
    rid = ensemble_run_id(phase, tag)
    store = SignalStore(str(ROOT / "data/research"))
    p = store.save_signal_run(
        SIG_ID, rid, df,
        manifest={
            "artifact_type": "multi_seed_ensemble_signal_run",
            "rawrank_of": stored_run_id(phase),
            "shift_trading_days": PHASES[phase],
            "ranking_score": "daily_zscore(mean_raw_prediction)  # no cap, order-preserving",
            "display_score": "clip(ranking_score, +/-3)",
            "experiment": EXPERIMENT,
            "train_window_trading_days": 504,
            "label_id": LABEL_ID,
            "seeds": seeds,
            "ensemble": "mean_of_seed_raw_predictions" if len(seeds) > 1 else "single_model",
            "lgb_params": "dict(_DEFAULT_LGB_PARAMS); seed={seeds}" .format(seeds=seeds),
            "description": "same-window {}-seed ensemble over raw predictions "
                           "(model realization lottery study)".format(len(seeds)),
        },
        overwrite=True,
    )
    print(f"  wrote {p}  ({len(df)} rows)", flush=True)
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phases", default="p0,p5,p10,p15")
    ap.add_argument("--seeds", default=",".join(map(str, SEEDS)),
                    help="comma-separated seeds (subset of SEEDS)")
    ap.add_argument("--windows", default=None, help="comma-separated window indices (smoke)")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--compose", action="store_true",
                    help="only compose signal runs from existing seed raw bank")
    ap.add_argument("--validate-only", action="store_true",
                    help="only validate stored rawrank vs recomposed single")
    args = ap.parse_args()

    phases = [s.strip() for s in args.phases.split(",") if s.strip()]
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    idxs = [int(s.strip()) for s in args.windows.split(",")] if args.windows else None

    if args.validate_only:
        # recompose single (seed 42) from raw bank and validate
        for phase in phases:
            df = compose_phase(phase, [42], "single")
            v = validate_single(phase, df)
            print(f"[{phase}] {v}", flush=True)
        return 0

    # Phase 1: build the per-seed raw bank (parallel across windows)
    if not args.compose:
        import multiprocessing as mp
        wrange = idxs if idxs is not None else range(len(load_windows()))
        tasks = [(phase, i) for phase in phases for i in wrange]
        t0 = time.time()
        results = []
        with mp.Pool(args.workers) as pool:
            for res in pool.imap_unordered(_build_star, tasks, chunksize=1):
                for r in res:
                    results.append(r)
                    print(f"  [{r.get('phase')} w{r.get('win'):04d} s{r.get('seed')}] "
                          f"{r.get('status')} {r.get('rows', '')} {r.get('error', '')}",
                          flush=True)
        n_built = sum(1 for r in results if r.get("status") == "built")
        n_skip = sum(1 for r in results if r.get("status") == "skipped")
        print(f"seed-raw bank: {n_built} built, {n_skip} skipped in "
              f"{time.time()-t0:.0f}s", flush=True)

    # Phase 2: compose + validate + save signal runs
    manifest = {}
    for phase in phases:
        for tag, sset in ENSEMBLES.items():
            if not set(sset).issubset(set(seeds)) and tag != "single":
                continue
            df = compose_phase(phase, sset, tag)
            print(f"[{phase}/{tag}] composed {len(df)} rows / "
                  f"{df['trade_date'].nunique()} days", flush=True)
            if tag == "single":
                v = validate_single(phase, df)
                print(f"[{phase}/single] validation: median_rho={v['median_rho']:.8f} "
                      f"top5_identical={v['top5_identical_days']}/{v['days']} "
                      f"fail={v['fail']}", flush=True)
                manifest[phase] = v
                if v["fail"]:
                    print(f"[{phase}] SINGLE-SEED REPRODUCTION FAILED — aborting "
                          f"phase {phase} (do not continue to ensemble)", flush=True)
                    break  # hard-stop the phase: never save ens3/ens5 off a bad baseline
                continue  # single == stored rawrank baseline; do not save a duplicate
            path = save_run(phase, df, sset, tag)
            manifest.setdefault(phase, {})[tag] = str(path)

    (TMP / "ensemble_manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False, default=str))
    print(f"manifest -> {TMP / 'ensemble_manifest.json'}", flush=True)
    return 0


def _build_star(task):
    phase, win_idx = task
    return build_window_all_seeds(phase, win_idx, SEEDS)


if __name__ == "__main__":
    sys.exit(main())
