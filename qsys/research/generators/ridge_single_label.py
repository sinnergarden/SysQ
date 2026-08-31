"""Minimal chronological ridge baseline for rolling signal research."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from qsys.research.generators.lightgbm_single_label import (
    LightGBMSingleLabelGenerator,
)


@dataclass
class RidgeSingleLabelGenerator(LightGBMSingleLabelGenerator):
    """Fixed-alpha ridge using the same PIT/cache/window contracts as LightGBM."""

    ridge_alpha: float = 1.0

    def generate(self, **kwargs) -> pd.DataFrame:
        from qsys.research.generators.temporal_validation import (
            attach_latest_model_diagnostics,
        )

        return attach_latest_model_diagnostics(
            super().generate(**kwargs), self._window_model_diagnostics
        )

    def __post_init__(self) -> None:
        super().__post_init__()
        self.ridge_alpha = float(self.ridge_alpha)
        if not np.isfinite(self.ridge_alpha) or self.ridge_alpha <= 0:
            raise ValueError("ridge_alpha must be finite and positive")
        if self.sample_weight_policy is not None:
            raise ValueError("ridge baseline does not support sample weighting")

    @property
    def model_checkpoint_code_dependencies(self) -> dict[str, Path]:
        from qsys.research.generators import (
            lightgbm_single_label,
            temporal_validation,
        )

        return {
            "qsys.research.generators.lightgbm_single_label": Path(
                lightgbm_single_label.__file__
            ).resolve(),
            "qsys.research.generators.temporal_validation": Path(
                temporal_validation.__file__
            ).resolve(),
        }

    def _train_window_model(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        label_dates: pd.Series,
        *,
        window_id: str,
    ):
        from qsys.research.generators.temporal_validation import (
            chronological_tail_partition,
        )
        from qsys.signal.alpha_v1.labels import (
            robust_zscore_fit,
            robust_zscore_transform,
        )
        from qsys.signal.alpha_v1.training import resolve_validation_size

        validation_size = resolve_validation_size(len(y_train))
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
            target_validation_rows=validation_size,
        )
        fit_X = X_train.iloc[:-validation_size]
        fit_y = pd.to_numeric(y_train.iloc[:-validation_size], errors="raise")
        center, scale = robust_zscore_fit(fit_X)
        Xz = robust_zscore_transform(fit_X, center, scale).to_numpy(dtype=float)
        y_values = fit_y.to_numpy(dtype=float)
        intercept = float(y_values.mean())
        centered_y = y_values - intercept
        gram = Xz.T @ Xz
        coef = np.linalg.solve(
            gram + self.ridge_alpha * np.eye(gram.shape[0]),
            Xz.T @ centered_y,
        )
        model = {"coef": coef, "intercept": intercept}

        validation_X = X_train.iloc[-validation_size:]
        validation_y = y_train.iloc[-validation_size:]
        validation_pred = self._predict_window_model(
            model, center, scale, validation_X
        )
        validation_rank_ic = validation_pred.corr(
            validation_y, method="spearman"
        )
        self._window_model_diagnostics.append({
            "window_id": window_id,
            "model_type": "ridge_regression",
            "ridge_alpha": self.ridge_alpha,
            "train_rows": int(len(fit_y)),
            "validation_rows": validation_size,
            "validation_start_label_date": validation_start,
            "validation_end_label_date": validation_end,
            "validation_split_contract": "strict_later_whole_label_dates_v1",
            "validation_rank_ic": (
                float(validation_rank_ic) if pd.notna(validation_rank_ic) else None
            ),
            "feature_importance_abs_coefficient": {
                feature: float(abs(value))
                for feature, value in zip(X_train.columns, coef)
            },
            "signed_coefficient": {
                feature: float(value)
                for feature, value in zip(X_train.columns, coef)
            },
        })
        return model, center, scale

    @staticmethod
    def _predict_window_model(model, center, scale, X_predict: pd.DataFrame) -> pd.Series:
        from qsys.signal.alpha_v1.labels import robust_zscore_transform

        Xz = robust_zscore_transform(X_predict, center, scale).to_numpy(dtype=float)
        values = model["intercept"] + Xz @ model["coef"]
        return pd.Series(values, index=X_predict.index, dtype=float)

    @staticmethod
    def _release_window_model(model) -> None:
        del model
