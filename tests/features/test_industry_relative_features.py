#!/usr/bin/env python3
"""Test for industry-relative rank features.

Usage:
    python tests/features/test_industry_relative_features.py

Checks:
    1. build_industry_relative_features produces all 11 features
    2. rank is within (trade_date, industry)
    3. group size < 5 produces NaN
    4. missing industry column returns unchanged df
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

pass_count = 0
fail_count = 0


def check(condition: bool, msg: str):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        print(f"  ✅ {msg}")
    else:
        fail_count += 1
        print(f"  ❌ {msg}")


def test_all_11_features():
    """Check that all 11 industry-relative rank features are produced."""
    from qsys.feature.groups.industry_relative_features import (
        build_industry_relative_features,
    )

    # Build a mock with all source columns across 3 dates x 2 industries x 4 stocks
    np.random.seed(42)
    rows = []
    for date in pd.date_range("2023-01-01", periods=3, freq="B"):
        for industry in ["金融", "科技"]:
            for _ in range(10):
                rows.append(
                    {
                        "trade_date": date,
                        "industry": industry,
                        "roe": np.random.randn(),
                        "revenue_yoy": np.random.randn(),
                        "profit_yoy": np.random.randn(),
                        "ocf_margin": np.random.randn(),
                        "pe_ttm": np.random.uniform(5, 50),
                        "pb_raw": np.random.uniform(0.5, 5),
                        "holder_num_chg_qoq": np.random.randn(),
                        "top10_holder_ratio_chg_qoq": np.random.randn(),
                        "margin_crowding_score": np.random.randn(),
                        "ret_60d": np.random.randn() * 0.02,
                        "ret_120d": np.random.randn() * 0.03,
                        "dummy": 1.0,
                    }
                )
    df = pd.DataFrame(rows)

    result = build_industry_relative_features(df)

    expected = [
        "industry_relative_roe",
        "industry_relative_revenue_yoy",
        "industry_relative_profit_yoy",
        "industry_relative_ocf_margin",
        "industry_relative_pe_cheapness",
        "industry_relative_pb_cheapness",
        "industry_relative_holder_chg",
        "industry_relative_top10_chg",
        "industry_relative_margin_crowding",
        "industry_relative_rps_60d",
        "industry_relative_rps_120d",
    ]

    for col in expected:
        check(col in result.columns, f"'{col}' column exists")

    check(len(expected) == 11, "All 11 expected features are defined")

    # Verify values are in [0, 1] range (rank percentile)
    for col in expected:
        vals = result[col].dropna()
        if len(vals) > 0:
            in_range = (vals >= 0).all() and (vals <= 1).all()
            check(in_range, f"'{col}' values in [0, 1] range")


def test_rank_within_group():
    """Rank is computed within (trade_date, industry)."""
    from qsys.feature.groups.industry_relative_features import (
        build_industry_relative_features,
    )

    np.random.seed(1)
    rows = []
    for date in pd.date_range("2023-01-01", periods=2, freq="B"):
        for industry in ["金融", "科技"]:
            for _ in range(10):
                rows.append(
                    {
                        "trade_date": date,
                        "industry": industry,
                        "roe": np.random.randn(),
                        "revenue_yoy": np.random.randn(),
                        "profit_yoy": np.random.randn(),
                        "ocf_margin": np.random.randn(),
                        "pe_ttm": np.random.uniform(5, 50),
                        "pb_raw": np.random.uniform(0.5, 5),
                        "holder_num_chg_qoq": np.random.randn(),
                        "top10_holder_ratio_chg_qoq": np.random.randn(),
                        "margin_crowding_score": np.random.randn(),
                        "ret_60d": np.random.randn() * 0.02,
                        "ret_120d": np.random.randn() * 0.03,
                    }
                )
    df = pd.DataFrame(rows)

    result = build_industry_relative_features(df)
    col = "industry_relative_roe"

    # Within each (trade_date, industry), the rank percentile should sum to ~N*(N+1)/2 / N
    for (d, ind), grp in result.groupby(["trade_date", "industry"]):
        vals = grp[col].dropna()
        if len(vals) >= 5:
            # Rank percentiles are uniformly spaced ~ (0.05, 0.15, ..., 0.95)
            expected_sum = sum((i + 1) / len(vals) for i in range(len(vals)))
            check(
                np.isclose(vals.sum(), expected_sum, atol=0.01),
                f"rank percentile sum matches for {d.date()} / {ind}",
            )


def test_group_smaller_than_5_returns_nan():
    """Group size < 5 produces NaN."""
    from qsys.feature.groups.industry_relative_features import (
        build_industry_relative_features,
    )

    rows = []
    for date in pd.date_range("2023-01-01", periods=1, freq="B"):
        for industry in ["tiny"]:
            for _ in range(3):  # only 3 in this group
                rows.append(
                    {
                        "trade_date": date,
                        "industry": industry,
                        "roe": np.random.randn(),
                        "revenue_yoy": np.random.randn(),
                        "profit_yoy": np.random.randn(),
                        "ocf_margin": np.random.randn(),
                        "pe_ttm": np.random.uniform(5, 50),
                        "pb_raw": np.random.uniform(0.5, 5),
                        "holder_num_chg_qoq": np.random.randn(),
                        "top10_holder_ratio_chg_qoq": np.random.randn(),
                        "margin_crowding_score": np.random.randn(),
                        "ret_60d": np.random.randn() * 0.02,
                        "ret_120d": np.random.randn() * 0.03,
                    }
                )
        # Also add a normal group for comparison
        for _ in range(10):
            rows.append(
                {
                    "trade_date": date,
                    "industry": "normal",
                    "roe": np.random.randn(),
                    "revenue_yoy": np.random.randn(),
                    "profit_yoy": np.random.randn(),
                    "ocf_margin": np.random.randn(),
                    "pe_ttm": np.random.uniform(5, 50),
                    "pb_raw": np.random.uniform(0.5, 5),
                    "holder_num_chg_qoq": np.random.randn(),
                    "top10_holder_ratio_chg_qoq": np.random.randn(),
                    "margin_crowding_score": np.random.randn(),
                    "ret_60d": np.random.randn() * 0.02,
                    "ret_120d": np.random.randn() * 0.03,
                }
            )
    df = pd.DataFrame(rows)
    result = build_industry_relative_features(df)

    col = "industry_relative_roe"
    tiny_mask = result["industry"] == "tiny"
    normal_mask = result["industry"] == "normal"

    # Tiny group should be all NaN for every rank feature
    for feat in ["industry_relative_roe", "industry_relative_rps_60d"]:
        tiny_vals = result.loc[tiny_mask, feat]
        check(
            tiny_vals.isna().all(),
            f"group size 3: '{feat}' all NaN",
        )

    # Normal group should have valid values
    normal_vals = result.loc[normal_mask, col].dropna()
    check(
        len(normal_vals) > 0,
        f"group size 10: '{col}' has non-NaN values",
    )


def test_missing_industry_returns_unchanged():
    """Missing industry column returns unchanged df."""
    from qsys.feature.groups.industry_relative_features import (
        build_industry_relative_features,
    )

    df = pd.DataFrame(
        {
            "trade_date": [pd.Timestamp("2023-01-01")] * 10,
            "roe": np.random.randn(10),
        }
    )
    result = build_industry_relative_features(df)
    check("industry" not in result.columns, "industry column not added")
    check("industry_relative_roe" not in result.columns, "no rank feature added")
    check("roe" in result.columns, "original column preserved")
    check(len(result) == len(df), "row count unchanged")


def main():
    global pass_count, fail_count

    print("\n=== 1. All 11 features produced ===")
    test_all_11_features()

    print("\n=== 2. Rank within (trade_date, industry) ===")
    test_rank_within_group()

    print("\n=== 3. Group size < 5 produces NaN ===")
    test_group_smaller_than_5_returns_nan()

    print("\n=== 4. Missing industry column returns unchanged df ===")
    test_missing_industry_returns_unchanged()

    print(f"\n{'=' * 40}")
    print(f"Results: {pass_count} passed, {fail_count} failed")
    if fail_count > 0:
        sys.exit(1)
    print("All checks passed ✅")


if __name__ == "__main__":
    main()
