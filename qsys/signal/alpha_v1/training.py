"""Alpha V1 — LightGBM 模型训练 / 预测。"""
from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform


_DEFAULT_LGB_PARAMS = {
    "colsample_bytree": 0.8879,
    "learning_rate": 0.0421,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 8,
    "verbosity": -1,
    "seed": 42,
}

_DEFAULT_BINARY_LGB_PARAMS = {
    "colsample_bytree": 0.8879,
    "learning_rate": 0.0421,
    "subsample": 0.8789,
    "lambda_l1": 205.6999,
    "lambda_l2": 580.9768,
    "max_depth": 6,
    "num_leaves": 64,
    "num_threads": 8,
    "verbosity": -1,
    "seed": 42,
    "scale_pos_weight": 1.0,  # override per training if imbalanced
}


def _resolve_train_data(X: pd.DataFrame, center: pd.Series, scale: pd.Series,
                        y: pd.Series, tag: str, mode: str, n_estimators: int,
                        lgb_params: dict, validation_size: int) -> tuple[lgb.Booster, Any, Any]:
    """Shared training logic for regression and binary modes."""
    Xz = robust_zscore_transform(X, center, scale)
    vs = validation_size
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y.iloc[-vs:].values)

    params = dict(lgb_params)
    if mode == "binary":
        params["objective"] = "binary"
        params["metric"] = "auc"
        # Auto-balance pos_weight when binary
        pos = (y.iloc[:-vs] == 1).sum()
        neg = (y.iloc[:-vs] == 0).sum()
        if pos > 0 and neg > 0 and params.get("scale_pos_weight", 1.0) == 1.0:
            params["scale_pos_weight"] = neg / pos
    else:
        params.setdefault("objective", "regression")
        params.setdefault("metric", "mse")

    model = lgb.train(
        params,
        train_data,
        num_boost_round=n_estimators,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
    )
    pred = pd.Series(model.predict(Xz.values), index=Xz.index)
    valid = pred.notna() & y.notna()
    n_trees = model.best_iteration if model.best_iteration else n_estimators

    if valid.sum() > 0:
        if mode == "binary":
            from sklearn.metrics import roc_auc_score
            auc = roc_auc_score(y[valid].astype(int), pred[valid])
            print(f"    [{tag}] Train AUC={auc:.5f}, trees={n_trees}")
        else:
            ric = float(pred[valid].corr(y[valid], method="spearman"))
            print(f"    [{tag}] Train RankIC={ric:.5f}, trees={n_trees}")
    return model, center, scale


def train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    tag: str,
    n_estimators: int = 200,
    lgb_params: dict[str, Any] | None = None,
    mode: str = "regression",
    validation_size: int | None = None,
):
    """Train a single LightGBM model.

    Parameters
    ----------
    X_train : pd.DataFrame
        Training features (clean, not NaN).
    y_train : pd.Series
        Training labels. For mode='regression' continuous values;
        for mode='binary' {0, 1}.
    tag : str
        Label for logging (e.g. '5d', '20d').
    n_estimators : int
        Number of boosting rounds.
    lgb_params : dict or None
        LightGBM hyperparameters. When *mode='binary'*, defaults to
        ``_DEFAULT_BINARY_LGB_PARAMS``.  Auto-computes ``scale_pos_weight``
        when left at 1.0.
    mode : str
        ``'regression'`` (default) or ``'binary'``.
    validation_size : int or None
        Number of trailing, time-ordered rows reserved for validation.  When
        omitted, use the legacy 15% rule capped at 20,000 rows.  The robust
        scaler is always fitted on the pre-validation rows only.

    Returns
    -------
    tuple of (lgb.Booster, pd.Series, pd.Series)
        model, center, scale
    """
    if lgb_params is None:
        lgb_params = dict(_DEFAULT_BINARY_LGB_PARAMS if mode == "binary" else _DEFAULT_LGB_PARAMS)
    if len(X_train) != len(y_train):
        raise ValueError("X_train and y_train must have the same number of rows")
    if len(X_train) < 2:
        raise ValueError("training requires at least two rows")
    if validation_size is None:
        validation_size = max(1, min(20000, int(len(X_train) * 0.15)))
    if not 0 < validation_size < len(X_train):
        raise ValueError(
            "validation_size must be positive and smaller than the training set"
        )

    # Fit preprocessing only on the earlier training partition.  Fitting the
    # median/IQR on the trailing validation period leaks future distribution
    # information into model selection.
    center, scale = robust_zscore_fit(X_train.iloc[:-validation_size])
    return _resolve_train_data(
        X_train,
        center,
        scale,
        y_train,
        tag,
        mode,
        n_estimators,
        lgb_params,
        validation_size,
    )


def predict_model(
    model: lgb.Booster,
    center: pd.Series,
    scale: pd.Series,
    X: pd.DataFrame,
    mode: str = "regression",
) -> pd.Series:
    """Predict using a trained LightGBM model.

    For mode='regression', returns raw predictions (then typically z-scored).
    For mode='binary', returns predicted probability of class 1.
    """
    Xz = robust_zscore_transform(X, center, scale)
    pred = model.predict(Xz.values)
    return pd.Series(pred, index=X.index)
