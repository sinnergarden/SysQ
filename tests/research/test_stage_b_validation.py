"""Unit contracts for independent Stage-B metric recomputation."""

from __future__ import annotations

import pandas as pd
import pytest

from qsys.research.stage_b_validation import (
    _compare_daily,
    _recompute_daily_metrics,
)


def test_recompute_daily_metrics_is_independent_and_exact() -> None:
    signal_rows = []
    label_rows = []
    for date, sign in (("2023-01-03", 1.0), ("2023-01-04", -1.0)):
        for value in range(1, 7):
            instrument = f"{value:06d}.SZ"
            signal_rows.append({
                "trade_date": date,
                "instrument": instrument,
                "score": float(value),
            })
            label_rows.append({
                "trade_date": date,
                "instrument": instrument,
                "label_value": sign * float(value),
                "is_valid": not (date == "2023-01-03" and value == 1),
            })

    daily, observation_count = _recompute_daily_metrics(
        pd.DataFrame(signal_rows), pd.DataFrame(label_rows)
    )

    assert observation_count == 11
    assert daily["n"].tolist() == [5, 6]
    assert daily["ic"].tolist() == pytest.approx([1.0, -1.0])
    assert daily["rank_ic"].tolist() == pytest.approx([1.0, -1.0])
    _compare_daily(daily, daily[["date", "ic", "n"]], "ic")
    _compare_daily(daily, daily[["date", "rank_ic", "n"]], "rank_ic")
