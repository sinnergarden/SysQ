from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.model.financial_rc_trainer import (
    FinancialRCTrainingError,
    derive_training_window,
)
from qsys.model.registry import create_model_trainer, has_model_trainer
from qsys.signal.alpha_v1 import training as lgb_training


def _dates(count: int) -> list[str]:
    start = date(2022, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def test_window_uses_latest_available_fully_mature_label() -> None:
    sessions = _dates(900)
    labels = sessions[1:701]

    window = derive_training_window(
        sessions,
        labels,
        as_of_date=sessions[800],
        horizon=60,
        window_sessions=504,
    )

    assert window.label_end == sessions[700]
    assert window.train_end == sessions[699]
    assert window.train_start == sessions[196]
    assert window.label_start == sessions[197]
    assert window.maturity_sessions == 101


def test_window_rejects_label_without_strict_maturity_buffer() -> None:
    sessions = _dates(100)
    with pytest.raises(FinancialRCTrainingError, match="no mature labels"):
        derive_training_window(
            sessions,
            [sessions[98]],
            as_of_date=sessions[99],
            horizon=10,
            window_sessions=20,
        )


def test_financial_rc_has_dedicated_training_candidate(tmp_path) -> None:
    config = {
        "strategy_id": "financial_rc",
        "account_id": "research",
        "training": {
            "engine": "financial_rc_lightgbm_bundle_v1",
            "feature_list_id": "features",
            "universe": "csi800",
            "models": [],
        },
    }
    assert has_model_trainer("financial_rc")
    trainer = create_model_trainer(
        "financial_rc", config, project_root=tmp_path
    )
    assert trainer.strategy_id == "financial_rc"
    assert trainer.account_id == "research"


def test_scaler_is_fit_on_pre_validation_rows_only() -> None:
    X = pd.DataFrame({"a": range(10), "b": range(10, 20)}, dtype=float)
    y = pd.Series(range(10), dtype=float)
    center = pd.Series({"a": 1.0, "b": 2.0})
    scale = pd.Series({"a": 3.0, "b": 4.0})

    with patch.object(
        lgb_training, "robust_zscore_fit", return_value=(center, scale)
    ) as fit, patch.object(
        lgb_training,
        "_resolve_train_data",
        return_value=(object(), center, scale),
    ) as resolve:
        lgb_training.train_model(X, y, "test", validation_size=3)

    pd.testing.assert_frame_equal(fit.call_args.args[0], X.iloc[:-3])
    assert resolve.call_args.args[-1] == 3
