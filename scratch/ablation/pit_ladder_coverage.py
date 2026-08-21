#!/usr/bin/env python3
"""Universe coverage report — GATE prerequisite before any model run.

Per universe (U0, U2) reports:
  * registry: unique instruments, span rows, PIT daily size (avg/min/max) over
    the sampled-window predict calendar
  * canonical data availability: #registry instruments with any feather bars,
    #with bars covering the test range, #missing entirely
  * missing feature ratio: NaN fraction in the PIT-filtered materialized frame
    (fillna 0 happens at train time — the ratio reported here is pre-fillna)
  * missing label ratio: fraction of sampled train rows with no label_value

Run from MAIN SysQ cwd.  Requires the U2 backfill + qlib refresh to be done
(the materialization needs real qlib bins for the csi1000-only symbols).

Usage:
    python scratch/ablation/pit_ladder_coverage.py --universes U0,U2

Writes scratch/ablation/pit_ladder/coverage_report.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/home/liuming/.openclaw/workspace/SysQ")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scratch.ablation.pit_ladder_common import (  # noqa: E402
    OUT_DIR,
    load_label,
    load_materialized_frame,
    load_registry,
    pit_daily_size,
    sample_windows,
    write_json,
)
from qsys.data.calendar import get_trading_calendar  # noqa: E402

CANONICAL_DIR = ROOT / "data" / "canonical" / "daily"


def canonical_coverage(registry: pd.DataFrame) -> dict:
    """How many registry instruments have real canonical bars in [2018, 2026]."""
    n_total = registry["instrument"].nunique()
    have_any, have_test_range, missing = 0, 0, 0
    for code in registry["instrument"].unique():
        f = CANONICAL_DIR / f"{code}.feather"
        if not f.is_file():
            missing += 1
            continue
        try:
            d = pd.read_feather(f, columns=["ts_code", "trade_date"])
        except Exception:
            missing += 1
            continue
        if d.empty:
            missing += 1
            continue
        have_any += 1
        dates = d["trade_date"].astype(str)
        if (dates >= "20180101").any() and (dates <= "20260731").any():
            have_test_range += 1
    return {
        "n_registry_instruments": n_total,
        "n_canonical_any_bars": have_any,
        "n_canonical_test_range": have_test_range,
        "n_canonical_missing": missing,
        "canonical_test_range_ratio": have_test_range / n_total if n_total else 0.0,
    }


def frame_stats(frames: list[pd.DataFrame], clean: list[str], sampled_windows: pd.DataFrame,
                label_df: pd.DataFrame) -> dict:
    """Feature/label missing ratios over the sampled windows.

    ``frames`` is one PIT-filtered feature frame per sampled window (U0:
    per-window cache-hit frames; U2: slices of the materialized full frame).
    Ratios are row-weighted across windows.
    """
    from qsys.research.generators.utils import build_next_trading_date_lookup

    n_all = 0
    nan_cells = 0
    total_cells = 0
    n_train_rows = 0
    n_missing_label = 0
    feat_nan_sum = pd.Series(dtype=float)
    seen_frames: list[int] = []
    for (_, w), frame in zip(sampled_windows.iterrows(), frames):
        # U2 materialize-once passes the SAME full frame for every window; count
        # its feature stats once, not 20x (label stats below still iterate all
        # windows — each window has its own train-range label check).
        if id(frame) not in seen_frames:
            seen_frames.append(id(frame))
            n_all += len(frame)
            feat_sub = frame[clean]
            nan_cells += int(feat_sub.isna().sum().sum())
            total_cells += int(np.prod(feat_sub.shape)) if feat_sub.shape[0] else 0
            feat_nan_sum = feat_nan_sum.add(feat_sub.isna().sum(), fill_value=0)

        ts, te = w["train_start"], w["train_end"]
        seg = frame[(frame["trade_date"] >= ts) & (frame["trade_date"] <= te)].copy()
        next_td = build_next_trading_date_lookup(ts, te)
        seg["label_date"] = seg["trade_date"].map(next_td)
        seg = seg.merge(
            label_df[["trade_date", "instrument", "label_value"]].rename(
                columns={"trade_date": "label_date"}),
            on=["label_date", "instrument"], how="left",
        )
        n_train_rows += len(seg)
        n_missing_label += int(seg["label_value"].isna().sum())

    missing_feature_ratio = nan_cells / total_cells if total_cells else float("nan")
    n_feat = len(clean)
    per_feat = (feat_nan_sum / n_all) if n_all else feat_nan_sum
    worst_features = per_feat.sort_values(ascending=False).head(5).to_dict()

    return {
        "n_materialized_rows": n_all,
        "missing_feature_ratio": missing_feature_ratio,
        "worst_features": worst_features,
        "n_train_rows_all_windows": n_train_rows,
        "n_train_rows_missing_label": n_missing_label,
        "missing_label_ratio": n_missing_label / n_train_rows if n_train_rows else float("nan"),
    }


def coverage_for(key: str, sampled_windows: pd.DataFrame, calendar: list[str]) -> dict:
    from scratch.ablation.pit_ladder_common import UNIVERSES

    u = UNIVERSES[key]
    print(f"\n=== {key} {u['label']} ===", flush=True)

    registry = load_registry(key)
    n_unique = registry["instrument"].nunique()
    print(f"  registry: {n_unique} unique instruments, {len(registry)} span rows", flush=True)

    sizes = pit_daily_size(registry, calendar)
    print(f"  PIT daily size (sampled-window predict cal): avg={sizes.mean():.0f} "
          f"min={sizes.min()} max={sizes.max()}", flush=True)

    cc = canonical_coverage(registry)
    print(f"  canonical: any_bars={cc['n_canonical_any_bars']}/{n_unique} "
          f"test_range={cc['n_canonical_test_range']} missing={cc['n_canonical_missing']}", flush=True)

    # Feature frame(s): U0 per-window cache-hit frames (exact replication of the
    # stored run); U2 one full-range materialization sliced per window.
    label_df = load_label(key)
    from scratch.ablation.pit_ladder_common import build_gen, extended_end

    gen = build_gen(key)
    clean: list[str] = []
    frames: list[pd.DataFrame] = []
    if u["mode"] == "per_window":
        for _, w in sampled_windows.iterrows():
            fr, clean = gen._load_data(w["train_start"], extended_end(w["predict_end"]))
            frames.append(gen._apply_pit_membership(fr))
    else:
        fr, clean, _ = load_materialized_frame(key)
        frames = [fr] * len(sampled_windows)
    fs = frame_stats(frames, clean, sampled_windows, label_df)
    print(f"  frame: {fs['n_materialized_rows']} rows; missing_feature_ratio="
          f"{fs['missing_feature_ratio']:.4%}; missing_label_ratio="
          f"{fs['missing_label_ratio']:.4%}", flush=True)

    row = {
        "universe": key,
        "label": u["label"],
        "registry_instruments": n_unique,
        "registry_spans": len(registry),
        "pit_daily_size_avg": round(float(sizes.mean()), 1),
        "pit_daily_size_min": int(sizes.min()),
        "pit_daily_size_max": int(sizes.max()),
        **cc,
        "n_materialized_rows": fs["n_materialized_rows"],
        "missing_feature_ratio": round(fs["missing_feature_ratio"], 6),
        "worst_features": fs["worst_features"],
        "n_train_rows_all_windows": fs["n_train_rows_all_windows"],
        "n_train_rows_missing_label": fs["n_train_rows_missing_label"],
        "missing_label_ratio": round(fs["missing_label_ratio"], 6),
    }
    # also record materialized frame path / cache key lineage
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--universes", default="U0,U2",
                    help="comma-separated universe keys to cover (default U0,U2)")
    args = ap.parse_args()

    keys = [k.strip() for k in args.universes.split(",") if k.strip()]
    sampled = sample_windows()
    print(f"sampled windows: {len(sampled)} "
          f"({sampled['predict_start'].min()} -> {sampled['predict_end'].max()})", flush=True)
    cal_start = sampled["predict_start"].min()
    cal_end = sampled["predict_end"].max()
    calendar = get_trading_calendar(cal_start, cal_end)

    rows = [coverage_for(k, sampled, calendar) for k in keys]
    df = pd.DataFrame(rows)
    out = OUT_DIR / "coverage_report.csv"
    df.to_csv(out, index=False)
    print(f"\nwrote {out}")
    print(df[["universe", "registry_instruments", "pit_daily_size_avg",
              "canonical_test_range_ratio", "missing_feature_ratio",
              "missing_label_ratio"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
