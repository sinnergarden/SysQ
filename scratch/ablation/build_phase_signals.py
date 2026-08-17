#!/usr/bin/env python3
"""Build 4 INDEPENDENT shifted rolling S180 signal runs (P0/P5/P10/P15) with
the raw-ranking fix.

Production S180 signal chain (the cap-tie bug):
    raw ──clip(zscore(raw), ±3)──> score_raw  ──per-day zscore──> score
so on any day where >=5 names clip at +3 (64/68 retrain days have >1 name
tied at the top score), the engine's top-5 is a tiebreak lottery (instrument
code order, mergesort), not model discrimination.

Ranking fix (Sec 1):
    score = zscore(raw)             (order-preserving, NO clip, no cap ties)
    score_raw = clip(zscore(raw), ±3)  (bounded display column only)
zscore is monotonic in raw, so top-5 by `score` == top-5 by raw prediction —
ordering proven identical in --validate.

Phase robustness (Sec 2): P0 = original 68 windows; P5/P10/P15 = the WHOLE
schedule shifted +5/+10/+15 trading days (train_start/train_end/predict_start/
predict_end all shift together, preserving the 504-trading-day train window
and the ~273-day maturity gap).  Each phase is a FULLY independent retraining
— same model def / features / 180d label / universe / raw-ranking / Top5.

Feature phase-invariance (verified bit-identical on overlap) lets every
shifted window slice its ORIGINAL window's cached frame directly — no qlib
rebuild, so this never touches the research cache key space.

Run from the MAIN SysQ cwd (qsys + data resolve).  Writes one SignalRun per
phase via the canonical SignalStore.save_signal_run path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timedelta
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
from qsys.signal.alpha_v1.training import train_model, predict_model  # noqa: E402
from qsys.signal.store import SignalStore  # noqa: E402

EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3"
WINDOWS_CSV = ROOT / "data/research/experiments" / EXPERIMENT / "rolling_windows.csv"
SRID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3__"
    "v3a_growth_financial_180d__fwd_ret_180d_raw__daily_zscore__"
    "2021-01-01_2026-07-31"
)
SIG_ID = "fwd_ret_180d_raw__daily_zscore"
PREDS_PARQUET = ROOT / "data/research/signals" / SIG_ID / SRID / "predictions.parquet"
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

# phases: {phase_name: shift_k (trading days)}
PHASES = {"p0": 0, "p5": 5, "p10": 10, "p15": 15}
SHIFT_CAL_START, SHIFT_CAL_END = "2017-01-01", "2026-12-31"

RUN_ID = {
    "p0": f"{SIG_ID}__rr_p0__rawrank__{EXPERIMENT}",
    "p5": f"{SIG_ID}__rr_p5__rawrank__{EXPERIMENT}",
    "p10": f"{SIG_ID}__rr_p10__rawrank__{EXPERIMENT}",
    "p15": f"{SIG_ID}__rr_p15__rawrank__{EXPERIMENT}",
}


def zscore_no_clip(s: pd.Series) -> pd.Series:
    """Cross-sectional z-score WITHOUT clipping (ranking score)."""
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def extended_end(predict_end: str) -> str:
    return (datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")


def load_windows() -> list[dict]:
    return pd.read_csv(WINDOWS_CSV).to_dict("records")


def build_shift_lookup(k: int) -> dict[str, str]:
    """{date -> date at +k trading days} over a broad calendar."""
    cal = get_trading_calendar(SHIFT_CAL_START, SHIFT_CAL_END)
    if not cal:
        raise RuntimeError("empty trading calendar")
    pos = {d: i for i, d in enumerate(cal)}
    out = {}
    for d, i in pos.items():
        j = i + k
        out[d] = cal[j] if 0 <= j < len(cal) else d
    return out


def shift_date(d: str, lookup: dict[str, str], win_id: str) -> str:
    if d in lookup:
        return lookup[d]
    # window dates are trading days by construction; guard anyway
    cal = get_trading_calendar(d, SHIFT_CAL_END)
    nxt = next((x for x in cal if x >= d), d)
    return lookup.get(nxt, nxt)


def load_labels_once():
    return LabelStore().load_labels(LABEL_ID)


def train_shifted_model(
    win: dict,
    shift_lookup: dict[str, str],
    gen: LightGBMSingleLabelGenerator,
    label_df: pd.DataFrame,
):
    """Train the SHIFTED window's model by slicing the ORIGINAL cache frame.

    Returns (frame, clean, model, center, scale, shifted_win).
    """
    shifted = {
        key: shift_date(win[key], shift_lookup, win["window_id"])
        for key in ("train_start", "train_end", "predict_start", "predict_end")
    }
    # cache HIT on the ORIGINAL window dates (feature PIT-invariance verified)
    frame, clean = gen._load_data(win["train_start"], extended_end(win["predict_end"]))

    next_td = build_next_trading_date_lookup(
        shifted["train_start"], shifted["train_end"]
    )
    check_training_label_maturity(
        shifted["train_end"], shifted["predict_start"], HORIZON
    )
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
        raise ValueError(
            f"No valid training samples for {win['window_id']} shifted "
            f"({shifted['train_start']}..{shifted['train_end']})"
        )
    tag = f"{win['window_id']}_{shifted['train_start']}_{shifted['train_end']}"
    model, center, scale = train_model(
        X_tr.loc[y_tr.index], y_tr, tag,
        n_estimators=gen.n_estimators, lgb_params=gen.lgb_params,
    )
    return frame, clean, model, center, scale, shifted


def infer_shifted_span(
    frame: pd.DataFrame,
    clean: list[str],
    model, center, scale,
    shifted: dict,
) -> pd.DataFrame:
    """Raw inference over the shifted predict span, cross-sectional raw zscore."""
    window_cal = get_trading_calendar(shifted["predict_start"], shifted["predict_end"])
    if not window_cal:
        return pd.DataFrame()
    prev_td = build_prev_trading_date_lookup(
        shifted["predict_start"], shifted["predict_end"]
    )
    feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
    pred = frame[frame["trade_date"].isin(feature_dates)].copy()
    if pred.empty:
        return pd.DataFrame()
    raw = predict_model(
        model, center, scale, pred[clean].fillna(0.0).astype(np.float32)
    )
    pred["_raw"] = raw.values
    pred["_score"] = pred.groupby("trade_date")["_raw"].transform(zscore_no_clip)
    pred["_score_raw"] = pred["_score"].clip(-3.0, 3.0)
    # F01: data_date = feature date (strictly before trade_date)
    pred["data_date"] = pred["trade_date"]
    f_to_d = {prev_td.get(d, d): d for d in window_cal}
    pred["trade_date"] = pred["trade_date"].map(f_to_d)
    out = pred[
        ["trade_date", "data_date", "instrument", "_score", "_score_raw"]
    ].rename(columns={"_score": "score", "_score_raw": "score_raw"})
    # sanity: F01 hold
    assert (out["data_date"].astype(str) < out["trade_date"].astype(str)).all()
    return out.reset_index(drop=True)


def build_phase(
    phase: str,
    windows: list[dict],
    gen: LightGBMSingleLabelGenerator,
    label_df: pd.DataFrame,
    tmp_dir: Path,
    idxs: list[int] | None,
) -> pd.DataFrame:
    k = PHASES[phase]
    shift_lookup = build_shift_lookup(k)
    records: list[pd.DataFrame] = []
    t0 = time.time()
    for i in idxs if idxs is not None else range(len(windows)):
        ckpt = tmp_dir / f"rr_{phase}_{i:04d}.parquet"
        if ckpt.exists():
            records.append(pd.read_parquet(ckpt))
            print(f"[{phase} {i:2d}] {windows[i]['window_id']}: ckpt exists, skip",
                  flush=True)
            continue
        w = windows[i]
        w0 = time.time()
        frame, clean, model, center, scale, shifted = train_shifted_model(
            w, shift_lookup, gen, label_df
        )
        out = infer_shifted_span(frame, clean, model, center, scale, shifted)
        if out.empty:
            print(f"[{phase} {i:2d}] {w['window_id']}: empty shifted predict span "
                  f"({shifted['predict_start']}..{shifted['predict_end']}), skip",
                  flush=True)
            continue
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(ckpt, index=False)
        records.append(out)
        print(f"[{phase} {i:2d}] {w['window_id']} span={shifted['predict_start']}"
              f"..{shifted['predict_end']} rows={len(out)} "
              f"({time.time()-w0:.0f}s)", flush=True)

    if not records:
        raise RuntimeError(f"phase {phase}: no rows produced")
    df = pd.concat(records, ignore_index=True)
    df = df.drop_duplicates(["trade_date", "instrument"]).reset_index(drop=True)
    print(f"[{phase}] {len(df)} rows, {df['trade_date'].nunique()} days, "
          f"({time.time()-t0:.0f}s total)")
    return df


def save_run(phase: str, df: pd.DataFrame) -> Path:
    df = df.copy()
    df["signal_id"] = SIG_ID
    df["signal_run_id"] = RUN_ID[phase]
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    df["data_date"] = df["data_date"].astype(str).str[:10]
    store = SignalStore(str(ROOT / "data/research"))
    p = store.save_signal_run(
        SIG_ID, RUN_ID[phase], df,
        manifest={
            "artifact_type": "rawrank_shifted_phase_signal_run",
            "rawrank_of": SRID,
            "shift_trading_days": PHASES[phase],
            "ranking_score": "daily_zscore(raw_prediction)  # no cap, order-preserving",
            "display_score": "clip(ranking_score, +/-3)",
            "experiment": EXPERIMENT,
            "train_window_trading_days": 504,
            "label_id": LABEL_ID,
            "description": "independent shifted rolling S180 pipeline with "
                           "raw(pre-cap)-ranking Top5",
        },
        overwrite=True,
    )
    print(f"wrote {p}  ({len(df)} rows)")
    return p


def is_cap_tie_day(day: pd.DataFrame, n_top: int = 5) -> bool:
    """True if >=5 names sit AT the +3.0 score_raw cap (cap-day top tie)."""
    s = day["score_raw"].sort_values(ascending=False)
    if len(s) < n_top:
        return False
    return float(s.iloc[n_top - 1]) >= 2.999 and float(s.iloc[0]) >= 2.999


def validate_p0(stored: pd.DataFrame, mine: pd.DataFrame, windows: list[dict]) -> int:
    """P0 raw regeneration vs stored (capped) run, per retrain day."""
    stored = stored.copy()
    mine = mine.copy()
    stored["trade_date"] = pd.to_datetime(stored["trade_date"])
    mine["trade_date"] = pd.to_datetime(mine["trade_date"])
    retrain_days = [pd.Timestamp(w["predict_start"]) for w in windows]

    spearmans, matches, capped_mism, clean_mism = [], 0, 0, 0
    total_capped = 0
    examples = []
    for t in retrain_days:
        sd = stored[stored["trade_date"] == t]
        md = mine[mine["trade_date"] == t]
        if sd.empty or md.empty:
            print(f"  [warn] {t.date()}: missing rows stored={len(sd)} mine={len(md)}")
            continue
        common = set(sd["instrument"]) & set(md["instrument"])
        if len(common) < 30:
            continue
        s_sorted = sd[sd["instrument"].isin(common)].set_index("instrument").loc[sorted(common)]
        m_sorted = md[md["instrument"].isin(common)].set_index("instrument").loc[sorted(common)]
        # 1) ordering identity: my score must be monotonic in stored score_raw? No —
        #    mine is zscore(raw); stored is zscore(clip(zscore(raw))). Monotonic in
        #    raw on non-capped days, but cap FLATTENS stored. So compare my score vs
        #    stored on non-capped days only for the cap effect; primary check is Top5.
        rho = s_sorted["score_raw"].corr(m_sorted["score"], method="spearman")
        if np.isfinite(rho):
            spearmans.append(rho)
        s5 = set(s_sorted.sort_values("score_raw", ascending=False).head(5).index)
        m5 = set(m_sorted.sort_values("score", ascending=False).head(5).index)
        capped = is_cap_tie_day(s_sorted)
        total_capped += int(capped)
        if s5 == m5:
            matches += 1
        else:
            if capped:
                capped_mism += 1
            else:
                clean_mism += 1
                examples.append((t, sorted(s5), sorted(m5)))

    print(f"\n=== P0 raw-ranking validation ({len(retrain_days)} retrain days) ===")
    print(f"  capped days (>=5 names at +3.0 cap): {total_capped}/{len(retrain_days)}")
    print(f"  top5 identical: {matches}/{len(retrain_days)}")
    print(f"  top5 DIFFER on capped days: {capped_mism}   "
          f"top5 DIFFER on NON-capped days: {clean_mism}")
    print(f"  spearman(score_raw_stored, score_raw_mine) per day: "
          f"median={np.median(spearmans):.5f} min={np.min(spearmans):.5f}")
    if examples:
        for t, s5, m5 in examples[:5]:
            print(f"  NON-CAPPED MISMATCH {t.date()}: stored={sorted(s5)} mine={sorted(m5)}")
    if clean_mism > 0:
        print("  !! non-capped mismatches exist — ordering is NOT order-preserving")
        return 1
    print("  OK: on every non-capped day top5 is identical; differences occur ONLY "
          "where the stored run had cap ties (the fix)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="p0", choices=sorted(PHASES),
                    help="which phase to build")
    ap.add_argument("--windows", default=None,
                    help="comma-separated window indices (smoke test)")
    ap.add_argument("--validate", action="store_true",
                    help="after build, run P0 raw-vs-stored per-day validation")
    ap.add_argument("--tmp", default="/home/liuming/.openclaw/workspace/SysQ-execution-ledger/scratch/ablation/phase_tmp")
    args = ap.parse_args()

    windows = load_windows()
    gen = LightGBMSingleLabelGenerator(**GEN_KWARGS)
    label_df = load_labels_once()
    idxs = [int(s.strip()) for s in args.windows.split(",")] if args.windows else None

    df = build_phase(args.phase, windows, gen, label_df, Path(args.tmp), idxs)
    path = save_run(args.phase, df)

    if args.validate:
        stored = pd.read_parquet(PREDS_PARQUET)
        stored = stored[["trade_date", "data_date", "instrument", "score_raw", "score"]]
        mine = pd.read_parquet(path)
        rc = validate_p0(stored, mine, windows)
        print(f"signal_run_id = {RUN_ID[args.phase]}")
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
