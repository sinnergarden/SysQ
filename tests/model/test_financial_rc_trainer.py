from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import pandas as pd
import pytest

from qsys.model.financial_rc_trainer import (
    compute_model_artifact_identity,
    FinancialRCTrainingError,
    FinancialRCTrainer,
    TrainingWindow,
    _filter_pit_membership,
    _label_coverage_universe,
    _prediction_membership_identity,
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

    pit_bound = compute_model_artifact_identity(
        artifact_hashes=base,
        universe_lineage={"pit_membership_sha256": "pit-a"},
        **kwargs,
    )
    pit_changed = compute_model_artifact_identity(
        artifact_hashes=base,
        universe_lineage={"pit_membership_sha256": "pit-b"},
        **kwargs,
    )
    assert pit_bound != pit_changed


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


def test_pit_label_coverage_uses_membership_window_not_ever_union() -> None:
    class Store:
        instruments = ["ACTIVE", "DELISTED_OUTSIDE_WINDOW"]

        def membership_window(self, start: str, end: str) -> list[str]:
            assert (start, end) == ("2023-10-30", "2025-11-25")
            return ["ACTIVE"]

    window = TrainingWindow(
        train_start="2023-10-27",
        train_end="2025-11-24",
        label_start="2023-10-30",
        label_end="2025-11-25",
        as_of_date="2026-08-24",
        horizon=180,
        window_sessions=504,
        maturity_sessions=182,
    )
    members, semantics = _label_coverage_universe(
        Store(), Store.instruments, window
    )

    result = profile_label_universe_coverage(
        ["ACTIVE"], members, min_coverage=0.99
    )

    assert semantics == "pit_membership_window"
    assert result["coverage"] == 1.0
    assert result["missing_members"] == []


def test_pit_label_coverage_still_rejects_missing_window_member() -> None:
    class Store:
        def membership_window(self, start: str, end: str) -> list[str]:
            return ["ACTIVE", "MISSING_IN_WINDOW"]

    window = TrainingWindow(
        train_start="2023-10-27",
        train_end="2025-11-24",
        label_start="2023-10-30",
        label_end="2025-11-25",
        as_of_date="2026-08-24",
        horizon=180,
        window_sessions=504,
        maturity_sessions=182,
    )
    members, _ = _label_coverage_universe(Store(), [], window)

    with pytest.raises(
        FinancialRCTrainingError,
        match="label artifact does not cover the current training universe",
    ):
        profile_label_universe_coverage(
            ["ACTIVE"], members, min_coverage=0.99
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


def _trainer_config(*, strategy_id: str, models: list[dict], engine: str) -> dict:
    return {
        "strategy_id": strategy_id,
        "feature_freshness": {
            "shareholder": {
                "min_coverage": 0.95,
                "features": {
                    "holder_num_stale_days": {
                        "max_median_days": 200,
                        "max_row_days": 365,
                    },
                    "top10_holder_stale_days": {
                        "max_median_days": 250,
                        "max_row_days": 365,
                    },
                },
            }
        },
        "inference": {"universe": "csi1800"},
        "training": {
            "engine": engine,
            "feature_list_id": "features",
            "training_universe": "csi1800_pit_union",
            "models": models,
        },
    }


def test_single_model_generic_engine_and_s180_registry(tmp_path) -> None:
    config = _trainer_config(
        strategy_id="s180_top10",
        engine="lightgbm_model_bundle_v1",
        models=[
            {
                "tag": "s180",
                "label_id": "fwd_ret_180d_raw",
                "experiment_id": "s180_top10",
                "horizon": 180,
            }
        ],
    )
    trainer = create_model_trainer("s180_top10", config, project_root=tmp_path)
    settings = trainer._settings()
    assert settings["engine"] == "lightgbm_model_bundle_v1"
    assert settings["training_universe"] == "csi1800_pit_union"
    assert settings["inference_universe"] == "csi1800"
    assert [item["tag"] for item in settings["models"]] == ["s180"]


def test_legacy_financial_rc_engine_still_accepts_two_models(tmp_path) -> None:
    config = _trainer_config(
        strategy_id="financial_rc",
        engine="financial_rc_lightgbm_bundle_v1",
        models=[
            {
                "tag": "60d",
                "label_id": "fwd_ret_60d_raw",
                "experiment_id": "60d",
                "horizon": 60,
            },
            {
                "tag": "180d",
                "label_id": "fwd_ret_180d_raw",
                "experiment_id": "180d",
                "horizon": 180,
            },
        ],
    )
    settings = FinancialRCTrainer(config, tmp_path)._settings()
    assert len(settings["models"]) == 2
    assert settings["income_feature_source"] == {
        "mode": "legacy_unverified_global_v0",
        "artifact_id": "",
        "artifact_path": "",
        "artifact_sha256": "",
        "manifest_path": "",
        "manifest_sha256": "",
        "required_history_start": "",
    }


def test_training_audited_income_mode_requires_complete_binding(tmp_path) -> None:
    config = _trainer_config(
        strategy_id="s180_top10",
        engine="lightgbm_model_bundle_v1",
        models=[{
            "tag": "s180",
            "label_id": "fwd_ret_180d_raw",
            "experiment_id": "s180_top10",
            "horizon": 180,
        }],
    )
    config["income_feature_source"] = {"mode": "audited_sidecar_v1"}

    with pytest.raises(FinancialRCTrainingError, match="artifact/manifest identity"):
        FinancialRCTrainer(config, tmp_path)._settings()


def test_pit_filter_uses_strict_row_date_intervals() -> None:
    class Store:
        spans = pd.DataFrame(
            [
                {
                    "instrument": "AAA",
                    "effective_from": "20220102",
                    "effective_to": "20220103",
                }
            ]
        )

    frame = pd.DataFrame(
        {
            "trade_date": ["2022-01-01", "2022-01-02", "2022-01-03", "2022-01-04"],
            "instrument": ["AAA", "AAA", "AAA", "AAA"],
            "value": [1, 2, 3, 4],
        }
    )
    result = _filter_pit_membership(frame, Store())
    assert result["value"].tolist() == [2, 3]


def test_pit_filter_allows_disjoint_reentry_intervals() -> None:
    class Store:
        spans = pd.DataFrame(
            [
                {
                    "instrument": "AAA",
                    "effective_from": "20220101",
                    "effective_to": "20220102",
                },
                {
                    "instrument": "AAA",
                    "effective_from": "20220104",
                    "effective_to": "20220105",
                },
            ]
        )

    frame = pd.DataFrame(
        {
            "trade_date": ["2022-01-02", "2022-01-03", "2022-01-04"],
            "instrument": ["AAA", "AAA", "AAA"],
            "value": [2, 3, 4],
        }
    )
    result = _filter_pit_membership(frame, Store())
    assert result["value"].tolist() == [2, 4]


def test_pit_filter_rejects_overlapping_active_intervals() -> None:
    class Store:
        spans = pd.DataFrame(
            [
                {
                    "instrument": "AAA",
                    "effective_from": "20220101",
                    "effective_to": "20220103",
                },
                {
                    "instrument": "AAA",
                    "effective_from": "20220102",
                    "effective_to": "20220104",
                },
            ]
        )

    frame = pd.DataFrame(
        {
            "trade_date": ["2022-01-02"],
            "instrument": ["AAA"],
            "value": [2],
        }
    )
    with pytest.raises(FinancialRCTrainingError, match="overlapping spans"):
        _filter_pit_membership(frame, Store())


def test_prediction_membership_is_regular_parquet_and_hash_bound(tmp_path) -> None:
    path = tmp_path / "members.parquet"
    pd.DataFrame({"instrument": ["AAA", "BBB"]}).to_parquet(path, index=False)
    resolved, digest, members = _prediction_membership_identity(tmp_path, path.name)
    assert resolved == path.resolve()
    assert len(digest) == 64
    assert members == {"AAA", "BBB"}

    symlink = tmp_path / "members-link.parquet"
    symlink.symlink_to(path)
    with pytest.raises(FinancialRCTrainingError, match="regular parquet"):
        _prediction_membership_identity(tmp_path, symlink.name)

    duplicate = tmp_path / "duplicate.parquet"
    pd.DataFrame({"instrument": ["AAA", "aaa"]}).to_parquet(duplicate, index=False)
    with pytest.raises(FinancialRCTrainingError, match="duplicate"):
        _prediction_membership_identity(tmp_path, duplicate.name)


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
