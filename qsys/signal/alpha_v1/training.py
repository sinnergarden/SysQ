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


# Closed vocabulary: model checkpoints must have stable, reproducible weight
# semantics.  ``None`` remains the legacy unweighted behaviour.
SUPPORTED_SAMPLE_WEIGHT_POLICIES = frozenset({"top_tail_v1"})


def validate_sample_weight_policy(policy: str | None) -> str | None:
    """Validate the supported sample-weight policy name."""
    if policy is None:
        return None
    if not isinstance(policy, str) or policy not in SUPPORTED_SAMPLE_WEIGHT_POLICIES:
        raise ValueError(
            "sample_weight_policy must be None or exactly one of "
            f"{sorted(SUPPORTED_SAMPLE_WEIGHT_POLICIES)}; got {policy!r}"
        )
    return policy


def resolve_validation_size(n_rows: int, validation_size: int | None = None) -> int:
    """Resolve the canonical trailing validation partition size."""
    if n_rows < 2:
        raise ValueError("training requires at least two rows")
    resolved = (
        max(1, min(20000, int(n_rows * 0.15)))
        if validation_size is None
        else validation_size
    )
    if not 0 < resolved < n_rows:
        raise ValueError(
            "validation_size must be positive and smaller than the training set"
        )
    return resolved


def compute_sample_weight(
    labels: pd.Series,
    label_dates: pd.Series,
    policy: str | None,
) -> pd.Series | None:
    """Compute date-wise graduated top-tail weights for a named policy.

    ``top_tail_v1`` assigns 1.0 by default, 2.0 at/above the 80th
    percentile, and 3.0 at/above the 90th percentile.  ``label_dates`` are
    the matured label dates, not feature or execution dates.
    """
    validate_sample_weight_policy(policy)
    if policy is None:
        return None
    if not isinstance(labels, pd.Series) or not isinstance(label_dates, pd.Series):
        raise TypeError("labels and label_dates must both be pandas Series")
    if not labels.index.equals(label_dates.index):
        raise ValueError("labels and label_dates must have identical indexes")
    if labels.empty:
        raise ValueError("cannot compute sample weights for empty labels")
    values = pd.to_numeric(labels, errors="coerce")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("labels must be finite to compute sample weights")
    if label_dates.isna().any():
        raise ValueError("label_dates must be non-null to compute sample weights")

    frame = pd.DataFrame({"label": values.astype(float), "label_date": label_dates})
    percentile = frame.groupby("label_date", sort=False)["label"].rank(
        pct=True, method="average"
    )
    weights = pd.Series(1.0, index=labels.index, dtype=float)
    weights.loc[percentile.index[percentile >= 0.80]] = 2.0
    weights.loc[percentile.index[percentile >= 0.90]] = 3.0
    if not np.isfinite(weights.to_numpy()).all() or (weights <= 0).any():
        raise ValueError("computed sample weights must be finite and positive")
    return weights


def compute_train_partition_sample_weight(
    labels: pd.Series,
    label_dates: pd.Series,
    policy: str | None,
    validation_size: int | None = None,
) -> pd.Series | None:
    """Compute weights using only labels before the validation boundary.

    The returned vector remains aligned to all ``labels`` so it can be passed
    through the existing training API; its trailing validation values are
    harmless unit placeholders because ``_resolve_train_data`` never attaches
    weights to the validation Dataset.
    """
    validate_sample_weight_policy(policy)
    if policy is None:
        return None
    if not isinstance(labels, pd.Series) or not isinstance(label_dates, pd.Series):
        raise TypeError("labels and label_dates must both be pandas Series")
    if not labels.index.equals(label_dates.index):
        raise ValueError("labels and label_dates must have identical indexes")
    vs = resolve_validation_size(len(labels), validation_size)
    train_labels = labels.iloc[:-vs]
    train_dates = label_dates.iloc[:-vs]
    train_weights = compute_sample_weight(train_labels, train_dates, policy)
    assert train_weights is not None  # policy was validated as non-None above
    weights = pd.Series(1.0, index=labels.index, dtype=float)
    weights.iloc[:-vs] = train_weights.to_numpy()
    return weights


