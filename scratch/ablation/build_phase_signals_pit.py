#!/usr/bin/env python3
"""Build 4 INDEPENDENT shifted rolling PIT S180 signal runs (P0/P5/P10/P15)
for the CSI800 PIT-universe audit Stage 11 phase-robustness check.

Same mechanism as build_phase_signals.py (raw-ranking fix + phase shift),
but on the PIT path:
  * universe csi800 -> csi800_pit_union, pit_membership=True
  * label fwd_ret_180d_raw -> fwd_ret_180d_raw_pit (union label store)
  * distinct _pit experiment / signal ids so nothing clobbers the baseline.
  * after `_load_data`, the PIT membership filter is applied EXACTLY as
    `generate()` does (train + predict share one filtered frame), so a shifted
    window trains on PIT-correct rows at feature-date semantics.

Feature phase-invariance (verified bit-identical on overlap) lets every
shifted window slice its ORIGINAL window's cached frame directly — no qlib
rebuild.  The 9B PIT retrain already wrote the 68 per-window caches under the
PIT identity (source_manifest_hash 2d8ff..., universe csi800_pit_union,
pit_membership true), so this cache-hits and never touches the research cache
key space.

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
from qsys.signal.alpha_v1.training import (  # noqa: E402
    _DEFAULT_LGB_PARAMS,
    train_model,
    predict_model,
)
from qsys.signal.store import SignalStore  # noqa: E402

EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3_pit"
WINDOWS_CSV = ROOT / "data/research/experiments" / EXPERIMENT / "rolling_windows.csv"
SRID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3_pit__"
    "v3a_growth_financial_180d_pit__fwd_ret_180d_raw_pit__daily_zscore__"
    "2021-01-01_2026-07-31"
)
SIG_ID = "fwd_ret_180d_raw_pit__daily_zscore"
PREDS_PARQUET = ROOT / "data/research/signals" / SIG_ID / SRID / "predictions.parquet"
LABEL_ID = "fwd_ret_180d_raw_pit"
HORIZON = horizon_from_label_id(LABEL_ID)

GEN_KWARGS = dict(
    universe="csi800_pit_union",
    n_estimators=300,
    feature_list_id="v3a_plus_liquidity_financial_rc",
    use_feature_cache=True,
    feature_cache_root=str(ROOT / "data/feature_cache"),
    source_manifest_hash="2d8ff143be01c3a99b44eeffd58706c91c774f93b93c13914f4d88ef355a1e2f",
    pit_membership=True,
)

# phases: {phase_name: shift_k (trading days)}
PHASES = {"p0": 0, "p5": 5, "p10": 10, "p15": 15}
SHIFT_CAL_START, SHIFT_CAL_END = "2017-01-01", "2026-12-31"

def run_id_for(phase: str, seeds: list[int]) -> str:
    tag = "ens3" if len(seeds) > 1 else "rawrank"
    return f"{SIG_ID}__rr_{phase}__{tag}__{EXPERIMENT}"


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
    seeds: list[int] | None = None,
):
    """Train the SHIFTED window's model(s) by slicing the ORIGINAL cache frame.

    Returns (frame, clean, models, shifted_win) where models = [(model, center,
    scale), ...] — one per seed (E1 3-seed ensemble averages raw predictions
    downstream; single-model uses seeds=[42]).
    """
    shifted = {
        key: shift_date(win[key], shift_lookup, win["window_id"])
        for key in ("train_start", "train_end", "predict_start", "predict_end")
    }
    # cache HIT on the ORIGINAL window dates (feature PIT-invariance verified)
    frame, clean = gen._load_data(win["train_start"], extended_end(win["predict_end"]))

    # PIT audit: apply membership filter EXACTLY as generate() does — once on
    # the shared frame, so the shifted train and predict subsets see identical
    # PIT rows (feature-date semantics).  Without this, the shifted train would
    # train on the raw union frame (non-members included) — a PIT violation.
    if gen.pit_membership:
        frame = gen._apply_pit_membership(frame)

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
    models = []
    for sd in (seeds or [42]):
        params = (dict(gen.lgb_params) if gen.lgb_params is not None
                  else dict(_DEFAULT_LGB_PARAMS))  # None -> defaults; explicit {} kept bare
        params["seed"] = sd
        model, center, scale = train_model(
            X_tr.loc[y_tr.index], y_tr, f"{tag}_s{sd}",
            n_estimators=gen.n_estimators, lgb_params=params,
        )
        models.append((model, center, scale))
    return frame, clean, models, shifted


def infer_shifted_span(
    frame: pd.DataFrame,
    clean: list[str],
    models: list,
    shifted: dict,
) -> pd.DataFrame:
    """Inference over the shifted predict span; cross-sectional zscore of the
    mean raw prediction across seeds."""
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
    X = pred[clean].fillna(0.0).astype(np.float32)
    raw = np.mean(
        [predict_model(model, center, scale, X).values
         for model, center, scale in models],
        axis=0,
    )
    pred["_raw"] = raw
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
    seeds: list[int],
) -> tuple[pd.DataFrame, list[dict]]:
    k = PHASES[phase]
    shift_lookup = build_shift_lookup(k)
    records: list[pd.DataFrame] = []
    kept: list[dict] = []  # windows whose shifted predict span was actually built
    seeds = seeds or [42]  # match train_shifted_model's fallback, so the fp
                           # never labels a seed-42 ckpt as seed-batch []
    # provenance-bound cache key: the filename carries the experiment
    # fingerprint, so a checkpoint is reused ONLY when produced by the exact
    # same feature/label/model/code/calendar state (see _checkpoint_fingerprint).
    fp = _checkpoint_fingerprint(phase, seeds, gen)
    seeds_tag = f"s{seeds[0]}" if len(seeds) == 1 else "_".join(f"s{s}" for s in seeds)
    t0 = time.time()
    for i in idxs if idxs is not None else range(len(windows)):
        ckpt = tmp_dir / f"rr_{phase}_{seeds_tag}_{fp}_{i:04d}.parquet"
        if ckpt.exists():
            records.append(pd.read_parquet(ckpt))
            kept.append(windows[i])
            print(f"[{phase} {i:2d}] {windows[i]['window_id']}: ckpt exists, skip",
                  flush=True)
            continue
        w = windows[i]
        w0 = time.time()
        frame, clean, models, shifted = train_shifted_model(
            w, shift_lookup, gen, label_df, seeds
        )
        out = infer_shifted_span(frame, clean, models, shifted)
        if out.empty:
            print(f"[{phase} {i:2d}] {w['window_id']}: empty shifted predict span "
                  f"({shifted['predict_start']}..{shifted['predict_end']}), skip",
                  flush=True)
            continue
        ckpt.parent.mkdir(parents=True, exist_ok=True)
        out.to_parquet(ckpt, index=False)
        records.append(out)
        kept.append(w)
        print(f"[{phase} {i:2d}] {w['window_id']} span={shifted['predict_start']}"
              f"..{shifted['predict_end']} rows={len(out)} "
              f"({time.time()-w0:.0f}s)", flush=True)

    if not records:
        raise RuntimeError(f"phase {phase}: no rows produced")
    df = pd.concat(records, ignore_index=True)
    df = df.drop_duplicates(["trade_date", "instrument"]).reset_index(drop=True)
    print(f"[{phase}] {len(df)} rows, {df['trade_date'].nunique()} days, "
          f"built {len(kept)}/{len(windows)} windows "
          f"({time.time()-t0:.0f}s total)")
    return df, kept


def _effective_model_params(gen: LightGBMSingleLabelGenerator) -> dict:
    """Params train_model actually receives, WITHOUT the per-seed override.

    Mirrors the train_shifted_model call site: None -> _DEFAULT_LGB_PARAMS,
    an explicit override -> the override itself ({} stays bare), then the
    seed is stripped so seed experiments compare equal.
    """
    params = (
        dict(_DEFAULT_LGB_PARAMS)
        if gen.lgb_params is None
        else dict(gen.lgb_params)
    )
    params.pop("seed", None)
    return params


def _label_manifest() -> tuple[dict, str | None]:
    """(label manifest dict, sha256 of manifest file bytes) from LabelStore.
    Unavailable -> ({}, None), recorded honestly; never crashes the run."""
    label_store = LabelStore(str(ROOT / "data/research"))
    try:
        label_mf = label_store.load_manifest(LABEL_ID)
        label_manifest_path = label_store.paths.label_manifest(LABEL_ID)
        if label_manifest_path.exists():
            return label_mf, hashlib.sha256(
                label_manifest_path.read_bytes()).hexdigest()
        return label_mf, None
    except Exception:
        return {}, None


# modules that directly determine a checkpoint's produced rows. The ckpt
# fingerprint hashes ALL of them, so an edit anywhere in the row-producing
# pipeline (not just this script) orphans stale checkpoints instead of
# silently reusing them under new code.
_CODE_FILES = [
    Path(__file__).resolve(),
    ROOT / "qsys/signal/alpha_v1/training.py",
    ROOT / "qsys/research/generators/lightgbm_single_label.py",
    ROOT / "qsys/research/generators/utils.py",
    ROOT / "qsys/data/calendar.py",
    ROOT / "qsys/label/store.py",
]


def _shift_calendar_hash() -> str:
    """Fingerprint of the trading-calendar DATA over the shift range. Every
    shifted train/predict span (and thus every produced row) comes from this
    calendar, so a calendar snapshot change must orphan checkpoints built on
    the old one."""
    cal = get_trading_calendar(SHIFT_CAL_START, SHIFT_CAL_END)
    return hashlib.sha256("\n".join(sorted(cal)).encode("utf-8")).hexdigest()


def _checkpoint_fingerprint(
    phase: str, seeds: list[int], gen: LightGBMSingleLabelGenerator
) -> str:
    """Per-batch cache key that binds a checkpoint to its exact provenance.

    A checkpoint holds the mean-raw output of a SPECIFIC (phase, seed-batch)
    under a SPECIFIC feature/label/model/code/calendar state. Unlike
    model_config_hash (seed stripped, so seed experiments compare equal), this
    fingerprint MUST include the seed — changing seeds must orphan the old
    ckpt. Any change in effective params (e.g. the #245 None->defaults fix),
    feature/label snapshot, universe, the windows file, the trading calendar,
    or ANY row-producing pipeline code (see _CODE_FILES) yields a different
    filename, so build_phase can never reuse a checkpoint produced by
    different provenance; stale checkpoints simply stop matching.
    """
    seeds = seeds or [42]  # match train_shifted_model's fallback: never label
                           # a seed-42 ckpt as seed-batch []
    _, label_manifest_hash = _label_manifest()

    def _sha_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    src = {
        "phase": phase,
        "shift_trading_days": PHASES[phase],
        "universe": gen.universe,
        "feature_list_id": gen.feature_list_id,
        "feature_hash": gen.source_manifest_hash,
        "label_id": LABEL_ID,
        "label_manifest_hash": label_manifest_hash,
        "n_estimators": gen.n_estimators,
        "lgb_params": _effective_model_params(gen),  # seed-stripped effective
        "seeds": sorted(seeds),  # ckpt binds the seed batch
        "windows_hash": _sha_file(WINDOWS_CSV),
        "shift_calendar": _shift_calendar_hash(),  # trading-calendar data
        "code": {str(p.relative_to(ROOT)): _sha_file(p) for p in _CODE_FILES},
    }
    return hashlib.sha256(
        json.dumps(src, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]


def _research_invariants(
    gen: LightGBMSingleLabelGenerator,
    windows: list[dict],
    phase: str,
) -> dict[str, object]:
    """Fingerprints of the dimensions that MUST stay constant across
    phase/seed experiments of the same research line (Task 4 invariant).

    Verification contract:
      * phase runs (P0/P5/P10/P15): universe/feature/label/model_config
        fingerprints identical; only `shift_trading_days` and the EFFECTIVE
        shifted train/predict date ranges differ.
      * seed runs (seed42/7/77/123/456): identical fingerprints AND identical
        date ranges/shift; only the top-level `seeds` field differs.

    ``windows`` MUST be the EFFECTIVE window list (the ones actually built;
    build_phase drops windows whose shifted predict span falls outside the
    trading calendar). Recording the nominal full schedule instead would lie
    about what was produced at the calendar edge — e.g. p15's last window,
    whose +15d shift inverts out of bounds and is skipped.

    Naming is deliberately honest: ``*_id_hash`` fields hash the ID string
    only (NOT the underlying data); content-level fingerprints carry an
    explicit ``*_snapshot_hash`` / ``*_manifest_hash`` name and cite their
    source artifact.  ``model_config_hash`` is computed from the EFFECTIVE
    params (what train_model receives) with the seed stripped, so seed runs
    compare equal.  ``code_version`` == git_commit (also stamped top-level by
    ``with_standard_metadata``).
    """
    from qsys.research.manifest import _get_git_commit

    model_config = {
        "n_estimators": gen.n_estimators,
        "lgb_params": _effective_model_params(gen),
    }
    # effective schedule span: shift applied to the first/last window exactly
    # as train_shifted_model does per-window (same lookup + shift_date)
    shift_lookup = build_shift_lookup(PHASES[phase])

    def _sh(win: dict, key: str) -> str:
        return shift_date(win[key], shift_lookup, win["window_id"])

    # fail loudly rather than record an inverted schedule (caller bug if hit)
    if _sh(windows[0], "predict_start") > _sh(windows[-1], "predict_end"):
        raise RuntimeError(
            f"{phase}: effective predict span inverted "
            f"{_sh(windows[0], 'predict_start')}..{_sh(windows[-1], 'predict_end')} "
            f"— windows passed must be the built (kept) windows"
        )

    # label artifact provenance from the LabelStore manifest: real recorded
    # fingerprint; unavailable -> None, recorded honestly (metadata must not
    # crash the signal save)
    label_mf, label_manifest_hash = _label_manifest()

    return {
        "universe": gen.universe,
        "universe_id_hash": hashlib.sha256(
            gen.universe.encode("utf-8")).hexdigest(),  # ID only
        "universe_snapshot_hash": label_mf.get("universe_hash"),  # label manifest's recorded csi800 snapshot
        "feature_list_id": gen.feature_list_id,
        "feature_hash": gen.source_manifest_hash,  # feature snapshot content hash
        "label_id": LABEL_ID,
        "label_id_hash": hashlib.sha256(
            LABEL_ID.encode("utf-8")).hexdigest(),  # ID only
        "label_manifest_hash": label_manifest_hash,  # sha256 of label manifest file
        "model_config_hash": hashlib.sha256(
            json.dumps(model_config, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "train_window_trading_days": 504,
        "train_date_range": {
            "start": _sh(windows[0], "train_start"),
            "end": _sh(windows[-1], "train_end"),
        },
        "predict_date_range": {
            "start": _sh(windows[0], "predict_start"),
            "end": _sh(windows[-1], "predict_end"),
        },
        "code_version": _get_git_commit() or "",
    }


def save_run(phase: str, df: pd.DataFrame, seeds: list[int],
             gen: LightGBMSingleLabelGenerator, windows: list[dict]) -> Path:
    """Persist the built signal. ``windows`` MUST be the EFFECTIVE window list
    (the ones actually built after build_phase drops out-of-calendar shifted
    spans) — research_invariants fingerprints must reflect what was produced,
    not the nominal full schedule."""
    rid = run_id_for(phase, seeds)
    df = df.copy()
    df["signal_id"] = SIG_ID
    df["signal_run_id"] = rid
    df["trade_date"] = df["trade_date"].astype(str).str[:10]
    df["data_date"] = df["data_date"].astype(str).str[:10]
    store = SignalStore(str(ROOT / "data/research"))
    p = store.save_signal_run(
        SIG_ID, rid, df,
        manifest={
            "artifact_type": "rawrank_shifted_phase_signal_run",
            "research_invariants": _research_invariants(gen, windows, phase),
            "rawrank_of": SRID,
            "shift_trading_days": PHASES[phase],
            "ranking_score": "daily_zscore(raw_prediction)  # no cap, order-preserving",
            "display_score": "clip(ranking_score, +/-3)",
            "experiment": EXPERIMENT,
            "train_window_trading_days": 504,
            "label_id": LABEL_ID,
            "seeds": seeds,
            "ensemble": "mean_of_seed_raw_predictions" if len(seeds) > 1 else "single_model",
            "description": "independent shifted rolling S180 pipeline with "
                           "raw(pre-cap)-ranking Top5"
                           + (f"; {len(seeds)}-seed mean ensemble" if len(seeds) > 1 else ""),
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
    ap.add_argument("--seeds", default="42",
                    help="comma-separated seeds; >1 seed -> 3-seed mean ensemble "
                         "(E1), raw preds averaged before cross-sectional zscore")
    ap.add_argument("--validate", action="store_true",
                    help="after build, run P0 raw-vs-stored per-day validation")
    ap.add_argument("--tmp", default="/home/liuming/.openclaw/workspace/SysQ-execution-ledger/scratch/ablation/phase_tmp")
    args = ap.parse_args()

    windows = load_windows()
    gen = LightGBMSingleLabelGenerator(**GEN_KWARGS)
    label_df = load_labels_once()
    idxs = [int(s.strip()) for s in args.windows.split(",")] if args.windows else None
    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]

    df, kept = build_phase(args.phase, windows, gen, label_df, Path(args.tmp), idxs, seeds)
    path = save_run(args.phase, df, seeds, gen, kept)

    if args.validate:
        stored = pd.read_parquet(PREDS_PARQUET)
        stored = stored[["trade_date", "data_date", "instrument", "score_raw", "score"]]
        mine = pd.read_parquet(path)
        rc = validate_p0(stored, mine, windows)
        print(f"signal_run_id = {run_id_for(args.phase, seeds)}")
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
