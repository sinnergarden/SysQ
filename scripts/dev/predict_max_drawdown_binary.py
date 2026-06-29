#!/usr/bin/env python3
"""P0: train max_drawdown binary classifier + predict 2026-06-29.

Usage:
    # Compute label first (one-time)
    python scripts/dev/predict_max_drawdown_binary.py --compute-label

    # Train on latest~504d data + predict 2026-06-29 (raw only, no calibration)
    python scripts/dev/predict_max_drawdown_binary.py --trade-date 2026-06-29 --top-k 800

    # Full calibration pipeline: LGBM-train / calib / test + calibrated output
    python scripts/dev/predict_max_drawdown_binary.py --trade-date 2026-06-29 --calibrate
"""
from __future__ import annotations

import argparse, json, os, sys, numpy as np, pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.data.adapter import QlibAdapter
from qsys.feature.registry import FeatureListRegistry
from qsys.label.compute import compute_future_max_drawdown, compute_binary_max_drawdown
from qsys.label.store import LabelStore
from qsys.signal.alpha_v1.training import train_model, predict_model
from qsys.signal.alpha_v1.calibrate import (
    ProbabilityCalibrator, compute_risk_percentile, _compute_metrics,
    _calibration_by_decile,
)


FEATURE_LIST = "alpha_v1_clean_132"
LABEL_BINARY = "fwd_maxdd_5d_binary_5pct"
UNIVERSE = "csi800"
HORIZON = 5  # label window in trading days
MODEL_ROOT = Path("data/research/models/stop_loss_binary_5d")


def compute_label(overwrite: bool = False) -> None:
    """Compute and save binary max-drawdown label."""
    print("Computing binary max-drawdown label (horizon=5d, threshold=-5%)...")
    df = compute_binary_max_drawdown(
        universe=UNIVERSE, horizon=5, start="2018-01-01", end="2026-07-15",
        threshold=-0.05,
    )
    store = LabelStore()
    path = store.save_labels(LABEL_BINARY, df, overwrite=overwrite,
                             manifest={"horizon": 5, "threshold": -0.05, "universe": UNIVERSE})
    print(f"Label saved: {path}  ({len(df)} rows)")
    pos = (df["label_value"] == 1).sum()
    neg = (df["label_value"] == 0).sum()
    print(f"  pos={pos} ({100*pos/len(df):.1f}%), neg={neg} ({100*neg/len(df):.1f}%)")


def _resolve_calendar(trade_date: str) -> tuple[list[str], int]:
    """Get trading calendar up to trade_date.

    Returns (cal, td_idx) where td_idx is the index of trade_date in cal.
    """
    from qlib.data import D
    QlibAdapter().init_qlib()
    cal = [str(c)[:10] for c in D.calendar(end_time="2026-12-31", freq="day")]
    cal = [d for d in cal if d <= trade_date]
    if trade_date not in cal:
        raise ValueError(f"{trade_date} not in trading calendar")
    td_idx = cal.index(trade_date)
    return cal, td_idx


def train(trade_date: str, train_window_days: int = 504) -> tuple:
    """Train binary classifier using trailing N trading days.

    Training cutoff respects label maturity: the last training date is
    ``trade_date - HORIZON`` trading days, ensuring all training labels
    are observable (forward window complete) as of trade_date.

    Returns (model, center, scale, clean_features).
    """
    QlibAdapter().init_qlib()
    features = FeatureListRegistry.load(FEATURE_LIST)
    print(f"Features: {len(features)}")

    cal, td_idx = _resolve_calendar(trade_date)
    if td_idx < HORIZON:
        raise ValueError(
            f"trade_date {trade_date} at position {td_idx} in calendar, "
            f"need at least {HORIZON} prior trading days"
        )
    train_end = cal[td_idx - HORIZON]
    train_start_idx = max(0, td_idx - HORIZON - train_window_days)
    train_start = cal[train_start_idx]
    print(f"Label window: {HORIZON}d | Training: [{train_start}, {train_end}] "
          f"({len(cal[train_start_idx:td_idx - HORIZON + 1])} days) "
          f"| Predict date: {trade_date}")

    raw = QlibAdapter().get_features(UNIVERSE, features + ["$close"],
                                     start_time=train_start, end_time=trade_date)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["ts_code"] = frame["instrument"]
    frame = frame.sort_values("ts_code").reset_index(drop=True)

    label_df = LabelStore().load_labels(LABEL_BINARY)
    train_df = frame[frame["trade_date"].between(train_start, train_end)].copy().merge(
        label_df[["trade_date", "instrument", "label_value"]],
        on=["trade_date", "instrument"], how="left",
    )
    y_valid = train_df["label_value"].notna()
    X_tr = train_df[features].fillna(0.0).astype(np.float32)
    y_tr = train_df.loc[y_valid, "label_value"].astype(int)
    print(f"Train samples: {len(y_tr)}  pos={y_tr.sum()}, neg={(1-y_tr).sum()}")

    if y_tr.empty or y_tr.nunique() < 2:
        raise ValueError("Training data insufficient for binary classification")

    model, center, scale = train_model(
        X_tr.loc[y_tr.index], y_tr, "binary_maxdd",
        n_estimators=300, mode="binary",
    )
    return model, center, scale, features


