"""Alpha V1 — LightGBM 模型训练 / 预测。"""
from __future__ import annotations

from typing import Any

import lightgbm as lgb
import pandas as pd

from qsys.signal.alpha_v1.labels import robust_zscore_fit, robust_zscore_transform


def train_model(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    tag: str,
    n_estimators: int = 200,
    lgb_params: dict[str, Any] | None = None,
):
    """Train a single LightGBM model.

    Parameters
    ----------
    X_train : pd.DataFrame
        Training features (clean, not NaN).
    y_train : pd.Series
        Training labels (zscores, not NaN).
    tag : str
        Label for logging (e.g. '5d', '20d').
    n_estimators : int
        Number of boosting rounds.
    lgb_params : dict or None
        LightGBM hyperparameters (defaults to alpha_v1 candidate params).

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
    center, scale = robust_zscore_fit(X_train)
    Xz = robust_zscore_transform(X_train, center, scale)
    N = len(Xz)
    vs = min(20000, int(N * 0.15))
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, label=y_train.iloc[:-vs].values)
    val_data = lgb.Dataset(Xz.iloc[-vs:].values, label=y_train.iloc[-vs:].values)
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
