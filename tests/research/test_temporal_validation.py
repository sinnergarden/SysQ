from __future__ import annotations

import pandas as pd

from qsys.research.generators.temporal_validation import (
    TemporalValidationLightGBMSingleLabelGenerator,
    chronological_tail_partition,
)
from qsys.research.matrix_job import _create_generator_from_config


def test_partition_is_chronological_and_never_splits_a_date() -> None:
    rows = []
    for instrument in ("C", "A", "B"):
        for date in pd.date_range("2024-01-02", periods=10, freq="B"):
            rows.append((instrument, date))
    index = pd.RangeIndex(len(rows))
    X = pd.DataFrame({"x": range(len(rows))}, index=index)
    y = pd.Series(range(len(rows)), index=index, dtype=float)
    dates = pd.Series([date for _, date in rows], index=index)

    ordered_X, ordered_y, ordered_dates, validation_size, start, end = (
        chronological_tail_partition(
            X, y, dates, target_validation_rows=7
        )
    )

    assert ordered_X.index.equals(ordered_y.index)
    assert ordered_y.index.equals(ordered_dates.index)
    assert validation_size == 9
    assert start == "2024-01-11"
    assert end == "2024-01-15"
    assert ordered_dates.iloc[:-validation_size].max() < ordered_dates.iloc[
        -validation_size:
    ].min()
    assert set(ordered_dates.iloc[-validation_size:].dt.strftime("%Y-%m-%d")) == {
        "2024-01-11", "2024-01-12", "2024-01-15"
    }


def test_factory_builds_temporal_lightgbm() -> None:
    generator = _create_generator_from_config({
        "generator_id": "temporal_lgbm",
        "type": "single_label_lightgbm_temporal",
        "params": {"label_id": "fwd_ret_120d_raw"},
    })
    assert isinstance(generator, TemporalValidationLightGBMSingleLabelGenerator)