def _validate_sample_weight(
    sample_weight: pd.Series | np.ndarray | None,
    index: pd.Index,
) -> pd.Series | np.ndarray | None:
    """Validate weights without silently reordering the training rows."""
    if sample_weight is None:
        return None
    if isinstance(sample_weight, pd.Series):
        if not sample_weight.index.equals(index):
            raise ValueError("sample_weight Series must have the exact training index")
        values = sample_weight.to_numpy(dtype=float)
        normalized: pd.Series | np.ndarray = sample_weight.astype(float)
    else:
        values = np.asarray(sample_weight, dtype=float)
        if values.ndim != 1 or len(values) != len(index):
            raise ValueError("sample_weight must be a one-dimensional aligned vector")
        normalized = values
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("sample_weight must contain only finite positive values")
    return normalized


def _resolve_train_data(X: pd.DataFrame, center: pd.Series, scale: pd.Series,
                        y: pd.Series, tag: str, mode: str, n_estimators: int,
                        lgb_params: dict, validation_size: int,
                        sample_weight: pd.Series | np.ndarray | None = None
                        ) -> tuple[lgb.Booster, Any, Any]:
    """Shared training logic for regression and binary modes."""
    Xz = robust_zscore_transform(X, center, scale)
    vs = validation_size
    weight = _validate_sample_weight(sample_weight, y.index)
    train_weight = None if weight is None else weight[:-vs]
    train_kwargs: dict[str, Any] = {"label": y.iloc[:-vs].values}
    if train_weight is not None:
        train_kwargs["weight"] = (
            train_weight.to_numpy()
            if isinstance(train_weight, pd.Series)
            else train_weight
        )
    train_data = lgb.Dataset(Xz.iloc[:-vs].values, **train_kwargs)
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
    sample_weight: pd.Series | np.ndarray | None = None,
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
    if sample_weight is not None and not X_train.index.equals(y_train.index):
        raise ValueError("sample_weight requires X_train and y_train to be index-aligned")
    validation_size = resolve_validation_size(len(X_train), validation_size)
    sample_weight = _validate_sample_weight(sample_weight, y_train.index)

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
        sample_weight=sample_weight,
    )


def fit_model_fixed_rounds(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    tag: str,
    *,
    n_estimators: int,
    lgb_params: dict[str, Any] | None = None,
    mode: str = "regression",
    sample_weight: pd.Series | np.ndarray | None = None,
):
    """Fit a serving model on all rows using a preselected tree count.

    This is the second stage after a purged time holdout has selected
    ``n_estimators``.  There is deliberately no validation set or early
    stopping here; preprocessing and the model are refit on the full matured
    training window.
    """

    if len(X_train) != len(y_train) or X_train.empty:
        raise ValueError("fixed-round training requires aligned, non-empty inputs")
    if sample_weight is not None and not X_train.index.equals(y_train.index):
        raise ValueError("sample_weight requires X_train and y_train to be index-aligned")
    if n_estimators <= 0:
        raise ValueError("n_estimators must be positive")
    sample_weight = _validate_sample_weight(sample_weight, y_train.index)
    if lgb_params is None:
        lgb_params = dict(
            _DEFAULT_BINARY_LGB_PARAMS
            if mode == "binary"
            else _DEFAULT_LGB_PARAMS
        )
    params = dict(lgb_params)
    if mode == "binary":
        params["objective"] = "binary"
        params["metric"] = "auc"
        pos = int((y_train == 1).sum())
        neg = int((y_train == 0).sum())
        if pos > 0 and neg > 0 and params.get("scale_pos_weight", 1.0) == 1.0:
            params["scale_pos_weight"] = neg / pos
    else:
        params.setdefault("objective", "regression")
        params.setdefault("metric", "mse")

    center, scale = robust_zscore_fit(X_train)
    Xz = robust_zscore_transform(X_train, center, scale)
    dataset_kwargs: dict[str, Any] = {"label": y_train.values}
    if sample_weight is not None:
        dataset_kwargs["weight"] = (
            sample_weight.to_numpy()
            if isinstance(sample_weight, pd.Series)
            else sample_weight
        )
    model = lgb.train(
        params,
        lgb.Dataset(Xz.values, **dataset_kwargs),
        num_boost_round=n_estimators,
        callbacks=[lgb.log_evaluation(0)],
    )
    print(f"    [{tag}] Refit serving model on all rows, trees={n_estimators}")
    return model, center, scale


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
