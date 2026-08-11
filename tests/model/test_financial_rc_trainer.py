from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.model.financial_rc_trainer import (
    compute_model_artifact_identity,
    FinancialRCTrainingError,
    derive_purged_evaluation_train_end,
    derive_training_window,
    profile_label_universe_coverage,
)
from qsys.model.registry import create_model_trainer, has_model_trainer
from qsys.signal.alpha_v1 import training as lgb_training


def _dates(count: int) -> list[str]:
    start = date(2022, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def test_artifact_identity_distinguishes_same_model_with_different_snapshot() -> None:
    base = {
        "model.txt": "model",
        "center.json": "center",
        "scale.json": "scale",
        "training_snapshot.parquet": "snapshot-a",
    }
    kwargs = {
        "feature_list_hash": "features",
        "label_lineage": {"label_sha256": "labels"},
        "training_config_hash": "config",
        "feature_availability": {"margin": {"lag_sessions": 0}},
    }
    first = compute_model_artifact_identity(artifact_hashes=base, **kwargs)
    changed = {**base, "training_snapshot.parquet": "snapshot-b"}
    second = compute_model_artifact_identity(artifact_hashes=changed, **kwargs)

    assert first != second


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


def test_evaluation_split_purges_the_forward_label_span() -> None:
    sessions = _dates(300)
    # horizon=60 leaves sessions 139..199 (61 sessions) strictly between
    # the final training feature at 138 and validation at 200.
    assert derive_purged_evaluation_train_end(
        sessions, sessions[200], 60
    ) == sessions[138]


def test_label_universe_gate_rejects_stale_membership_artifact() -> None:
    current = [f"S{index:03d}" for index in range(100)]
    stale_labels = current[:94] + [f"OLD{index:03d}" for index in range(6)]

    with pytest.raises(
        FinancialRCTrainingError,
        match="label artifact does not cover the current training universe",
    ):
        profile_label_universe_coverage(
            stale_labels,
            current,
            min_coverage=0.95,
        )


def test_label_universe_gate_allows_small_recent_listing_gap() -> None:
    current = [f"S{index:03d}" for index in range(100)]
    result = profile_label_universe_coverage(
        current[:99],
        current,
        min_coverage=0.99,
    )

    assert result["coverage"] == 0.99
    assert result["missing_members"] == ["S099"]


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


def test_fixed_round_refit_uses_all_rows() -> None:
    X = pd.DataFrame({"a": range(10), "b": range(10, 20)}, dtype=float)
    y = pd.Series(range(10), dtype=float)
    center = pd.Series({"a": 1.0, "b": 2.0})
    scale = pd.Series({"a": 3.0, "b": 4.0})

    with patch.object(
        lgb_training, "robust_zscore_fit", return_value=(center, scale)
    ) as fit, patch.object(
        lgb_training,
        "robust_zscore_transform",
        return_value=X,
    ), patch.object(lgb_training.lgb, "train", return_value=object()) as train:
        lgb_training.fit_model_fixed_rounds(
            X, y, "test", n_estimators=17
        )

    pd.testing.assert_frame_equal(fit.call_args.args[0], X)
    assert train.call_args.kwargs["num_boost_round"] == 17
