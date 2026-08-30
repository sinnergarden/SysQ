from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.feature.groups.growth_confirmation_v0 import _required_income_symbols
from qsys.feature.groups.relative_strength import build_relative_strength_features
from qsys.feature.groups.industry_momentum_features import (
    build_industry_momentum_features,
)
from qsys.feature.builder import build_phase1_features
from qsys.feature.config import RESEARCH_FEATURE_FLAGS
from qsys.feature.transforms import apply_cross_sectional_standardization


def test_rolling_history_is_continuous_but_rps_uses_member_cross_section() -> None:
    dates = pd.bdate_range("2023-01-02", periods=25)
    rows: list[dict[str, object]] = []
    for instrument, closes in (
        ("AAA", np.linspace(10.0, 30.0, len(dates))),
        ("BBB", np.full(len(dates), 20.0)),
    ):
        for position, (trade_date, close) in enumerate(zip(dates, closes, strict=True)):
            rows.append(
                {
                    "ts_code": instrument,
                    "trade_date": trade_date,
                    "close": close,
                    "volume": 100.0,
                    "amount": 1_000.0,
                    "industry": "I",
                    "_pit_member": instrument == "BBB" or position == len(dates) - 1,
                }
            )
    frame = pd.DataFrame(rows)

    built = build_relative_strength_features(frame)
    final_date = dates[-1]
    final = built[built["trade_date"] == final_date].set_index("ts_code")

    assert np.isfinite(final.loc["AAA", "ret_20d"])
    assert "_pit_member" in built.columns
    assert final.loc["AAA", "rps_20d"] == 1.0
    assert final.loc["BBB", "rps_20d"] == 0.5
    assert built.loc[
        (built["ts_code"] == "AAA") & (built["trade_date"] < final_date),
        "rps_20d",
    ].isna().all()


def test_cross_sectional_standardization_excludes_nonmembers() -> None:
    frame = pd.DataFrame(
        {
            "trade_date": ["2023-01-03"] * 3,
            "instrument": ["AAA", "FUTURE", "BBB"],
            "value": [1.0, 100.0, 3.0],
            "_pit_member": [True, False, True],
        }
    )

    result = apply_cross_sectional_standardization(frame, ["value"])

    assert result.loc[0, "value_rank"] == 0.5
    assert pd.isna(result.loc[1, "value_rank"])
    assert result.loc[2, "value_rank"] == 1.0


def test_pit_mask_survives_phase1_until_final_standardization() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["AAA", "FUTURE", "BBB"],
            "trade_date": pd.to_datetime(["2023-01-03"] * 3),
            "close": [10.0, 100.0, 20.0],
            "volume": [100.0, 10_000.0, 200.0],
            "amount": [1_000.0, 1_000_000.0, 2_000.0],
            "turnover_rate": [0.01, 0.9, 0.02],
            "industry": ["I", "I", "I"],
            "_pit_member": [True, False, True],
        }
    )

    built = build_phase1_features(
        frame,
        flags={
            **{key: False for key in RESEARCH_FEATURE_FLAGS},
            "enable_liquidity_features": True,
            "enable_relative_strength_features": True,
        },
    )

    assert "_pit_member" in built.columns
    assert pd.isna(built.loc[1, "amount_log"])
    assert built.loc[[0, 2], "amount_log"].notna().all()


def test_income_scope_gate_requires_only_pit_consumed_symbols() -> None:
    frame = pd.DataFrame(
        {
            "ts_code": ["MEMBER", "HISTORICAL", "MEMBER", "FUTURE"],
            "_pit_member": [True, False, True, pd.NA],
        }
    )

    assert _required_income_symbols(frame) == {"MEMBER"}


def test_income_scope_gate_preserves_all_symbols_without_pit_mask() -> None:
    frame = pd.DataFrame({"ts_code": ["AAA", "BBB", "AAA"]})

    assert _required_income_symbols(frame) == {"AAA", "BBB"}


def test_industry_aggregation_excludes_nonmember_union_history() -> None:
    dates = pd.bdate_range("2023-01-02", periods=30)
    rows: list[dict[str, object]] = []
    for instrument, daily_return, member in (
        ("AAA", 0.01, True),
        ("BBB", 0.01, True),
        ("FUTURE", 0.50, False),
    ):
        closes = 100.0 * np.cumprod(np.full(len(dates), 1.0 + daily_return))
        rows.extend(
            {
                "ts_code": instrument,
                "trade_date": date,
                "close": close,
                "amount": 1_000.0,
                "industry": "I",
                "_pit_member": member,
            }
            for date, close in zip(dates, closes, strict=True)
        )

    built = build_industry_momentum_features(pd.DataFrame(rows))
    final_members = built.loc[
        built["trade_date"].eq(dates[-1]) & built["_pit_member"],
        "industry_ret_20d",
    ]

    assert np.allclose(final_members, 0.01)