def predict(trade_date: str, model, center, scale, features, top_k: int = 800):
    """Predict on trade_date for the full universe."""
    QlibAdapter().init_qlib()
    raw = QlibAdapter().get_features(UNIVERSE, features + ["$close"],
                                     start_time=trade_date, end_time=trade_date)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["ts_code"] = frame["instrument"]
    frame = frame.sort_values("ts_code").reset_index(drop=True)
    print(f"Predict: {len(frame)} stocks")

    prob = predict_model(model, center, scale,
                         frame[features].fillna(0.0).astype(np.float32),
                         mode="binary").values

    result = pd.DataFrame({
        "ts_code": frame["ts_code"],
        "name": "", "industry": "",
        "downside_prob": prob,
    })
    result = result.sort_values("downside_prob", ascending=False).reset_index(drop=True)
    result["rank"] = result.index + 1

    tb = pd.read_parquet("data/tushare/stock_basic.parquet")
    tb["ck"] = tb["ts_code"].str.replace(".", "", regex=False)
    nm = dict(zip(tb["ck"], tb["name"]))
    for code in result["ts_code"]:
        ck = code.replace(".", "")
        result.loc[result["ts_code"] == code, "name"] = nm.get(ck, "")

    _save_raw_json(trade_date, result, top_k)
    return result


def _save_raw_json(trade_date: str, result: pd.DataFrame, top_k: int = 800) -> Path:
    """Save raw stop_loss_prob.json (backward-compatible)."""
    out_dir = Path("outputs") / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "stop_loss_prob.json"
    records = []
    for _, r in result.iterrows():
        records.append({
            "rank": int(r["rank"]),
            "ts_code": r["ts_code"],
            "name": r.get("name", ""),
            "industry": r.get("industry", ""),
            "downside_prob": round(float(r["downside_prob"]), 6),
        })
    payload = {
        "trade_date": trade_date,
        "model": "stop_loss_binary_5d",
        "label": LABEL_BINARY,
        "feature_list": FEATURE_LIST,
        "universe": UNIVERSE,
        "total": len(records),
        "predictions": records[:top_k],
    }
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"-> {out_path}")
    return out_path


# ═══════════════════════════════════════════════════════════════════
# Calibration pipeline
# ═══════════════════════════════════════════════════════════════════


