"""Contracts for the closed canonical LightGBM sample-weight policy."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qsys.signal.alpha_v1 import training


def test_top_tail_weights_are_datewise_and_graduated() -> None:
    labels = pd.Series(
        [1.0, 2.0, 3.0, 4.0, 5.0] * 2,
        index=pd.Index(range(10)),
    )
    dates = pd.Series(["2026-01-01"] * 5 + ["2026-01-02"] * 5, index=labels.index)

    weights = training.compute_sample_weight(labels, dates, "top_tail_v1")

    assert weights is not None
    assert weights.index.equals(labels.index)
    # pct ranks are .2/.4/.6/.8/1.0: 80% receives 2x and 100% receives 3x.
    assert weights.iloc[:5].tolist() == [1.0, 1.0, 1.0, 2.0, 3.0]
    assert weights.iloc[5:].tolist() == [1.0, 1.0, 1.0, 2.0, 3.0]


def test_train_weights_ignore_validation_labels_at_same_date() -> None:
    # The row split deliberately cuts through one label_date group.  Only
    # the first three rows may determine the training percentile ranks.
    dates = pd.Series(["2026-01-01"] * 5)
    first = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    changed_validation = pd.Series([1.0, 2.0, 3.0, -100.0, 100.0])

    weights_first = training.compute_train_partition_sample_weight(
        first, dates, "top_tail_v1", validation_size=2
    )
    weights_changed = training.compute_train_partition_sample_weight(
        changed_validation, dates, "top_tail_v1", validation_size=2
    )

    assert weights_first is not None and weights_changed is not None
    assert weights_first.iloc[:3].tolist() == weights_changed.iloc[:3].tolist()
    assert weights_first.iloc[:3].tolist() == [1.0, 1.0, 3.0]
    # Placeholder values are never attached to validation Dataset.
    assert weights_first.iloc[3:].tolist() == [1.0, 1.0]


@pytest.mark.parametrize("policy", ["unknown", "TOP_TAIL_V1", True, {}, ["top_tail_v1"]])
def test_weight_policy_schema_fails_closed(policy) -> None:
    with pytest.raises((TypeError, ValueError), match="sample_weight_policy"):
        training.validate_sample_weight_policy(policy)


def test_weight_policy_default_preserves_unweighted_behavior() -> None:
    labels = pd.Series([1.0, 2.0], index=[10, 11])
    dates = pd.Series(["2026-01-01", "2026-01-01"], index=[10, 11])
    assert training.compute_sample_weight(labels, dates, None) is None


def test_complete_date_validation_reaches_target_without_splitting_date() -> None:
    dates = pd.Series(
        ["2026-01-01"] * 4
        + ["2026-01-02"] * 3
        + ["2026-01-03"] * 4
        + ["2026-01-04"] * 3
    )

    validation_size = training.resolve_complete_date_validation_size(
        dates, validation_size=5
    )

    assert validation_size == 7
    boundary = len(dates) - validation_size
    assert dates.iloc[boundary - 1] < dates.iloc[boundary]


def test_complete_date_validation_fails_closed_on_non_chronological_rows() -> None:
    with pytest.raises(ValueError, match="sorted"):
        training.resolve_complete_date_validation_size(
            pd.Series(["2026-01-02", "2026-01-01", "2026-01-03"]),
            validation_size=1,
        )

    with pytest.raises(ValueError, match="at least two label dates"):
        training.resolve_complete_date_validation_size(
            pd.Series(["2026-01-01", "2026-01-01"]),
            validation_size=1,
        )


def test_weight_helper_requires_exact_alignment_and_finite_values() -> None:
    labels = pd.Series([1.0, 2.0], index=[10, 11])
    dates = pd.Series(["2026-01-01", "2026-01-01"], index=[11, 10])
    with pytest.raises(ValueError, match="identical indexes"):
        training.compute_sample_weight(labels, dates, "top_tail_v1")

    with pytest.raises(ValueError, match="finite"):
        training.compute_sample_weight(
            pd.Series([1.0, np.inf], index=[10, 11]),
            pd.Series(["2026-01-01", "2026-01-01"], index=[10, 11]),
            "top_tail_v1",
        )


def test_resolve_training_weights_only_training_partition(monkeypatch) -> None:
    captured: list[dict] = []

    class FakeDataset:
        def __init__(self, *args, **kwargs):
            captured.append(kwargs)

    class FakeModel:
        best_iteration = 2

        def predict(self, values):
            return np.zeros(len(values))

    monkeypatch.setattr(training.lgb, "Dataset", FakeDataset)
    monkeypatch.setattr(training.lgb, "train", lambda *args, **kwargs: FakeModel())
    monkeypatch.setattr(
        training,
        "robust_zscore_transform",
        lambda X, center, scale: X,
    )
    X = pd.DataFrame({"x": range(5)}, dtype=float)
    y = pd.Series(range(5), index=X.index, dtype=float)
    weight = pd.Series([1.0, 2.0, 3.0, 2.0, 3.0], index=X.index)

    training._resolve_train_data(
        X,
        pd.Series([0.0]),
        pd.Series([1.0]),
        y,
        "test",
        "regression",
        3,
        {"objective": "regression"},
        2,
        sample_weight=weight,
    )

    assert len(captured) == 2
    assert captured[0]["weight"].tolist() == [1.0, 2.0, 3.0]
    assert "weight" not in captured[1]


def test_train_model_rejects_misaligned_weights() -> None:
    X = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    y = pd.Series([1.0, 2.0, 3.0], index=X.index)
    with pytest.raises(ValueError, match="exact training index"):
        training.train_model(
            X,
            y,
            "test",
            validation_size=1,
            sample_weight=pd.Series([1.0, 2.0], index=[0, 1]),
        )
