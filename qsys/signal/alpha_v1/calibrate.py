"""Probability calibration for binary LightGBM classifiers.

Splits a training window chronologically into LGBM-train / calib / test.
Fits a calibrator (Platt sigmoid or isotonic) on calib predictions.
Evaluates calibration quality on test predictions.

Outputs both raw and calibrated probabilities plus cross-sectional risk
percentile.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import numpy as np
import pandas as pd

from qsys.signal.alpha_v1.training import train_model, predict_model


@dataclass
class CalibrationEval:
    """Full calibration evaluation result."""

    # Pre-calibration
    raw_auc: float | None = None
    raw_pr_auc: float | None = None
    raw_logloss: float | None = None
    raw_brier: float | None = None
    raw_prob_mean: float | None = None

    # Post-calibration
    cal_auc: float | None = None
    cal_pr_auc: float | None = None
    cal_logloss: float | None = None
    cal_brier: float | None = None
    cal_prob_mean: float | None = None

    # Ground truth
    true_bad_rate: float | None = None

    # Decile bins
    calibration_by_decile: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


def _compute_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict[str, float | None]:
    """Compute AUC, PR-AUC, logloss, brier, prob_mean.

    Filters out rows where y_true is NaN (e.g. from unobservable labels).
    """
    from sklearn.metrics import (
        roc_auc_score, average_precision_score,
        log_loss, brier_score_loss,
    )
    result: dict[str, float | None] = {
        "auc": None, "pr_auc": None, "logloss": None,
        "brier": None, "prob_mean": None,
    }
    if len(y_true) == 0:
        return result

    # Drop NaN labels
    valid = ~np.isnan(y_true)
    yt = y_true[valid]
    yp = y_prob[valid]
    if len(yt) < 10:
        return result

    result["prob_mean"] = float(yp.mean())
    try:
        result["auc"] = float(roc_auc_score(yt, yp))
    except Exception:
        pass
    try:
        result["pr_auc"] = float(average_precision_score(yt, yp))
    except Exception:
        pass
    try:
        result["logloss"] = float(log_loss(yt, yp))
    except Exception:
        pass
    try:
        result["brier"] = float(brier_score_loss(yt, yp))
    except Exception:
        pass
    return result


def _calibration_by_decile(
    y_true: np.ndarray, y_prob: np.ndarray,
) -> list[dict[str, Any]]:
    """Bin predictions into 10 deciles by predicted probability.

    Returns one dict per decile with: decile, n, pred_prob_mean, true_bad_rate.
    """
    df = pd.DataFrame({"y_true": y_true, "y_prob": y_prob})
    df["decile"] = pd.qcut(df["y_prob"].rank(method="first"), q=10, labels=False) + 1
    rows: list[dict[str, Any]] = []
    for dec in range(1, 11):
        sub = df[df["decile"] == dec]
        if sub.empty:
            continue
        rows.append({
            "decile": int(dec),
            "n": len(sub),
            "pred_prob_mean": round(float(sub["y_prob"].mean()), 6),
            "true_bad_rate": round(float(sub["y_true"].mean()), 6),
        })
    return rows


class ProbabilityCalibrator:
    """Fit a calibration mapping from raw probability (or margin) to
    calibrated probability.

    Parameters
    ----------
    method : str
        ``"sigmoid"`` (default, Platt scaling) or ``"isotonic"``.
    use_margin : bool
        If True, fit on raw margin (log-odds) from LightGBM; otherwise raw prob.
        Default True — falls back to raw_prob if margin not provided.
    """

    def __init__(self, method: str = "sigmoid", use_margin: bool = True):
        if method not in ("sigmoid", "isotonic"):
            raise ValueError(f"Unknown calibration method: {method!r}")
        self.method = method
        self.use_margin = use_margin
        self._calibrator: Any = None
        self._fitted = False

    def _build_input(self, raw_prob: np.ndarray,
                     raw_margin: np.ndarray | None = None) -> np.ndarray:
        if self.use_margin and raw_margin is not None:
            return raw_margin.reshape(-1, 1)
        # Clip raw_prob to avoid log(0) / log(1) issues for logloss metrics
        eps = 1e-15
        clipped = np.clip(raw_prob, eps, 1 - eps)
        # For sigmoid: logit transform so the model works in log-odds space
        if self.method == "sigmoid":
            return np.log(clipped / (1 - clipped)).reshape(-1, 1)
        return raw_prob.reshape(-1, 1)

    def fit(self, raw_prob: np.ndarray, y_true: np.ndarray,
            raw_margin: np.ndarray | None = None) -> None:
        """Fit calibrator on a held-out calibration set (NOT the training set).

        Rows with NaN ``y_true`` are silently dropped.
        """
        valid = ~np.isnan(y_true)
        yt = y_true[valid].astype(int)
        filtered_prob = raw_prob[valid]
        filtered_margin = raw_margin[valid] if raw_margin is not None else None
        X = self._build_input(filtered_prob, filtered_margin)
        if self.method == "sigmoid":
            from sklearn.linear_model import LogisticRegression
            self._calibrator = LogisticRegression(C=1e10, solver="lbfgs", max_iter=10000, random_state=42)
            self._calibrator.fit(X, yt)
        else:
            from sklearn.isotonic import IsotonicRegression
            self._calibrator = IsotonicRegression(out_of_bounds="clip")
            self._calibrator.fit(X.ravel(), yt)
        self._fitted = True

    def predict(self, raw_prob: np.ndarray,
                raw_margin: np.ndarray | None = None) -> np.ndarray:
        """Return calibrated probabilities.

        F02 fix: for the sigmoid (Platt) branch the calibrator is a
        ``LogisticRegression`` whose ``.predict()`` returns hard class labels
        (0/1) — use ``.predict_proba()[:, 1]`` to get a continuous calibrated
        probability.  Without this the sigmoid path shipped degenerate 0/1
        "probabilities" (logloss ~9.5-9.9) that were written as valid sidecar
        risk data.  Isotonic branch returns continuous values via ``.predict``.
        """
        if not self._fitted:
            raise RuntimeError("Calibrator not fitted. Call .fit() first.")
        X = self._build_input(raw_prob, raw_margin)
        X2 = X.reshape(-1, 1) if X.ndim == 1 else X
        if self.method == "sigmoid":
            proba = self._calibrator.predict_proba(X2)[:, 1]
        else:
            proba = self._calibrator.predict(X2)
        return np.clip(proba, 0.0, 1.0)


def compute_risk_percentile(calibrated_probs: np.ndarray) -> np.ndarray:
    """Cross-sectional risk percentile.

    Higher = more dangerous. Equal calibrated probabilities must receive
    the same percentile to avoid misleading risk ordering after isotonic
    calibration creates probability buckets.
    """
    s = pd.Series(calibrated_probs)
    return (s.rank(method="average", pct=True).to_numpy() * 100).astype(np.float32)


def run_calibration_pipeline(
    *,
    features: list[str],
    universe: str,
    label_id: str,
    trade_date: str,
    cal: list[str],
    td_idx: int,
    label_horizon: int,
    train_window_days: int = 504,
    calib_ratio: float = 0.15,
    test_ratio: float = 0.15,
    calib_method: str = "sigmoid",
    use_margin: bool = True,
    lgb_n_estimators: int = 300,
) -> dict[str, Any]:
    """End-to-end calibration pipeline for stop-loss binary classifier.

    1. Find training window respecting label maturity.
    2. Split chronologically: LGBM-train / calib / test.
    3. Train LGBM on LGBM-train portion.
    4. Compute raw_preds + raw_margins for calib & test portions.
    5. Fit calibrator on calib portion.
    6. Evaluate raw vs calibrated on test portion.
    7. Return calibration report + fitted artifacts.

    Parameters
    ----------
    cal : list[str]
        Trading calendar dates (sorted, YYYY-MM-DD).
    td_idx : int
        Index of trade_date in calendar (the prediction date).
    label_horizon : int
        Forward window in trading days.
    calib_ratio, test_ratio :
        Fraction of (train_window_days - label_horizon) to reserve.
    """
    from qlib.data import D
    from qsys.data.adapter import QlibAdapter
    from qsys.label.store import LabelStore
    from qsys.feature.registry import FeatureListRegistry

    QlibAdapter().init_qlib()

    # ── Resolve date boundaries ──
    train_end_idx = td_idx - label_horizon
    if train_end_idx < 0:
        raise ValueError(f"Not enough calendar before {trade_date}")
    n_available = train_end_idx
    n_train_lgb = int(n_available * (1 - calib_ratio - test_ratio))
    n_calib = int(n_available * calib_ratio)
    n_test = n_available - n_train_lgb - n_calib

    train_end = cal[train_end_idx]
    train_start = cal[max(0, train_end_idx - train_window_days)]

    # LGBM train: earliest portion
    lgbm_train_end_idx = train_start + n_train_lgb - 1 if train_window_days <= n_available else n_train_lgb - 1
    lgbm_train_end = cal[min(lgbm_train_end_idx, train_end_idx)]
    # calib: middle portion
    calib_start = cal[min(lgbm_train_end_idx + 1, train_end_idx)]
    calib_end = cal[min(lgbm_train_end_idx + n_calib, train_end_idx)]
    # test: latest portion (still with mature labels)
    test_start = cal[min(lgbm_train_end_idx + n_calib + 1, train_end_idx)]
    test_end = train_end

    print(f"  LGBM train:  [{train_start}, {lgbm_train_end}]  ({n_train_lgb} days)")
    print(f"  Calibration: [{calib_start}, {calib_end}]  ({n_calib} days)")
    print(f"  Test (OOS):  [{test_start}, {test_end}]  ({n_test} days)")

    # ── Load features ──
    raw = QlibAdapter().get_features(universe, features + ["$close"],
                                     start_time=train_start, end_time=trade_date)
    frame = raw.reset_index().rename(columns={"datetime": "trade_date"})
    frame = frame.loc[:, ~frame.columns.duplicated()]
    frame["trade_date"] = frame["trade_date"].astype(str).str[:10]
    frame["ts_code"] = frame["instrument"]
    frame = frame.sort_values("ts_code").reset_index(drop=True)

    # ── Load labels ──
    label_df = LabelStore().load_labels(label_id)

    # ── Train LGBM ──
    def _filter_and_train(date_start, date_end):
        train = frame[frame["trade_date"].between(date_start, date_end)].copy().merge(
            label_df[["trade_date", "instrument", "label_value"]],
            on=["trade_date", "instrument"], how="left",
        )
        y_valid = train["label_value"].notna()
        X_tr = train[features].fillna(0.0).astype(np.float32)
        y_tr = train.loc[y_valid, "label_value"].astype(int)
        if y_tr.empty or y_tr.nunique() < 2:
            return None, None, None, None
        pos = (y_tr == 1).sum()
        neg = (y_tr == 0).sum()
        print(f"    Train samples: {len(y_tr)}  pos={pos} ({100*pos/len(y_tr):.1f}%)")
        model, center, scale = train_model(
            X_tr.loc[y_tr.index], y_tr, "calib_lgbm",
            n_estimators=lgb_n_estimators, mode="binary",
        )
        return model, center, scale, (train, X_tr)

    model, center, scale, _ = _filter_and_train(train_start, lgbm_train_end)
    if model is None:
        raise ValueError("Failed to train LGBM for calibration pipeline")

    # ── Predict on calib and test sets ──
    def _predict_set(date_start, date_end):
        sub = frame[frame["trade_date"].between(date_start, date_end)].copy()
        if sub.empty:
            return None, None, None, None
        Xp = sub[features].fillna(0.0).astype(np.float32)
        # Get raw margin (before sigmoid) if possible
        from qsys.signal.alpha_v1.labels import robust_zscore_transform
        Xz = robust_zscore_transform(Xp, center, scale)
        raw_margin = model.predict(Xz.values, raw_score=True)
        raw_prob = model.predict(Xz.values)
        # Merge labels
        merged = sub.merge(
            label_df[["trade_date", "instrument", "label_value"]],
            on=["trade_date", "instrument"], how="left",
        )
        y_true = merged["label_value"].values
        return raw_prob, raw_margin, y_true, sub["ts_code"].values

    calib_prob, calib_margin, calib_y, calib_codes = _predict_set(calib_start, calib_end)
    test_prob, test_margin, test_y, test_codes = _predict_set(test_start, test_end)

    # ── Fit calibrator ──
    calibrator = ProbabilityCalibrator(method=calib_method, use_margin=use_margin)
    calib_input = calib_margin if use_margin and calib_margin is not None else calib_prob
    calibrator.fit(calib_prob, calib_y, raw_margin=calib_margin)
    print(f"  Calibrator: {calib_method} ({'margin' if use_margin else 'prob'}) fitted on {len(calib_y)} samples")

    # ── Evaluate on test set ──
    test_raw_metrics = _compute_metrics(test_y, test_prob) if test_y is not None else {}
    test_cal_prob = calibrator.predict(test_prob, raw_margin=test_margin) if test_prob is not None else np.array([])
    test_cal_metrics = _compute_metrics(test_y, test_cal_prob) if len(test_cal_prob) > 0 else {}

    test_true_bad_rate = float(test_y.mean()) if test_y is not None else None
    decile_raw = _calibration_by_decile(test_y, test_prob) if test_y is not None else []
    decile_cal = _calibration_by_decile(test_y, test_cal_prob) if len(test_cal_prob) > 0 else []

    # ── Return full results ──
    return {
        "model": model,
        "center": center,
        "scale": scale,
        "calibrator": calibrator,
        "eval": CalibrationEval(
            raw_auc=test_raw_metrics.get("auc"),
            raw_pr_auc=test_raw_metrics.get("pr_auc"),
            raw_logloss=test_raw_metrics.get("logloss"),
            raw_brier=test_raw_metrics.get("brier"),
            raw_prob_mean=test_raw_metrics.get("prob_mean"),
            cal_auc=test_cal_metrics.get("auc"),
            cal_pr_auc=test_cal_metrics.get("pr_auc"),
            cal_logloss=test_cal_metrics.get("logloss"),
            cal_brier=test_cal_metrics.get("brier"),
            cal_prob_mean=test_cal_metrics.get("prob_mean"),
            true_bad_rate=test_true_bad_rate,
            calibration_by_decile=decile_cal,
        ),
        "raw_metrics_on_test": test_raw_metrics,
        "cal_metrics_on_test": test_cal_metrics,
        "test_decile_raw": decile_raw,
        "test_decile_cal": decile_cal,
    }
