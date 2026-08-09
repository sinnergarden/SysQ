from __future__ import annotations

import pandas as pd

from qsys.feature.groups.value_growth_v3a import (
    build_shareholder_features,
    load_shareholder_data,
)


def test_numeric_yyyymmdd_is_parsed_as_announcement_date(tmp_path):
    holder_path = tmp_path / "holder_num.parquet"
    pd.DataFrame(
        {
            "inst": ["AAA", "AAA"],
            "ann_date": [20260115, 20260415],
            "holder_num": [100.0, 80.0],
        }
    ).to_parquet(holder_path)
    daily = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-01-14", "2026-01-16", "2026-04-16"]),
            "ts_code": ["AAA", "AAA", "AAA"],
        }
    )

    loaded = load_shareholder_data(daily, str(holder_path))

    assert pd.isna(loaded.loc[0, "holder_num"])
    assert loaded.loc[1, "holder_num"] == 100.0
    assert loaded.loc[2, "holder_num"] == 80.0
    assert loaded.loc[2, "holder_real_ann_date"] == "2026-04-15"


def test_average_shares_change_does_not_require_total_share():
    frame = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2026-08-07"]),
            "ts_code": ["AAA"],
            "holder_num": [80.0],
            "holder_num_prev_ann": [100.0],
        }
    )

    result = build_shareholder_features(frame)

    assert result.loc[0, "avg_shares_per_holder_chg_qoq"] == 0.25
