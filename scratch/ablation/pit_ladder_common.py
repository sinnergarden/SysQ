#!/usr/bin/env python3
"""Shared loaders for the PIT universe ladder diagnostic (U0 vs U2).

DIAGNOSTIC experiment (non-production, no deployment path).

Frozen (must NOT change):
  * label formula = S180 forward return (adjusted close, raw, no normalization)
  * model = LightGBM regression, n_estimators=300, seed 42 (default
    _DEFAULT_LGB_PARAMS; lgb_params=None -> seed 42)
  * ranking score = per-trade_date UNCLIPPED cross-sectional zscore of raw pred
  * Top5 / Top20 selection
  * feature_list_id = v3a_plus_liquidity_financial_rc
  * calendar = the 68-window schedule, train_window_days=504, step_days=20

Universe semantics (train universe == predict universe, no future info):
  U0 = PIT CSI800 member-as-of  (csi800_pit_union registry, csi800_pit_v1 artifact)
  U2 = PIT CSI1800 member-as-of (csi1800_pit_union registry, csi1800_pit_v1 artifact)
  U1/U3 deferred (ever-member registry / liquid A-share) — scaffold kept.

Run from the MAIN SysQ cwd so `qsys` + `data` resolve.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qsys.data.calendar import get_trading_calendar
from qsys.label.store import LabelStore
from qsys.research.generators.lightgbm_single_label import LightGBMSingleLabelGenerator
from qsys.research.generators.utils import (
    build_next_trading_date_lookup,
    build_prev_trading_date_lookup,
    check_training_label_maturity,
    horizon_from_label_id,
)
from qsys.signal.alpha_v1.training import train_model, predict_model

# ── Frozen experimental constants ─────────────────────────────────────
EXPERIMENT = "financial_rc_180d_rolling_5y_to_202607_v3_pit"
WINDOWS_CSV = ROOT / "data/research/experiments" / EXPERIMENT / "rolling_windows.csv"
OUT_DIR = ROOT / "scratch" / "ablation" / "pit_ladder"
REGISTRY_DIR = ROOT / "data" / "qlib_bin" / "instruments"

HORIZON = 180
FEATURE_LIST_ID = "v3a_plus_liquidity_financial_rc"
SOURCE_MANIFEST_HASH = "2d8ff143be01c3a99b44eeffd58706c91c774f93b93c13914f4d88ef355a1e2f"
N_ESTIMATORS = 300

# Sampled retrain dates: label-maturity bound.  A 180d forward label from
# predict_start t is observable only if t + 180 trading days <= last close
# (2026-08-21), which puts the hard cut-off around 2025-11.
MAX_PREDICT_START = "2025-11-01"
N_SAMPLED = 20

# U0 stored checkpoint signal (rolling runner output, daily_zscore transform).
U0_STORED_SRID = (
    "rolling__financial_rc_180d_rolling_5y_to_202607_v3_pit__"
    "v3a_growth_financial_180d_pit__fwd_ret_180d_raw_pit__daily_zscore__"
    "2021-01-01_2026-07-31"
)
U0_STORED_PREDS = (
    ROOT / "data/research/signals" / "fwd_ret_180d_raw_pit__daily_zscore"
    / U0_STORED_SRID / "predictions.parquet"
)

# ── Universe registry ─────────────────────────────────────────────────
# mode = how the diagnostic obtains the shared feature frame:
#   per_window         : U0 — per-window cache-hit on the existing 68-window
#                        v3 caches (near-zero cost, exact replication).
#   materialize_once   : U2 — one full-range materialization, sliced per window.
UNIVERSES = {
    "U0": {
        "label": "U0 PIT CSI800",
        "universe": "csi800_pit_union",
        "artifact": "csi800_pit_v1",
        "pit_mode": "member_as_of",
        "label_id": "fwd_ret_180d_raw_pit",
        "mode": "per_window",
        # Legacy flag True — the v3 per-window cache identity carries
        # `pit_membership` (bool), so this must match the stored run exactly
        # or every U0 window would cache-miss and force a full rebuild.
        "gen_extra": {"pit_membership": True},
    },
    "U2": {
        "label": "U2 PIT CSI1800",
        "universe": "csi1800_pit_union",
        "artifact": "csi1800_pit_v1",
        "pit_mode": "member_as_of",
        "label_id": "fwd_ret_180d_raw_pit_csi1800",
        "mode": "materialize_once",
        "gen_extra": {
            "pit_filter_mode": "member_as_of",
            "pit_universe_artifact": "csi1800_pit_v1",
        },
    },
}

# Deferred universes (scaffold; not runnable until their label stores exist).
UNIVERSES["U1"] = {
    "label": "U1 CSI800 ever-member-as-of",
    "universe": "csi800_pit_full_union",
    "artifact": "csi800_pit_v1",
    "pit_mode": "ever_member_as_of",
    "label_id": "fwd_ret_180d_raw_pit_ever",
    "mode": "materialize_once",
    "gen_extra": {
        "pit_filter_mode": "ever_member_as_of",
        "pit_universe_artifact": "csi800_pit_v1",
    },
}
UNIVERSES["U3"] = {
    "label": "U3 liquid A-share",
    "universe": "all",
    "artifact": "csi_liquid_pit_v1",
    "pit_mode": "member_as_of",
    "label_id": "fwd_ret_180d_raw_pit_liquid",
    "mode": "materialize_once",
    "gen_extra": {
        "pit_filter_mode": "member_as_of",
        "pit_universe_artifact": "csi_liquid_pit_v1",
        "liquidity_exclusion_path": str(
            ROOT / "data/research/universes/csi_liquid_pit_v1/raw/liquidity_exclusions.parquet"
        ),
    },
}

# ── Small helpers ─────────────────────────────────────────────────────


def zscore_no_clip(s: pd.Series) -> pd.Series:
    """Per-day cross-sectional z-score WITHOUT clipping (the ranking score)."""
    std = s.std(ddof=0)
    if pd.isna(std) or std < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / std


def extended_end(predict_end: str) -> str:
    return (datetime.strptime(predict_end, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")


def load_windows() -> pd.DataFrame:
    return pd.read_csv(WINDOWS_CSV)


def sample_windows(n: int = N_SAMPLED, max_predict_start: str = MAX_PREDICT_START) -> pd.DataFrame:
    """Deterministic uniform sample of the retrain windows.

    Qualified windows are those with ``predict_start <= max_predict_start``
    (180d fwd labels must be fully observable at eval time).  The sample is
    taken with ``linspace`` over the qualified range so the 20 dates spread
    across 2021 → ~2025 instead of truncating at the first 39 windows (the
    naive ``range(0, N, N//20)[:20]`` stride-2 sample stops ~2024-03).
    """
    win = load_windows()
    qual = win[win["predict_start"] <= max_predict_start].reset_index(drop=True)
    if len(qual) < n:
        raise RuntimeError(f"only {len(qual)} qualified windows, need {n}")
    idx = np.unique(np.linspace(0, len(qual) - 1, n).round().astype(int)).tolist()
    return qual.iloc[idx].reset_index(drop=True)


def build_gen(key: str) -> LightGBMSingleLabelGenerator:
    u = UNIVERSES[key]
    return LightGBMSingleLabelGenerator(
        label_id=u["label_id"],
        universe=u["universe"],
        n_estimators=N_ESTIMATORS,
        feature_list_id=FEATURE_LIST_ID,
        use_feature_cache=True,
        write_through=True,
        feature_cache_root=str(ROOT / "data/feature_cache"),
        source_manifest_hash=SOURCE_MANIFEST_HASH,
        **u["gen_extra"],
    )


def load_label(key: str) -> pd.DataFrame:
    return LabelStore(str(ROOT / "data/research")).load_labels(UNIVERSES[key]["label_id"])


def load_registry(key: str) -> pd.DataFrame:
    """Parse the qlib_bin registry for a universe into (instrument, start, end)."""
    u = UNIVERSES[key]
    path = REGISTRY_DIR / f"{u['universe']}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"registry not found: {path}")
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"malformed registry line: {line!r}")
        rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return pd.DataFrame(rows, columns=["instrument", "start", "end"])


def pit_daily_size(registry: pd.DataFrame, dates: list[str]) -> pd.Series:
    """Per-date instrument count where start <= date <= end (PIT daily size)."""
    st = registry["start"].astype(str).str.replace("-", "", regex=False).astype(int)
    en = registry["end"].astype(str).str.replace("-", "", regex=False).astype(int)
    d = pd.Series(dates).astype(str).str.replace("-", "", regex=False).astype(int)
    return d.apply(lambda x: int(((st <= x) & (x <= en)).sum()))


def load_materialized_frame(key: str) -> tuple[pd.DataFrame, list[str]]:
    """Materialize (or cache-hit) the full-range PIT-filtered frame for U2/U3.

    For per_window universes (U0) this raises — U0 frames are loaded per window.
    """
    u = UNIVERSES[key]
    if u["mode"] != "materialize_once":
        raise ValueError(f"{key} uses mode {u['mode']}; not materialize_once")
    sampled = sample_windows()
    full_start = "2017-01-01"  # buffer before earliest train_start (2018-03-13)
    full_end = extended_end(sampled["predict_end"].max())
    gen = build_gen(key)
    frame, clean = gen._load_data(full_start, full_end)
    frame = gen._apply_pit_membership(frame)
    return frame, clean, gen


# ── Train + predict (exact replication of generate() with unclipped rank) ──


def train_predict_window(
    frame: pd.DataFrame,
    clean: list[str],
    gen: LightGBMSingleLabelGenerator,
    win: dict,
    label_df: pd.DataFrame,
) -> pd.DataFrame:
    """Replicate LightGBMSingleLabelGenerator.generate() for one window.

    * train rows: feature date f labeled with fwd_ret[next_td(f)] (F01 strict
      alignment), label-maturity gate checked before training.
    * predict rows: execution day d uses features from prev_td(d); F01 assert
      data_date < trade_date on every emitted row.
    * ranking score = per-trade_date UNCLIPPED zscore of raw pred.
    """
    train_start, train_end = win["train_start"], win["train_end"]
    predict_start, predict_end = win["predict_start"], win["predict_end"]

    check_training_label_maturity(train_end, predict_start, HORIZON)

    next_td = build_next_trading_date_lookup(train_start, train_end)
    train = frame[
        (frame["trade_date"] >= train_start) & (frame["trade_date"] <= train_end)
    ].copy()
    train["label_date"] = train["trade_date"].map(next_td)
    train = train.merge(
        label_df[["trade_date", "instrument", "label_value"]].rename(
            columns={"trade_date": "label_date"}
        ),
        on=["label_date", "instrument"], how="left",
    )
    y_valid = train["label_value"].notna()
    X_tr = train[clean].fillna(0.0).astype(np.float32)
    y_tr = train.loc[y_valid, "label_value"].astype(float)
    if y_tr.empty:
        raise ValueError(
            f"No valid training samples for window {win['window_id']} "
            f"({train_start}..{train_end})"
        )

    model, center, scale = train_model(
        X_tr.loc[y_tr.index], y_tr, f"{win['window_id']}_seed42",
        n_estimators=gen.n_estimators, lgb_params=gen.lgb_params,
    )

    window_cal = get_trading_calendar(predict_start, predict_end)
    prev_td = build_prev_trading_date_lookup(predict_start, predict_end)
    feature_dates = sorted({prev_td.get(d, d) for d in window_cal})
    pred = frame[frame["trade_date"].isin(feature_dates)].copy()
    if pred.empty:
        raise ValueError(
            f"No feature data for execution window [{predict_start}, {predict_end}]"
        )
    pred["pred"] = predict_model(
        model, center, scale, pred[clean].fillna(0.0).astype(np.float32)
    ).values

    f_to_d = {prev_td.get(d, d): d for d in window_cal}
    rows: list[dict] = []
    for f in feature_dates:
        td = f_to_d.get(f)
        sub = pred[pred["trade_date"] == f]
        if td is None or sub.empty:
            continue
        assert str(f) < td, f"F01 lookahead: feature date {f} >= trade_date {td}"
        z = zscore_no_clip(sub["pred"])
        for i, r in sub.iterrows():
            rows.append({
                "trade_date": td,
                "data_date": str(f),
                "instrument": str(r["instrument"]),
                "score": float(z.loc[i]) if pd.notna(z.loc[i]) else 0.0,
            })
    return pd.DataFrame(rows)


# ── Evaluation (label-store 口径, matches signal_analytics RankIC) ────


def eval_metrics(day_pred: pd.DataFrame, label_at_t: pd.Series) -> dict:
    """Metrics for one (universe, trade_date) row.

    ``day_pred``: DataFrame(instrument, score) for one trade_date (unclipped).
    ``label_at_t``: Series(instrument -> fwd180 label_value) from the label store
    (indexed by instrument).  Joined on instrument — inner join, same as
    signal_analytics._rank_ic_single (min_count is applied by the caller).
    """
    if day_pred.empty:
        return {}
    joined = day_pred.set_index("instrument").join(label_at_t, how="inner")
    joined = joined.dropna(subset=["score", "label_value"])
    if len(joined) < 5:
        return {}

    scored = joined.sort_values("score", ascending=False)
    top5 = scored.head(5)
    top20 = scored.head(20)
    univ_ew = float(scored["label_value"].mean())

    def _winners(fwd: float) -> bool:
        return fwd > 0.50  # winner50

    label_vals = scored["label_value"]
    n_win50 = int((label_vals > 0.50).sum())
    n_win100 = int((label_vals > 1.00).sum())

    # Spearman rank IC (rank of score vs rank of label_value).
    rank_ic = float(scored["score"].corr(scored["label_value"], method="spearman"))
    ic = float(scored["score"].corr(scored["label_value"], method="pearson"))

    # NDCG@5: gain = max(fwd, 0), positions i=1..5 discounted log2(i+1).
    dcg = sum(
        max(0.0, float(top5.iloc[i]["label_value"])) / np.log2(i + 2)
        for i in range(len(top5))
    )
    idcg = sum(
        max(0.0, v) / np.log2(i + 2)
        for i, v in enumerate(sorted(label_vals.tolist(), reverse=True)[:5])
    )
    ndcg5 = dcg / idcg if idcg > 0 else float("nan")

    def _capture(top: pd.DataFrame) -> tuple[float, float]:
        in50 = int((top["label_value"] > 0.50).sum())
        in100 = int((top["label_value"] > 1.00).sum())
        c50 = in50 / n_win50 if n_win50 else float("nan")
        c100 = in100 / n_win100 if n_win100 else float("nan")
        return c50, c100

    cap50, cap100 = _capture(top5)
    prec50 = int((top5["label_value"] > 0.50).sum()) / 5
    prec100 = int((top5["label_value"] > 1.00).sum()) / 5

    return {
        "n_scored": int(len(scored)),
        "n_win50": n_win50,
        "n_win100": n_win100,
        "top5_fwd180": float(top5["label_value"].mean()),
        "top20_fwd180": float(top20["label_value"].mean()),
        "univ_ew_fwd180": univ_ew,
        "top5_excess_ew": float(top5["label_value"].mean()) - univ_ew,
        "top20_excess_ew": float(top20["label_value"].mean()) - univ_ew,
        "ic_180": ic,
        "rank_ic_180": rank_ic,
        "ndcg_at5": ndcg5,
        "top5_capture50": cap50,
        "top5_capture100": cap100,
        "top5_precision50": prec50,
        "top5_precision100": prec100,
        "top5_names": ",".join(top5.index.tolist()),
        "top20_names": ",".join(top20.index.tolist()),
    }


def random_top5_baseline(day_pred: pd.DataFrame, label_at_t: pd.Series, seeds: list[int]) -> dict:
    """Deterministic random Top5 baseline over fixed seeds (0..99).

    For each seed, sample 5 instruments WITHOUT replacement from the scored
    universe and average their label_value / capture100.  Seeds are stored in
    the artifact so the baseline is reproducible.
    """
    joined = day_pred.set_index("instrument").join(label_at_t, how="inner").dropna(
        subset=["score", "label_value"])
    if len(joined) < 5:
        return {}
    insts = joined.index.tolist()
    n_win100 = int((joined["label_value"] > 1.00).sum())

    fwd_means, cap100s = [], []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        pick = rng.choice(insts, size=5, replace=False)
        sub = joined.loc[pick]
        fwd_means.append(float(sub["label_value"].mean()))
        if n_win100:
            cap100s.append(int((sub["label_value"] > 1.00).sum()) / n_win100)
    random_top5 = float(np.mean(fwd_means))
    random_cap100 = float(np.mean(cap100s)) if cap100s else float("nan")
    return {
        "random_top5_fwd180": random_top5,
        "random_capture100": random_cap100,
    }


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