def run_calibrated(
    trade_date: str,
    calib_method: str = "isotonic",
    train_window_days: int = 504,
    calib_ratio: float = 0.15,
) -> None:
    """Full calibration pipeline: LGBM-train / calib / test split.

    1. Reserve latest N_test + N_calib days for evaluation.
    2. Train LGBM on earliest portion.
    3. Predict on calib set → fit calibrator.
    4. Predict on test set → evaluate raw vs calibrated.
    5. Predict on trade_date → apply calibrator → output.
    """
    features = FeatureListRegistry.load(FEATURE_LIST)
    cal, td_idx = _resolve_calendar(trade_date)
    if td_idx < HORIZON:
        raise ValueError(f"Not enough calendar before {trade_date}")
    train_end_idx = td_idx - HORIZON

    # Holdout split: use the last train_window_days mature dates,
    # then reserve the final n_calib days as holdout for calibrator.
    window = min(train_window_days, train_end_idx + 1)
    window_start_idx = train_end_idx - window + 1
    window_end_idx = train_end_idx

    n_calib = max(1, int(window * calib_ratio))
    calib_start_idx = window_end_idx - n_calib + 1
    calib_end_idx = window_end_idx

    lgbm_start_idx = window_start_idx
    lgbm_end_idx = calib_start_idx - 1
    if lgbm_end_idx < lgbm_start_idx:
        raise ValueError(
            f"Not enough mature history for LGBM train + calibration holdout "
            f"(window={window}, n_calib={n_calib})"
        )

    lgbm_start = cal[lgbm_start_idx]
    lgbm_end = cal[lgbm_end_idx]
    calib_start = cal[calib_start_idx]
    calib_end = cal[calib_end_idx]

    n_lgbm = lgbm_end_idx - lgbm_start_idx + 1
    n_holdout = calib_end_idx - calib_start_idx + 1

    print(f"\n{'='*70}")
    print(f"Calibration pipeline for {trade_date}")
    print(f"{'='*70}")
    print(f"  LGBM train:           [{lgbm_start}, {lgbm_end}]  ({n_lgbm}d)")
    print(f"  Calibration holdout:  [{calib_start}, {calib_end}]  ({n_holdout}d)")
    print(f"  Predict:              {trade_date}")
    print(f"  (Label horizon: {HORIZON}d, labels mature up to {cal[train_end_idx]})")

    # Load feature data for the (window + predict) range only
    QlibAdapter().init_qlib()
    raw = QlibAdapter().get_features(UNIVERSE, features + ["$close"],
                                     start_time=lgbm_start, end_time=trade_date)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["ts_code"] = frame["instrument"]
    frame = frame.sort_values("ts_code").reset_index(drop=True)

    # Load labels
    label_df = LabelStore().load_labels(LABEL_BINARY)

    # ── 1. Train LGBM ──
    def _prepare_train(date_start, date_end):
        sub = frame[frame["trade_date"].between(date_start, date_end)].copy().merge(
            label_df[["trade_date", "instrument", "label_value"]],
            on=["trade_date", "instrument"], how="left",
        )
        y_valid = sub["label_value"].notna()
        X_tr = sub[features].fillna(0.0).astype(np.float32)
        y_tr = sub.loc[y_valid, "label_value"].astype(int)
        return X_tr.loc[y_tr.index], y_tr, X_tr.columns.tolist()

    X_tr, y_tr, _ = _prepare_train(lgbm_start, lgbm_end)
    pos = (y_tr == 1).sum()
    neg = (y_tr == 0).sum()
    print(f"\n  LGBM training: {len(y_tr)} samples "
          f"(pos={pos}/{100*pos/len(y_tr):.1f}%)")
    model, center, scale = train_model(
        X_tr, y_tr, "calib_lgbm", n_estimators=300, mode="binary",
    )

    # ── 2. Predict on calib set for calibrator fitting ──
    def _get_raw_preds_with_label(date_start, date_end):
        sub = frame[frame["trade_date"].between(date_start, date_end)].copy()
        if sub.empty:
            return None, None, None
        from qsys.signal.alpha_v1.labels import robust_zscore_transform
        Xp = sub[features].fillna(0.0).astype(np.float32)
        Xz = robust_zscore_transform(Xp, center, scale)
        sub["raw_prob"] = model.predict(Xz.values)
        merged = sub.merge(
            label_df[["trade_date", "instrument", "label_value"]],
            on=["trade_date", "instrument"], how="inner",
        )
        if merged.empty:
            return None, None, None
        return (
            merged["raw_prob"].to_numpy(dtype=float),
            merged["label_value"].to_numpy(dtype=float),
            merged["instrument"].to_numpy(),
        )

    calib_prob, calib_y, _ = _get_raw_preds_with_label(calib_start, calib_end)

    # ── 3. Fit calibrator — isotonic on raw_prob ──
    calibrator = ProbabilityCalibrator(method=calib_method, use_margin=False)
    calibrator.fit(calib_prob, calib_y)

    # ── 4. Evaluate calibration quality on calib set itself ──
    cal_calib_prob = calibrator.predict(calib_prob)
    raw_metrics = _compute_metrics(calib_y, calib_prob)
    cal_metrics = _compute_metrics(calib_y, cal_calib_prob)
    calib_valid = calib_y[~np.isnan(calib_y)]
    true_bad_rate = float(calib_valid.mean()) if len(calib_valid) > 0 else 0.0

    print(f"\n{'─'*60}")
    print(f"  CALIBRATION REPORT (holdout calibration set)")
    print(f"{'─'*60}")
    print(f"  {'Metric':<20s} {'Raw':>10s} {'Calibrated':>12s}")
    print(f"  {'─'*42}")
    for m in ["auc", "pr_auc", "logloss", "brier", "prob_mean"]:
        raw_v = raw_metrics.get(m, None)
        cal_v = cal_metrics.get(m, None)
        print(f"  {m:<20s} {str(round(raw_v,5) if raw_v else 'N/A'):>10s} "
              f"{str(round(cal_v,5) if cal_v else 'N/A'):>12s}")
    print(f"  {'':<20s} {'':>10s} {'':>12s}")
    print(f"  true_bad_rate: {true_bad_rate:.4%}")

    decile = _calibration_by_decile(calib_y, cal_calib_prob)
    print(f"\n  Calibration by decile (calibrated):")
    print(f"  {'Decile':>7s} {'n':>5s} {'Pred_mean':>10s} {'True_rate':>10s}")
    for d in decile:
        print(f"  {d['decile']:7d} {d['n']:5d} {d['pred_prob_mean']:10.4f} {d['true_bad_rate']:10.4f}")

    # ── 5. Predict on trade_date ──
    print(f"\n  Predicting {trade_date}...")
    QlibAdapter().init_qlib()
    raw_pred = QlibAdapter().get_features(UNIVERSE, features + ["$close"],
                                          start_time=trade_date, end_time=trade_date)
    pred_frame = raw_pred.reset_index().rename(columns={"datetime": "trade_date"})
    pred_frame = pred_frame.loc[:, ~pred_frame.columns.duplicated()]
    pred_frame["trade_date"] = pred_frame["trade_date"].astype(str).str[:10]
    pred_frame["ts_code"] = pred_frame["instrument"]
    pred_frame = pred_frame.sort_values("ts_code").reset_index(drop=True)

    from qsys.signal.alpha_v1.labels import robust_zscore_transform
    Xp = pred_frame[features].fillna(0.0).astype(np.float32)
    Xz = robust_zscore_transform(Xp, center, scale)
    raw_prob = model.predict(Xz.values)
    calibrated_prob = calibrator.predict(raw_prob)
    risk_pct = compute_risk_percentile(calibrated_prob)

    # ── Compute alpha scores for eval (60d+180d ranking) ──
    try:
        from qsys.feature.registry import FeatureListRegistry as _FLR
        from qsys.signal.alpha_v1.labels import robust_zscore_transform as _rzt
        from scripts.dev.financial_rc.adapter import _eligible_model as _em
        _fc_feats = _FLR.load("v3a_plus_liquidity_financial_rc")
        _fc_raw = QlibAdapter().get_features(UNIVERSE, _fc_feats, start_time=trade_date, end_time=trade_date)
        _fc_frame = _fc_raw.reset_index().rename(columns={"datetime": "trade_date"})
        _fc_frame = _fc_frame.loc[:, ~_fc_frame.columns.duplicated()]
        _fc_frame["ts_code"] = _fc_frame["instrument"]
        _fc_frame = _fc_frame.sort_values("ts_code").reset_index(drop=True)
        import lightgbm as _lgb
        _mds = {
            "60d": _em("60d_v3a_growth_financial", "fwd_ret_60d_raw", 60, trade_date, "v3a_plus_liquidity_financial_rc", __import__("pathlib").Path("data/research/models")),
            "180d": _em("180d_v3a_growth_financial", "fwd_ret_180d_raw", 180, trade_date, "v3a_plus_liquidity_financial_rc", __import__("pathlib").Path("data/research/models")),
        }
        _scores = {}
        for _tag in ["60d", "180d"]:
            _md = _mds[_tag]
            _m = _lgb.Booster(model_file=str(_md / "model.txt"))
            _c = pd.read_json(_md / "center.json", typ="series")
            _s = pd.read_json(_md / "scale.json", typ="series")
            _xz = _rzt(_fc_frame[_fc_feats].fillna(0).astype(np.float32), _c, _s)
            _p = _m.predict(_xz.values)
            _pz = (_p - _p.mean()) / max(_p.std(), 1e-8)
            _scores[_tag] = pd.Series(_pz, index=_fc_frame["ts_code"])
        _ranking = 0.3 * _scores["60d"] + 0.7 * _scores["180d"]
        _ranking_z = (_ranking - _ranking.mean()) / max(_ranking.std(), 1e-8)
        alpha_scores = _ranking_z.to_dict()
    except Exception:
        alpha_scores = None

    # Build output
    pred_codes = pred_frame["ts_code"].values
    tb = pd.read_parquet("data/tushare/stock_basic.parquet")
    tb["ck"] = tb["ts_code"].str.replace(".", "", regex=False)
    nm = dict(zip(tb["ck"], tb["name"]))
    ind = dict(zip(tb["ck"], tb["industry"]))

    raw_rank = np.argsort(np.argsort(raw_prob))[::-1] + 1  # 1-based rank descending

    records = []
    for i in range(len(pred_codes)):
        ck = pred_codes[i].replace(".", "")
        records.append({
            "rank": int(raw_rank[i]),
            "ts_code": pred_codes[i],
            "name": nm.get(ck, ""),
            "industry": ind.get(ck, ""),
            "downside_prob": round(float(raw_prob[i]), 6),
            "calibrated_prob": round(float(calibrated_prob[i]), 6),
            "downside_risk_pct": round(float(risk_pct[i]), 2),
        })
    records.sort(key=lambda r: r["rank"])

    # ── 6. Save outputs ──
    out_dir = Path("outputs") / trade_date
    out_dir.mkdir(parents=True, exist_ok=True)

    # a) Update stop_loss_prob.json with calibrated fields added
    payload = {
        "trade_date": trade_date,
        "model": "stop_loss_binary_5d",
        "label": LABEL_BINARY,
        "feature_list": FEATURE_LIST,
        "universe": UNIVERSE,
        "total": len(records),
        "calibration_info": {
            "method": calib_method,
            "use_margin": False,
            "split": "holdout",
            "lgbm_train_start": str(lgbm_start),
            "lgbm_train_end": str(lgbm_end),
            "calib_start": str(calib_start),
            "calib_end": str(calib_end),
            "label_horizon": HORIZON,
            "true_bad_rate": round(float(true_bad_rate), 6),
            "role": "risk_sidecar",
            "recommended_usage": "Use calibrated_prob/risk_pct as risk bucket and sizing aid, not as a standalone hard buy/sell threshold.",
            "eval_note": "AUC is not the primary production metric; inspect risk decile lift and alpha-conditional risk impact.",
            "note": "Calibrator fitted on mature historical holdout not used to train the LightGBM model. calibrated_prob is for interpretability/risk bucket, not a production hard threshold without holdout validation.",
        },
        "calibration_report": {
            "raw": {k: round(float(v), 6) if v is not None else None for k, v in raw_metrics.items()},
            "calibrated": {k: round(float(v), 6) if v is not None else None for k, v in cal_metrics.items()},
            "decile": decile,
        },
        "predictions": records,
    }
    cal_path = out_dir / "stop_loss_prob_calibrated.json"
    cal_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n-> {cal_path}")

    # b) Also update the original file with calibrated fields (backward compat)
    simple_records = []
    for r in records:
        simple_records.append({
            "rank": r["rank"],
            "ts_code": r["ts_code"],
            "name": r["name"],
            "industry": r["industry"],
            "downside_prob": r["downside_prob"],
            "calibrated_prob": r["calibrated_prob"],
            "downside_risk_pct": r["downside_risk_pct"],
        })
    orig_payload = {
        "trade_date": trade_date,
        "model": "stop_loss_binary_5d",
        "label": LABEL_BINARY,
        "feature_list": FEATURE_LIST,
        "universe": UNIVERSE,
        "total": len(simple_records),
        "predictions": simple_records,
    }
    orig_path = out_dir / "stop_loss_prob.json"
    orig_path.write_text(json.dumps(orig_payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"-> {orig_path} (updated with calibrated_prob + downside_risk_pct)")

    # ── 7. Build and save stop_loss_eval.json ──
    eval_data = _build_stop_loss_eval(
        trade_date, records, calib_y, calib_prob, cal_calib_prob,
        raw_metrics, cal_metrics, decile, true_bad_rate,
        calib_start, calib_end, alpha_scores,
    )
    eval_path = out_dir / "stop_loss_eval.json"
    eval_path.write_text(json.dumps(eval_data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"-> {eval_path}")

    # ── 8. Console summary ──
    print(f"\n{'─'*60}")
    print(f"  STOP-LOSS RISK SIDECAR SUMMARY")
    print(f"{'─'*60}")
    cm = eval_data.get("classification_metrics", {})
    print(f"  AUC={cm.get('auc','?'):.4f}  PR-AUC={cm.get('pr_auc','?'):.4f}  "
          f"Brier={cm.get('brier','?'):.4f}  bad_rate={cm.get('overall_bad_rate','?'):.4%}")
    rd = eval_data.get("risk_deciles", [])
    if len(rd) >= 10:
        top_d = rd[-1]; bot_d = rd[0]
        print(f"  Top risk decile:   true_bad_rate={top_d.get('true_bad_rate',0):.4%}  "
              f"lift={top_d.get('lift_vs_overall','?'):.2f}x")
        print(f"  Bottom risk decile: true_bad_rate={bot_d.get('true_bad_rate',0):.4%}  "
              f"lift={bot_d.get('lift_vs_overall','?'):.2f}x")
    atr = eval_data.get("alpha_topk_risk_eval", [])
    if isinstance(atr, list) and len(atr) > 0:
        for entry in atr:
            tk = entry.get("top_k", "?")
            hi = len(entry.get("high_risk_names", []))
            lo = len(entry.get("low_risk_names", []))
            avg_risk = entry.get("avg_calibrated_prob", 0)
            print(f"  Alpha Top{tk:<3d}: avg_risk={avg_risk:.4%}  high_risk={hi}  low_risk={lo}")
    print(f"  {'─'*60}")
    print(f"  P0 stop-loss classifier provides a short-horizon downside risk sidecar.")
    print(f"  AUC is moderate; production usefulness should be judged by risk-decile")
    print(f"  lift and alpha-conditional portfolio impact.")


def _build_risk_deciles(
    y_true: np.ndarray, raw_prob: np.ndarray, cal_prob: np.ndarray,
    overall_bad_rate: float,
) -> list[dict]:
    """Build risk deciles from holdout predictions, including raw prob mean."""
    df = pd.DataFrame({"y_true": y_true, "raw_prob": raw_prob, "cal_prob": cal_prob}).dropna()
    if df.empty:
        return []
    df["decile"] = pd.qcut(df["cal_prob"].rank(method="first"), q=10, labels=False, duplicates="drop") + 1
    rows = []
    for dec in sorted(df["decile"].unique()):
        sub = df[df["decile"] == dec]
        tbr = float(sub["y_true"].mean())
        lift = round(tbr / overall_bad_rate, 4) if overall_bad_rate > 0 else None
        risk_level = "high" if dec >= 9 else ("mid" if dec >= 7 else "low")
        rows.append({
            "decile": int(dec),
            "risk_level": risk_level,
            "n": int(len(sub)),
            "avg_raw_prob": round(float(sub["raw_prob"].mean()), 6),
            "avg_calibrated_prob": round(float(sub["cal_prob"].mean()), 6),
            "true_bad_rate": round(tbr, 6),
            "lift_vs_overall": lift,
            "avg_label_value": round(tbr, 6),
        })
    return sorted(rows, key=lambda x: x["decile"])


def _build_stop_loss_eval(
    trade_date: str, records: list, calib_y, calib_prob, cal_calib_prob,
    raw_metrics: dict, cal_metrics: dict, decile: list, true_bad_rate: float,
    calib_start, calib_end, alpha_scores: dict | None = None,
) -> dict:
    """Build stop_loss_eval.json: risk deciles, alpha merge, conditional eval."""
    import json
    from pathlib import Path

    out_dir = Path("outputs") / trade_date
    eval_data: dict = {}

    # ── 1. Classification metrics ──
    eval_data["classification_metrics"] = {
        "auc": cal_metrics.get("auc"),
        "pr_auc": cal_metrics.get("pr_auc"),
        "logloss": cal_metrics.get("logloss"),
        "brier": cal_metrics.get("brier"),
        "overall_bad_rate": round(float(true_bad_rate), 6),
        "n_samples": int(len(calib_y)) if calib_y is not None else 0,
        "eval_window_start": str(calib_start),
        "eval_window_end": str(calib_end),
        "note": "Metrics are evaluated on mature historical holdout, not future live OOS.",
    }

    # ── 2. Risk deciles (built from holdout vecs, contains avg_raw_prob) ──
    risk_deciles = _build_risk_deciles(calib_y, calib_prob, cal_calib_prob, float(true_bad_rate))
    eval_data["risk_deciles"] = risk_deciles

    # ── 3. Alpha TopK risk eval — prefer candidates.json, fallback alpha_scores ──
    cand_path = out_dir / "candidates.json"
    merged_codes: list[tuple[str, float]] = []

    if cand_path.exists():
        try:
            with open(cand_path) as f:
                cand = json.load(f)
            can_scores = {}
            for c in cand.get("candidates", []):
                code = c["ts_code"]
                score = (c.get("ranking_score") or c.get("score")
                         or c.get("alpha_score") or c.get("combined_score")
                         or c.get("60d_180d_score") or 0)
                can_scores[code] = float(score)
            if len(can_scores) >= 15:
                merged_codes = sorted(can_scores.items(), key=lambda x: x[1], reverse=True)
        except Exception:
            pass

    if not merged_codes and alpha_scores:
        merged_codes = sorted(alpha_scores.items(), key=lambda x: x[1], reverse=True)

    if merged_codes:
        rec_map = {r["ts_code"]: r for r in records}
        alpha_topk_entries = []
        for top_k in [20, 50, 100, 200]:
            top = merged_codes[:min(top_k, len(merged_codes))]
            matched = []
            for code, asc in top:
                r = rec_map.get(code)
                if r:
                    matched.append({
                        "ts_code": code,
                        "name": r.get("name", ""),
                        "alpha_score": asc,
                        "calibrated_prob": r["calibrated_prob"],
                        "risk_pct": r["downside_risk_pct"],
                    })
            if not matched:
                continue
            cal_probs = [m["calibrated_prob"] for m in matched]
            high_risk = [m for m in matched if m["calibrated_prob"] >= 0.40]
            low_risk = [m for m in matched if m["calibrated_prob"] <= 0.15]
            entry = {
                "top_k": top_k,
                "n": len(matched),
                "avg_alpha_score": round(float(np.mean([m["alpha_score"] for m in matched])), 6),
                "avg_calibrated_prob": round(float(np.mean(cal_probs)), 6),
                "risk_count_ge_80": sum(1 for m in matched if m["risk_pct"] >= 80),
                "risk_count_ge_90": sum(1 for m in matched if m["risk_pct"] >= 90),
                "risk_count_ge_95": sum(1 for m in matched if m["risk_pct"] >= 95),
                "high_risk_names": sorted(high_risk, key=lambda x: x["calibrated_prob"], reverse=True)[:10],
                "low_risk_names": sorted(low_risk, key=lambda x: x["calibrated_prob"])[:10],
            }
            alpha_topk_entries.append(entry)
        eval_data["alpha_topk_risk_eval"] = alpha_topk_entries
    else:
        eval_data["alpha_topk_risk_eval"] = {
            "status": "skipped",
            "reason": "neither candidates.json nor alpha_scores available",
        }

    # ── 4. Alpha-conditional eval ──
    eval_data["alpha_conditional_eval"] = {
        "status": "skipped",
        "reason": "historical alpha scores not available in this script",
    }

    return eval_data


def main():
    parser = argparse.ArgumentParser(description="Stop-loss binary classifier — P0")
    parser.add_argument("--compute-label", action="store_true", help="Compute label first")
    parser.add_argument("--trade-date", default="2026-06-29", help="Prediction trade date")
    parser.add_argument("--top-k", type=int, default=800, help="Top K to output")
    parser.add_argument("--train-window", type=int, default=504,
                        help="Training window in trading days")
    parser.add_argument("--calibrate", action="store_true",
                        help="Run calibration pipeline (LGBM/calib/test split)")
    parser.add_argument("--calib-method", default="sigmoid",
                        choices=["sigmoid", "isotonic"],
                        help="Calibration method")
    args = parser.parse_args()

    if args.compute_label:
        compute_label(overwrite=True)
        return

    if not LabelStore().label_exists(LABEL_BINARY):
        print(f"Label {LABEL_BINARY} not found, computing first...")
        compute_label()

    if args.calibrate:
        run_calibrated(
            args.trade_date,
            calib_method=args.calib_method,
            train_window_days=args.train_window,
        )
    else:
        model, center, scale, features = train(args.trade_date, args.train_window)
        predict(args.trade_date, model, center, scale, features, args.top_k)


if __name__ == "__main__":
    main()
