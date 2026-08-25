from __future__ import annotations

import pandas as pd
import pytest

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


def test_profile_supports_independent_feature_coverage_floors() -> None:
    contract = normalise_shareholder_freshness(
        {
            "min_coverage": 0.95,
            "features": {
                "holder_num_stale_days": {
                    "min_coverage": 0.5,
                    "max_median_days": 200,
                    "max_row_days": 365,
                },
                "top10_holder_stale_days": {
                    "min_coverage": 0.9,
                    "max_median_days": 250,
                    "max_row_days": 365,
                },
            },
        }
    )
    frame = pd.DataFrame(
        {
            "trade_date": ["2026-08-21"] * 4,
            "holder_num_stale_days": [10, 20, None, None],
            "top10_holder_stale_days": [10, 20, 30, None],
        }
    )

    result = profile_shareholder_feature_freshness(
        frame, contract, date_column="trade_date"
    )

    assert result["features"]["holder_num_stale_days"]["min_coverage"] == 0.5
    assert not any(
        "holder_num_stale_days coverage" in item
        for item in result["violations"]
    )
    assert (
        "top10_holder_stale_days coverage=75.00% below 90.00%"
        in result["violations"]
    )


@pytest.mark.parametrize("value", [0, 1.01, "not-a-number"])
def test_feature_coverage_floor_must_be_valid(value) -> None:
    raw = {
        "min_coverage": 0.95,
        "features": {
            "holder_num_stale_days": {
                "min_coverage": value,
                "max_median_days": 200,
                "max_row_days": 365,
            },
            "top10_holder_stale_days": {
                "max_median_days": 250,
                "max_row_days": 365,
            },
        },
    }

    with pytest.raises(ValueError, match="min_coverage"):
        normalise_shareholder_freshness(raw)
