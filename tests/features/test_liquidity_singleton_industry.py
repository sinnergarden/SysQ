from __future__ import annotations

import pandas as pd

from qsys.feature.groups.liquidity import build_liquidity_features


def test_singleton_industry_zscores_are_neutral_not_missing() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["A", "B", "C"],
            "trade_date": ["2026-08-07"] * 3,
            "industry": ["singleton", "pair", "pair"],
            "close": [10.0, 10.0, 11.0],
            "volume": [100.0, 200.0, 300.0],
            "amount": [1000.0, 2000.0, 3000.0],
            "turnover_rate": [1.0, 2.0, 3.0],
        }
    )
    result = build_liquidity_features(frame)
    singleton = result[result["ts_code"] == "A"].iloc[0]
    assert singleton["amount_log_ind_zscore"] == 0.0
    assert singleton["turnover_rate_ind_zscore"] == 0.0
