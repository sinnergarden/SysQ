#!/usr/bin/env python3
"""Test for cross-sectional neutralized features via OLS residualization.

Usage:
    python tests/features/test_neutralized_features.py

Checks:
    1. build_neutralized_features produces expected features
    2. mktcap neutral features have near-zero corr with log_mktcap
    3. < 50 samples produces NaN
    4. missing field skips gracefully
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


def test_produces_expected_features():
    """build_neutralized_features produces all expected columns."""
    from qsys.feature.groups.neutralized_features import build_neutralized_features

    np.random.seed(42)
    N = 200
    rows = []
    for date in pd.date_range("2023-01-01", periods=5, freq="B"):
        for _ in range(N):
            rows.append(
                {
                    "trade_date": date,
                    "log_mktcap": np.random.uniform(20, 26),
                    "industry": np.random.choice(["金融", "科技", "医药", "消费", "制造"]),
                    "ret_60d": np.random.randn() * 0.02,
                    "ret_120d": np.random.randn() * 0.03,
                    "roe": np.random.randn() * 0.05 + 0.08,
                    "holder_concentration_score": np.random.randn(),
                    "dummy": 1.0,
                }
            )
    df = pd.DataFrame(rows)

    result = build_neutralized_features(df)

    expected_mktcap = [
        "mktcap_neutral_ret_60d",
        "mktcap_neutral_ret_120d",
        "mktcap_neutral_roe",
        "mktcap_neutral_holder_score",
    ]
    expected_industry_size = [
        "industry_size_neutral_ret_60d",
        "industry_size_neutral_ret_120d",
        "industry_size_neutral_roe",
        "industry_size_neutral_holder_score",
    ]

    for col in expected_mktcap + expected_industry_size:
        check(col in result.columns, f"'{col}' column exists")

    # non-null values exist
    for col in expected_mktcap + expected_industry_size:
        if col in result.columns:
            check(
                result[col].notna().sum() > 0,
                f"'{col}' has non-null values",
            )


def test_mktcap_neutral_zero_corr():
    """Market-cap neutralized features have near-zero correlation with log_mktcap."""
    from qsys.feature.groups.neutralized_features import build_neutralized_features

    np.random.seed(0)
    N = 300
    rows = []
    for date in pd.date_range("2023-01-01", periods=3, freq="B"):
        for _ in range(N):
            rows.append(
                {
                    "trade_date": date,
                    "log_mktcap": np.random.uniform(20, 26),
                    "industry": np.random.choice(["金融", "科技", "医药", "消费", "制造"]),
                    "ret_60d": np.random.randn() * 0.02,
                    "ret_120d": np.random.randn() * 0.03,
                    "roe": np.random.randn() * 0.05 + 0.08,
                    "holder_concentration_score": np.random.randn(),
                }
            )
    df = pd.DataFrame(rows)

    # Inject some mktcap correlation into raw ret_60d
    df["ret_60d"] = df["ret_60d"] + 0.01 * (df["log_mktcap"] - 23)

    result = build_neutralized_features(df)

    for col in [
        "mktcap_neutral_ret_60d",
        "mktcap_neutral_ret_120d",
        "mktcap_neutral_roe",
        "mktcap_neutral_holder_score",
    ]:
        if col not in result.columns:
            continue
        # Per trade-date correlation
        corrs = []
        for _date, grp in result.groupby("trade_date"):
            valid = grp[[col, "log_mktcap"]].dropna()
            if len(valid) > 10:
                c = valid[col].corr(valid["log_mktcap"])
                corrs.append(c)
        if corrs:
            max_abs_corr = max(abs(c) for c in corrs)
            check(
                max_abs_corr < 0.15,
                f"'{col}' max |corr(log_mktcap)| = {max_abs_corr:.4f} < 0.15",
            )
        else:
            check(False, f"'{col}' no valid correlation computed")


def test_less_than_50_samples_returns_nan():
    """Cross-sections with fewer than 50 observations yield NaN for that date."""
    from qsys.feature.groups.neutralized_features import build_neutralized_features

    np.random.seed(0)
    # Build data with one date having only 10 stocks
    rows = []
    for _ in range(10):
        rows.append(
            {
                "trade_date": pd.Timestamp("2023-01-02"),
                "log_mktcap": np.random.uniform(20, 26),
                "industry": "金融",
                "ret_60d": np.random.randn() * 0.02,
                "ret_120d": np.random.randn() * 0.03,
                "roe": np.random.randn() * 0.05 + 0.08,
                "holder_concentration_score": np.random.randn(),
            }
        )
    for _ in range(100):
        rows.append(
            {
                "trade_date": pd.Timestamp("2023-01-03"),
                "log_mktcap": np.random.uniform(20, 26),
                "industry": "科技",
                "ret_60d": np.random.randn() * 0.02,
                "ret_120d": np.random.randn() * 0.03,
                "roe": np.random.randn() * 0.05 + 0.08,
                "holder_concentration_score": np.random.randn(),
            }
        )
    df = pd.DataFrame(rows)
    result = build_neutralized_features(df)

    # Jan 2 (small) should be all NaN for neutralized features
    small_mask = result["trade_date"] == pd.Timestamp("2023-01-02")
    big_mask = result["trade_date"] == pd.Timestamp("2023-01-03")

    for col in ["mktcap_neutral_ret_60d", "mktcap_neutral_ret_120d",
                 "mktcap_neutral_roe", "mktcap_neutral_holder_score"]:
        if col in result.columns:
            check(
                result.loc[small_mask, col].isna().all(),
                f"'{col}' all NaN for date with 10 samples",
            )
            check(
                result.loc[big_mask, col].notna().any(),
                f"'{col}' has valid values for date with 100 samples",
            )


def test_missing_field_skips_gracefully():
    """Missing source columns are silently skipped."""
    from qsys.feature.groups.neutralized_features import build_neutralized_features

    # No ret_60d, ret_120d — only roe
    df = pd.DataFrame(
        {
            "trade_date": pd.date_range("2023-01-01", periods=3, freq="B").repeat(100),
            "log_mktcap": np.random.uniform(20, 26, 300),
            "industry": np.random.choice(["金融", "科技"], 300),
            "roe": np.random.randn(300) * 0.05 + 0.08,
        }
    )
    result = build_neutralized_features(df)

    check(
        "mktcap_neutral_roe" in result.columns,
        "mktcap_neutral_roe present",
    )
    check(
        "mktcap_neutral_ret_60d" not in result.columns,
        "mktcap_neutral_ret_60d absent (no input)",
    )
    check(
        "mktcap_neutral_holder_score" not in result.columns,
        "mktcap_neutral_holder_score absent (no input)",
    )

    # Missing log_mktcap entirely
    df2 = pd.DataFrame(
        {
            "trade_date": pd.date_range("2023-01-01", periods=3, freq="B").repeat(100),
            "ret_60d": np.random.randn(300),
        }
    )
    result2 = build_neutralized_features(df2)
    check(
        "log_mktcap" not in result2.columns,
        "log_mktcap not added",
    )
    check(
        "mktcap_neutral_ret_60d" not in result2.columns,
        "no neutral features when log_mktcap missing",
    )
    check(
        "ret_60d" in result2.columns,
        "original columns preserved",
    )


def main():
    global pass_count, fail_count

    print("\n=== 1. Expected features produced ===")
    test_produces_expected_features()

    print("\n=== 2. Mktcap-neutral features have near-zero corr with log_mktcap ===")
    test_mktcap_neutral_zero_corr()

    print("\n=== 3. < 50 samples produces NaN ===")
    test_less_than_50_samples_returns_nan()

    print("\n=== 4. Missing field skips gracefully ===")
    test_missing_field_skips_gracefully()

    print(f"\n{'=' * 40}")
    print(f"Results: {pass_count} passed, {fail_count} failed")
    if fail_count > 0:
        sys.exit(1)
    print("All checks passed ✅")


if __name__ == "__main__":
    main()
