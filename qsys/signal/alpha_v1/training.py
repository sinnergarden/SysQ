"""Alpha V1 — LightGBM 模型训练 / 预测。"""
from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd

from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform


def train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    tag: str,
    n_estimators: int = 200,
    lgb_params: dict[str, Any] | None = None,
    sample_weight: pd.Series | None = None,
    groups: np.ndarray | None = None,
):
    """Train a single LightGBM model.

    Parameters
    ----------
    X_train : pd.DataFrame
        Training features (clean, not NaN).
    y_train : pd.Series
        Training labels (or ranking bins 0-4 for lambdarank).
    tag : str
        Label for logging (e.g. '5d', '20d').
    n_estimators : int
        Number of boosting rounds.
    lgb_params : dict or None
        LightGBM hyperparameters (defaults to alpha_v1 candidate params).
    sample_weight : pd.Series or None
        Per-sample weights (recency weighting).
    groups : np.ndarray or None
        Group sizes for lambdarank (one entry per date).

    Returns
    -------
    tuple of (lgb.Booster, pd.Series, pd.Series)
        model, center, scale
    """
    if lgb_params is None:
        lgb_params = {
            "objective": "regression",
            "metric": "mse",
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

    is_rank = lgb_params.get("objective") == "lambdarank"

    if is_rank:
        # Skip robust_zscore for lambdarank (features still standardized,
        # but group info must be passed for correct validation split)
        center = pd.Series(0, index=X_train.columns)
        scale = pd.Series(1, index=X_train.columns)
        Xz = X_train.astype(np.float32)

        # Split by groups, not rows, to preserve query boundaries
        cumsum = np.cumsum(groups)
        n_train_groups = max(1, int(len(groups) * 0.85))
        split_idx = int(cumsum[n_train_groups - 1]) if n_train_groups > 1 else len(Xz)

        train_data = lgb.Dataset(
            Xz.iloc[:split_idx].values,
            label=y_train.iloc[:split_idx].values,
            group=groups[:n_train_groups],
            weight=sample_weight.iloc[:split_idx].values if sample_weight is not None else None,
        )
        val_data = lgb.Dataset(
            Xz.iloc[split_idx:].values,
            label=y_train.iloc[split_idx:].values,
            group=groups[n_train_groups:],
            weight=sample_weight.iloc[split_idx:].values if sample_weight is not None else None,
        )
    else:
        center, scale = robust_zscore_fit(X_train)
        Xz = robust_zscore_transform(X_train, center, scale)
        N = len(Xz)
        vs = min(20000, int(N * 0.15))
        train_data = lgb.Dataset(
            Xz.iloc[:-vs].values,
            label=y_train.iloc[:-vs].values,
            weight=sample_weight.iloc[:-vs].values if sample_weight is not None else None,
        )
        val_data = lgb.Dataset(
            Xz.iloc[-vs:].values,
            label=y_train.iloc[-vs:].values,
            weight=sample_weight.iloc[-vs:].values if sample_weight is not None else None,
        )

    model = lgb.train(
        lgb_params,
        train_data,
        num_boost_round=n_estimators,
        valid_sets=[val_data],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(0)],
    )
    pred = pd.Series(model.predict(Xz.values), index=Xz.index)
    valid = pred.notna() & y_train.notna()
    if valid.sum() > 0:
        ric = float(pred[valid].corr(y_train[valid], method="spearman"))
        print(f"    [{tag}] Train RankIC={ric:.5f}, trees={model.best_iteration}")
    return model, center, scale


def predict_model(model: lgb.Booster, center: pd.Series, scale: pd.Series, X: pd.DataFrame) -> pd.Series:
    Xz = robust_zscore_transform(X, center, scale)
    return pd.Series(model.predict(Xz.values), index=X.index)
