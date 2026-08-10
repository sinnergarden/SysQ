from __future__ import annotations

import pandas as pd

from qsys.feature.availability import (
    apply_margin_source_lag,
    normalise_feature_availability,
    resolve_lagged_open_session,
)


def test_margin_lag_uses_exact_previous_panel_session() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": [
                "2026-04-01",
                "2026-04-02",
                "2026-04-03",
                "2026-04-01",
                "2026-04-03",
            ],
            "ts_code": ["AAA", "AAA", "AAA", "BBB", "BBB"],
            "margin_balance": [10.0, 20.0, 30.0, 100.0, 300.0],
        }
    )

    result = apply_margin_source_lag(frame, lag_sessions=1)

    aaa = result[result["ts_code"] == "AAA"].reset_index(drop=True)
    bbb = result[result["ts_code"] == "BBB"].reset_index(drop=True)
    assert pd.isna(aaa.loc[0, "margin_balance"])
    assert aaa.loc[1, "margin_balance"] == 10.0
    assert aaa.loc[2, "margin_balance"] == 20.0
    assert pd.isna(bbb.loc[1, "margin_balance"])


def test_previous_open_session_resolver_skips_weekend() -> None:
    assert resolve_lagged_open_session(
        "2026-08-10",
        ["2026-08-06", "2026-08-07", "2026-08-10"],
        1,
    ) == "2026-08-07"


def test_financial_rc_availability_contract_is_canonical() -> None:
    assert normalise_feature_availability(
        {"margin": {"source": "tushare.margin_detail", "lag_sessions": 1}}
    ) == {
        "margin": {
            "source": "tushare.margin_detail",
            "lag_sessions": 1,
            "availability_rule": "previous_open_session",
        }
    }
