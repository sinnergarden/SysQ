from __future__ import annotations

import pandas as pd

from qsys.feature.freshness import (
    normalise_shareholder_freshness,
    profile_shareholder_feature_freshness,
    shareholder_row_freshness_reasons,
)


def _contract() -> dict:
    return normalise_shareholder_freshness(
        {
            "min_coverage": 0.5,
            "features": {
                "holder_num_stale_days": {"max_median_days": 200, "max_row_days": 365},
                "top10_holder_stale_days": {"max_median_days": 250, "max_row_days": 365},
            },
        }
    )


def test_multi_date_profile_cannot_hide_one_outage_day() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-01-01"] * 2 + ["2026-08-01"] * 2,
            "holder_num_stale_days": [10, 20, 500, 510],
            "top10_holder_stale_days": [10, 20, 500, 510],
        }
    )
    result = profile_shareholder_feature_freshness(
        frame, _contract(), date_column="trade_date"
    )
    assert result["status"] == "fail"
    assert result["features"]["holder_num_stale_days"]["max_daily_median_days"] == 505.0


def test_row_gate_excludes_stale_or_missing_source_values() -> None:
    reasons = shareholder_row_freshness_reasons(
        pd.Series(
            {"holder_num_stale_days": 366, "top10_holder_stale_days": None}
        ),
        _contract(),
    )
    assert reasons == [
        "stale_holder_num_stale_days",
        "missing_top10_holder_stale_days",
    ]
