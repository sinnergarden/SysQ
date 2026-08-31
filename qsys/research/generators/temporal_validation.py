"""Strict whole-date validation partitions for rolling research models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)


def attach_latest_model_diagnostics(
    predictions: pd.DataFrame,
    diagnostics: list[dict[str, object]],
) -> pd.DataFrame:
    """Attach one window's diagnostics to its checkpoint-bound predictions."""
    if not diagnostics:
        raise ValueError("window model diagnostics were not recorded")
    predictions.attrs["model_diagnostics"] = dict(diagnostics[-1])
    return predictions


def chronological_tail_partition(
    X: pd.DataFrame,
    y: pd.Series,
    label_dates: pd.Series,
    *,
    target_validation_rows: int,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, int, str, str]:
    """Sort by label date and reserve complete trailing dates for validation."""
    if not (len(X) == len(y) == len(label_dates)) or X.empty:
        raise ValueError("chronological validation inputs must be aligned and non-empty")
    if target_validation_rows <= 0 or target_validation_rows >= len(y):
        raise ValueError("target_validation_rows must be within the training sample")
    parsed = pd.to_datetime(label_dates, errors="coerce")
    if parsed.isna().any():
        raise ValueError("label_dates contain invalid values")
    order = np.argsort(parsed.to_numpy(), kind="stable")
    ordered_X = X.iloc[order]
    ordered_y = y.iloc[order]
    ordered_dates = parsed.iloc[order]
    counts = ordered_dates.value_counts(sort=False).sort_index()
    accumulated = 0
    cutoff: pd.Timestamp | None = None
    for date, count in counts.sort_index(ascending=False).items():
        accumulated += int(count)
        cutoff = pd.Timestamp(date)
        if accumulated >= target_validation_rows:
            break
    if cutoff is None:
        raise ValueError("could not resolve chronological validation cutoff")
    validation_size = int((ordered_dates >= cutoff).sum())
    fit_size = len(ordered_dates) - validation_size
    if fit_size <= 0:
        raise ValueError("chronological validation leaves no fitting rows")
    fit_max = ordered_dates.iloc[:fit_size].max()
    validation_min = ordered_dates.iloc[fit_size:].min()
    if not fit_max < validation_min:
        raise ValueError("validation split must separate whole label dates")
    return (
        ordered_X,
        ordered_y,
        ordered_dates,
        validation_size,
        validation_min.strftime("%Y-%m-%d"),
        ordered_dates.iloc[-1].strftime("%Y-%m-%d"),
    )


@dataclass
class TemporalValidationLightGBMSingleLabelGenerator(
    LightGBMSingleLabelGenerator
):
    """LightGBM baseline with a strictly later whole-date validation tail."""

    @property
    def model_checkpoint_code_dependencies(self) -> dict[str, Path]:
        from qsys.research.generators import lightgbm_single_label

        return {
            "qsys.research.generators.lightgbm_single_label": Path(
                lightgbm_single_label.__file__
            ).resolve(),
        }

    def generate(self, **kwargs) -> pd.DataFrame:
        return attach_latest_model_diagnostics(
            super().generate(**kwargs), self._window_model_diagnostics
        )

    def _train_window_model(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        label_dates: pd.Series,
        *,
        window_id: str,
    ):
        from qsys.signal.alpha_v1.training import (
            compute_train_partition_sample_weight,
            resolve_validation_size,
            train_model,
        )

        target_size = resolve_validation_size(len(y_train))
        (
            X_train,
            y_train,
            label_dates,
            validation_size,
            validation_start,
            validation_end,
        ) = chronological_tail_partition(
            X_train,
            y_train,
            label_dates,
            target_validation_rows=target_size,
        )
        sample_weight = compute_train_partition_sample_weight(
            y_train,
            label_dates,
            self.sample_weight_policy,
            validation_size=validation_size,
        )
        model, center, scale = train_model(
            X_train,
            y_train,
            "window",
            n_estimators=self.n_estimators,
            lgb_params=self.lgb_params,
            validation_size=validation_size,
            sample_weight=sample_weight,
        )
        validation_pred = self._predict_window_model(
            model, center, scale, X_train.iloc[-validation_size:]
        )
        validation_y = y_train.iloc[-validation_size:]
        validation_rank_ic = validation_pred.corr(
            validation_y, method="spearman"
        )
        feature_importance = getattr(model, "feature_importance", None)
        if callable(feature_importance):
            gain = feature_importance(importance_type="gain")
            split = feature_importance(importance_type="split")
        else:
            gain = np.zeros(len(X_train.columns), dtype=float)
            split = np.zeros(len(X_train.columns), dtype=int)
        best_iteration = getattr(model, "best_iteration", None)
        self._window_model_diagnostics.append({
            "window_id": window_id,
            "model_type": "lightgbm_regression",
            "train_rows": int(len(y_train) - validation_size),
            "validation_rows": validation_size,
            "validation_start_label_date": validation_start,
            "validation_end_label_date": validation_end,
            "validation_split_contract": "strict_later_whole_label_dates_v1",
            "validation_rank_ic": (
                float(validation_rank_ic)
                if pd.notna(validation_rank_ic) else None
            ),
            "best_iteration": int(best_iteration or self.n_estimators),
            "feature_importance_gain": {
                feature: float(value)
                for feature, value in zip(X_train.columns, gain)
            },
            "feature_importance_split": {
                feature: int(value)
                for feature, value in zip(X_train.columns, split)
            },
        })
        return model, center, scale
