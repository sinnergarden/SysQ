"""Contracts for the fixed-alpha rolling ridge baseline."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qsys.research.generators.ridge_single_label import RidgeSingleLabelGenerator
from qsys.research.matrix_job import _create_generator_from_config


def test_ridge_fits_only_chronological_training_partition() -> None:
    index = pd.RangeIndex(40)
    feature = pd.Series(np.linspace(-2.0, 2.0, len(index)), index=index)
    X = pd.DataFrame({"quality": feature})
    y = 3.0 * feature + 0.01 * np.sin(np.arange(len(index)))
    generator = RidgeSingleLabelGenerator(ridge_alpha=1.0)

    model, center, scale = generator._train_window_model(
        X,
        y,
        pd.Series(pd.date_range("2020-01-01", periods=len(index)), index=index),
        window_id="w1",
    )
    prediction = generator._predict_window_model(model, center, scale, X.iloc[-6:])

    assert np.isfinite(prediction).all()
    diagnostics = generator.model_diagnostics_lineage
    assert diagnostics is not None
    window = diagnostics["windows"][0]
    assert window["train_rows"] + window["validation_rows"] == len(index)
    assert window["signed_coefficient"]["quality"] > 0
    assert window["validation_rank_ic"] > 0.99


def test_factory_builds_ridge_and_rejects_irrelevant_tree_params() -> None:
    generator = _create_generator_from_config({
        "generator_id": "ridge",
        "type": "single_label_ridge",
        "params": {"label_id": "fwd_ret_120d_raw_pit_csi1800", "ridge_alpha": 2.0},
    })

    assert isinstance(generator, RidgeSingleLabelGenerator)
    assert generator.ridge_alpha == 2.0
    with pytest.raises(ValueError, match="unknown keys"):
        _create_generator_from_config({
            "generator_id": "ridge",
            "type": "single_label_ridge",
            "params": {
                "label_id": "fwd_ret_120d_raw_pit_csi1800",
                "n_estimators": 100,
            },
        })


def test_ridge_rejects_nonpositive_alpha_and_weighting() -> None:
    with pytest.raises(ValueError, match="ridge_alpha"):
        RidgeSingleLabelGenerator(ridge_alpha=0)
    with pytest.raises(ValueError, match="sample weighting"):
        RidgeSingleLabelGenerator(sample_weight_policy="top_tail_v1")
