"""Alpha V1 — signal pre-computation for the generic backtest engine."""
from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.signal.alpha_v1.labels import cs_zscore, make_zs_label
from qsys.signal.alpha_v1.training import predict_model, train_model


def precompute_alpha_v1_signals(
    frame: pd.DataFrame,
    windows: list[dict],
    clean_features: list[str],
    config,
) -> tuple[dict[tuple[str, str], float], list[dict], list[dict]]:
    """Pre-compute blended (0.8*z5 + 0.2*z20) scores for all test dates.

    Trains 5d/20d models per window, predicts, blends, and caches every
    score into a lookup dict consumed by ``BacktestEngine``.

    Returns
    -------
    signal_lookup : dict[(date_str, instrument), blended_score]
    prediction_rows : list[dict] — per-row predictions for artifact export
    signal_rows : list[dict] — per-window IC / RankIC stats
    """
    all_test_dates = set()
    for w in windows:
        for d in pd.date_range(w["test_start"], w["test_end"], freq="D"):
            all_test_dates.add(d.strftime("%Y-%m-%d"))

    all_dates = sorted(
        d
        for d in frame["trade_date"].unique()
        if d.strftime("%Y-%m-%d") in all_test_dates
        or any(
            w["test_start"] <= d.strftime("%Y-%m-%d") <= w["test_end"] for w in windows
        )
    )
    all_dates_str = set(
        d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10]
        for d in all_dates
    )
    valid_windows = [w for w in windows if w["test_start"] in all_dates_str]
    retrain_schedule = {w["test_start"]: w for w in valid_windows}

    signal_lookup: dict[tuple[str, str], float] = {}
    prediction_rows: list[dict] = []
    signal_rows: list[dict] = []
    n_retrains = 0

    for w in valid_windows:
        train_mask = (frame["trade_date"] >= w["train_start"]) & (
            frame["trade_date"] <= w["train_end"]
        )
        train = frame[train_mask].copy()
        if len(train) < 1000:
            continue

        models_ok = True
        models = {}
        for tag, h in [("5d", 5), ("20d", 20)]:
            y_train = make_zs_label(h)(train)
            X_tr = train[clean_features].astype(np.float32).fillna(0.0)
            y_tr = y_train[pd.notna(y_train)]
            valid_rows = y_tr.index
            X_tr_valid = X_tr.loc[valid_rows]
            if len(X_tr_valid) < 500:
                models_ok = False
                break
            try:
                models[tag] = train_model(
                    X_tr_valid,
                    y_tr,
                    f"{w['window_id']}_{tag}",
                    n_estimators=config.training.n_estimators,
                    lgb_params=config.training.lgb_params,
                )
            except Exception as e:
                print(f"    [{w['window_id']}] ERROR {tag}: {e}")
                models_ok = False
                break

        if not models_ok:
            continue

        test_mask = (frame["trade_date"] >= w["test_start"]) & (
            frame["trade_date"] <= w["test_end"]
        )
        test_data = frame[test_mask].copy()
        if len(test_data) == 0:
            continue

        X_test = test_data[clean_features].astype(np.float32).fillna(0.0)
        for tag in ["5d", "20d"]:
            test_data[f"pred_{tag}"] = predict_model(*models[tag], X_test).values

        for d in test_data["trade_date"].unique():
            dm = test_data["trade_date"] == d
            sub = test_data[dm]
            z5 = cs_zscore(pd.Series(sub["pred_5d"].values))
            z20 = cs_zscore(pd.Series(sub["pred_20d"].values))
            test_data.loc[dm, "blended_score"] = (
                config.blend.blend_5d * z5.values + config.blend.blend_20d * z20.values
            )

        keep_cols = ["trade_date", "instrument", "pred_5d", "pred_20d", "blended_score"]
        preds_df = test_data[keep_cols].dropna(subset=["blended_score"])
        for _, row in preds_df.iterrows():
            td = row["trade_date"]
            date_key = (
                td.strftime("%Y-%m-%d") if hasattr(td, "strftime") else str(td)[:10]
            )
            key = (date_key, row["instrument"])
            score = float(row["blended_score"]) if pd.notna(row["blended_score"]) else 0.0
            signal_lookup[key] = score
            prediction_rows.append(
                {
                    "trade_date": date_key,
                    "instrument": row["instrument"],
                    "pred_5d": float(row["pred_5d"]) if pd.notna(row["pred_5d"]) else 0.0,
                    "pred_20d": float(row["pred_20d"]) if pd.notna(row["pred_20d"]) else 0.0,
                    "blended_score": score,
                }
            )

        # Per-window IC / RankIC
        window_ics = []
        for ic_date in test_data["trade_date"].unique():
            ic_mask = test_data["trade_date"] == ic_date
            ic_sub = test_data[ic_mask].dropna(subset=["blended_score", "fwd_5d"])
            if len(ic_sub) >= 10:
                ic_v = float(ic_sub["blended_score"].corr(ic_sub["fwd_5d"]))
                ric_v = float(
                    ic_sub["blended_score"].corr(ic_sub["fwd_5d"], method="spearman")
                )
                window_ics.append({"IC": ic_v, "RankIC": ric_v})
        if window_ics:
            signal_rows.append(
                {
                    "window_id": w["window_id"],
                    "IC_mean": float(np.mean([x["IC"] for x in window_ics])),
                    "RankIC_mean": float(np.mean([x["RankIC"] for x in window_ics])),
                    "IC_std": float(np.std([x["IC"] for x in window_ics])),
                    "RankIC_std": float(np.std([x["RankIC"] for x in window_ics])),
                }
            )

        n_retrains += 1
        print(f"  [{w['window_id']}] signals ready ({w['test_start']}~{w['test_end']})")

    print(f"  Models retrained: {n_retrains}x, total predictions: {len(prediction_rows)}")
    return signal_lookup, prediction_rows, signal_rows
