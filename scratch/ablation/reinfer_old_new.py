#!/usr/bin/env python3
"""Phase 1 — old-vs-new model comparison at each retrain boundary.

For every retrain day t (= window k's predict_start), the just-activated new
model M_k produced a stored score on the strictly-PIT day-t feature snapshot
(data_date == prev_td(t)) — that is the *only* model-version variable in play
because the same snapshot is fed to the previous model M_{k-1} here.

  score_new / rank_new  <- stored predictions.parquet (score_raw) at t
  score_old / rank_old  <- re-trained M_{k-1}, re-inferenced on the SAME
                           day-t features (data_date == prev_td(t))

Because the S180 windows are contiguous 20-trading-day chunks, prev_td(t) is
the last feature date of window k-1's own predict span, so the day-t rows live
inside window k-1's cached frame — the frame already on disk is used for both
training M_{k-1} and inferencing X_t.

Usage (run from the MAIN SysQ cwd):
  python /path/to/reinfer_old_new.py --validate   # reproducibility check on 1 window
  python /path/to/reinfer_old_new.py --windows 0  # one boundary (debug)
  python /path/to/reinfer_old_new.py              # all 67 boundaries
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
sys.path.insert(0, str(ROOT))

from qsys.research.generators.lightgbm_single_label import (  # noqa: E402
    LightGBMSingleLabelGenerator,
)
from qsys.research.generators.utils import (  # noqa: E402
    build_next_trading_date_lookup,
    build_prev_trading_date_lookup,
    check_training_label_maturity,
    horizon_from_label_id,
)
from qsys.data.calendar import get_trading_calendar  # noqa: E402
from qsys.label.store import LabelStore  # noqa: E402
from qsys.signal.alpha_v1.training import train_model, predict_model  # noqa: E402

EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3"
WINDOWS_CSV = ROOT / "data/research/experiments" / EXPERIMENT / "rolling_windows.csv"
SRID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3__"
    "v3a_growth_financial_180d__fwd_ret_180d_raw__daily_zscore__"
    "2021-01-01_2026-07-31"
)
PREDS_PARQUET = ROOT / "data/research/signals/fwd_ret_180d_raw__daily_zscore" / SRID / "predictions.parquet"
LABEL_ID = "fwd_ret_180d_raw"
HORIZON = horizon_from_label_id(LABEL_ID)

GEN_KWARGS = dict(
    universe="csi800",
    n_estimators=300,
    feature_list_id="v3a_plus_liquidity_financial_rc",
    use_feature_cache=True,
    feature_cache_root=str(ROOT / "data/feature_cache"),
    source_manifest_hash="9e6148becd79057da9199079218fdcae7351361ad28126b349d6ddd5323a909b",
)


def load_windows() -> list[dict]:
    df = pd.read_csv(WINDOWS_CSV)
    return df.to_dict("records")


def load_stored_preds() -> pd.DataFrame:
    df = pd.read_parquet(PREDS_PARQUET)
    return df[["trade_date", "data_date", "instrument", "score_raw", "score"]].copy()


def extended_end(predict_end: str) -> str:
    return (datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")


def train_old_model(win: dict, gen: LightGBMSingleLabelGenerator):
    """Re-train window win's model exactly as the rolling pipeline did."""
    frame, clean = gen._load_data(win["train_start"], extended_end(win["predict_end"]))
    label_df = LabelStore().load_labels(LABEL_ID)
    next_td = build_next_trading_date_lookup(win["train_start"], win["train_end"])
    check_training_label_maturity(win["train_end"], win["predict_start"], HORIZON)
    train = frame[
        (frame["trade_date"] >= win["train_start"]) & (frame["trade_date"] <= win["train_end"])
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
        raise ValueError(f"No valid training samples for {win['window_id']}")
    model, center, scale = train_model(
        X_tr.loc[y_tr.index], y_tr, win["window_id"],
        n_estimators=gen.n_estimators, lgb_params=gen.lgb_params,
    )
    return frame, clean, model, center, scale, X_tr.index


def validate_one(win: dict, gen: LightGBMSingleLabelGenerator,
                 stored: pd.DataFrame) -> dict:
    """Re-train a window and compare its full predict-span output to stored."""
    frame, clean, model, center, scale, _ = train_old_model(win, gen)
    window_cal = get_trading_calendar(win["predict_start"], win["predict_end"])
    prev_td = build_prev_trading_date_lookup(win["predict_start"], win["predict_end"])
    feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
    pred = frame[frame["trade_date"].isin(feature_dates)].copy()
    raw = predict_model(
        model, center, scale, pred[clean].fillna(0.0).astype(np.float32)
    )
    pred["score_old"] = raw.values
    pred = pred[["trade_date", "instrument", "score_old"]].copy()
    f_to_d = {prev_td.get(d, d): d for d in window_cal}
    pred["trade_date"] = pred["trade_date"].map(f_to_d)

    s = stored[["trade_date", "instrument", "score_raw"]].rename(
        columns={"score_raw": "score_new"})
    m = pred.merge(s, on=["trade_date", "instrument"], how="inner")
    if m.empty:
        return {"window": win["window_id"], "n": 0, "error": "no overlap"}
    # per-day spearman of rank order (raw preds vs stored raw)
    rhos = []
    mae = []
    for td, g in m.groupby("trade_date"):
        if len(g) >= 30:
            rhos.append(g["score_old"].corr(g["score_new"], method="spearman"))
            mae.append((g["score_old"] - g["score_new"]).abs().mean())
    return {
        "window": win["window_id"],
        "train_end": win["train_end"],
        "predict_start": win["predict_start"],
        "n": len(m),
        "n_days": len(rhos),
        "spearman_median": float(np.median(rhos)) if rhos else np.nan,
        "spearman_min": float(np.min(rhos)) if rhos else np.nan,
        "mae_mean": float(np.mean(mae)) if mae else np.nan,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", action="store_true",
                    help="reproducibility check: re-train w0000, compare to stored")
    ap.add_argument("--windows", default=None,
                    help="comma-separated old-window indices to re-train (e.g. 0,1); "
                         "default all 0..66 (each boundary needs window k-1 as old model)")
    ap.add_argument("--out", default="scratch/ablation/reinfer_retrain_days.parquet")
    ap.add_argument("--tmp", default="scratch/ablation/reinfer_tmp",
                    help="dir for per-window checkpoint parquets (resume-safe)")
    args = ap.parse_args()

    windows = load_windows()
    stored = load_stored_preds()
    gen = LightGBMSingleLabelGenerator(**GEN_KWARGS)

    if args.validate:
        res = validate_one(windows[0], gen, stored)
        print(json.dumps(res, indent=2))
        # also validate a middle window (w0660) and the last-but-one
        for idx in (33, 66):
            res = validate_one(windows[idx], gen, stored)
            print(json.dumps(res, indent=2))
        return 0

    if args.windows:
        idxs = [int(s.strip()) for s in args.windows.split(",")]
    else:
        idxs = list(range(0, len(windows) - 1))  # old models w0000..w1320

    t0 = time.time()
    tmp_dir = Path(args.tmp)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    records: list[pd.DataFrame] = []
    for i in idxs:
        ckpt = tmp_dir / f"{i:03d}.parquet"
        if ckpt.exists():
            print(f"[{i:2d}] {windows[i]['window_id']}: checkpoint exists, skip", flush=True)
            records.append(pd.read_parquet(ckpt))
            continue
        win_old = windows[i]
        win_new = windows[i + 1]
        t = win_new["predict_start"]
        w0 = time.time()
        frame, clean, model, center, scale, _ = train_old_model(win_old, gen)

        # day-t feature snapshot == last feature date of the old window's span
        prev_td = build_prev_trading_date_lookup(win_new["predict_start"], win_new["predict_end"])
        f = prev_td.get(t, t)
        day_rows = frame[frame["trade_date"] == f]
        if day_rows.empty:
            print(f"[skip] {win_old['window_id']}: no feature rows at {f}", flush=True)
            continue
        raw_old = predict_model(
            model, center, scale, day_rows[clean].fillna(0.0).astype(np.float32)
        )
        old = pd.DataFrame({
            "trade_date": t,
            "data_date": f,
            "instrument": day_rows["instrument"].values,
            "score_old": raw_old.values,
        })
        new = stored[stored["trade_date"] == t][
            ["trade_date", "data_date", "instrument", "score_raw"]
        ].rename(columns={"score_raw": "score_new"})
        merged = old.merge(new, on=["trade_date", "instrument"], how="inner",
                           suffixes=("_oldrow", ""))
        if merged.empty:
            print(f"[skip] {win_old['window_id']}: no overlap with stored at {t}", flush=True)
            continue
        merged["window_old"] = win_old["window_id"]
        merged["window_new"] = win_new["window_id"]
        merged["train_end_old"] = win_old["train_end"]
        merged["train_end_new"] = win_new["train_end"]
        merged["model_version_old"] = win_old["window_id"]
        merged["model_version_new"] = win_new["window_id"]
        merged.to_parquet(ckpt, index=False)
        records.append(merged)
        print(f"[{i:2d}] {win_old['window_id']}->{win_new['window_id']} t={t} "
              f"rows={len(merged)} ({time.time()-w0:.0f}s)", flush=True)

    if not records:
        print("no records", file=sys.stderr)
        return 1
    out = pd.concat(records, ignore_index=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_path, index=False)
    print(f"\nwrote {len(out)} rows -> {out_path}  ({time.time()-t0:.0f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
