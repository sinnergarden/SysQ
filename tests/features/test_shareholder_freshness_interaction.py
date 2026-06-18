#!/usr/bin/env python3
"""Test for shareholder freshness and interaction features.

Usage:
    python tests/features/test_shareholder_freshness_interaction.py

Checks:
    1. decay decreases with stale_days
    2. missing optional field doesn't crash
    3. interactions produce non-null values
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


def _make_mock_df(n_stocks: int = 20, n_dates: int = 10) -> pd.DataFrame:
    """Create a mock DataFrame suitable for shareholder freshness tests."""
    np.random.seed(42)
    rows = []
    dates = pd.date_range("2023-01-01", periods=n_dates, freq="B")
    for date in dates:
        for i in range(n_stocks):
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": f"{i:06d}.SZ",
                    "holder_concentration_score": np.random.randn(),
                    "holder_squeeze_score": np.random.randn(),
                    "holder_num_stale_days": float(np.random.randint(0, 180)),
                    "top10_holder_stale_days": float(np.random.randint(0, 180)),
                    "pe_repair_room_to_median": np.random.randn(),
                    "pb_repair_room_to_median": np.random.randn(),
                    "revenue_yoy_accel": np.random.randn(),
                    "profit_yoy_accel": np.random.randn(),
                    "industry_relative_rps_120d": np.random.uniform(0, 1),
                }
            )
    return pd.DataFrame(rows)


def test_decay_decreases_with_stale_days():
    """Decay weight decreases monotonically as stale_days increases."""
    from qsys.feature.groups.shareholder_freshness_and_interaction import (
        build_shareholder_freshness_and_interaction_features,
    )

    df = _make_mock_df()
    result = build_shareholder_freshness_and_interaction_features(df)

    check("holder_decay_weight" in result.columns, "holder_decay_weight present")
    check("top10_decay_weight" in result.columns, "top10_decay_weight present")

    # Verify decay = exp(-stale_days / 60) is strictly decreasing
    for col_name, stale_col in [
        ("holder_decay_weight", "holder_num_stale_days"),
        ("top10_decay_weight", "top10_holder_stale_days"),
    ]:
        if col_name not in result.columns or stale_col not in result.columns:
            continue
        # Sample a few pairs where stale_days differ
        sampled = result[[stale_col, col_name]].dropna().sample(n=min(50, len(result)), random_state=0)
        for _, row in sampled.iterrows():
            expected = np.exp(-row[stale_col] / 60.0)
            check(
                np.isclose(row[col_name], expected, atol=1e-10),
                f"'{col_name}' = {row[col_name]:.6f} matches exp(-{stale_col}/60) = {expected:.6f}",
            )
            break  # one check is sufficient

    # Monotonicity: larger stale_days -> smaller decay
    for stale_col, decay_col in [
        ("holder_num_stale_days", "holder_decay_weight"),
        ("top10_holder_stale_days", "top10_decay_weight"),
    ]:
        if decay_col not in result.columns:
            continue
        pairs = result[[stale_col, decay_col]].dropna().drop_duplicates(subset=[stale_col])
        monotonic = all(
            pairs[decay_col].iloc[i] > pairs[decay_col].iloc[i + 1] or np.isclose(pairs[decay_col].iloc[i], pairs[decay_col].iloc[i + 1])
            for i in range(len(pairs) - 1)
            if pairs[stale_col].iloc[i] < pairs[stale_col].iloc[i + 1]
        )
        check(monotonic, f"'{decay_col}' decreases as stale_days increases")


def test_missing_optional_field_does_not_crash():
    """Missing fields like valuation_repair_score, growth_accel_score don't crash."""
    from qsys.feature.groups.shareholder_freshness_and_interaction import (
        build_shareholder_freshness_and_interaction_features,
    )

    # Minimal input: only required fields, no optional overrides
    df = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=5, freq="B").repeat(10),
        "ts_code": [f"{i:06d}.SZ" for i in range(10)] * 5,
        "holder_concentration_score": np.random.randn(50),
        "holder_squeeze_score": np.random.randn(50),
        "holder_num_stale_days": np.random.uniform(0, 180, 50),
        "top10_holder_stale_days": np.random.uniform(0, 180, 50),
        "pe_repair_room_to_median": np.random.randn(50),
        "pb_repair_room_to_median": np.random.randn(50),
        "revenue_yoy_accel": np.random.randn(50),
        "profit_yoy_accel": np.random.randn(50),
        "industry_relative_rps_120d": np.random.uniform(0, 1, 50),
    })

    try:
        result = build_shareholder_freshness_and_interaction_features(df)
        check(True, "no crash with minimal input (no optional overrides)")
        check("holder_decay_weight" in result.columns, "decay weight present")
        check("holder_concentration_score_decay" in result.columns, "decay score present")
        check("fresh_holder_signal_40d" in result.columns, "fresh signal present")
    except Exception as e:
        check(False, f"crashed with minimal input: {e}")

    # Even more minimal: no stale_days columns at all
    df2 = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=3, freq="B").repeat(10),
        "ts_code": [f"{i:06d}.SZ" for i in range(10)] * 3,
        "holder_concentration_score": np.random.randn(30),
        "pe_repair_room_to_median": np.random.randn(30),
        "pb_repair_room_to_median": np.random.randn(30),
    })

    try:
        result2 = build_shareholder_freshness_and_interaction_features(df2)
        check(True, "no crash when stale_days columns are missing")
        check("holder_decay_weight" not in result2.columns, "no decay weight without stale_days")
    except Exception as e:
        check(False, f"crashed without stale_days: {e}")


def test_interactions_produce_non_null():
    """Interaction features produce non-null values."""
    from qsys.feature.groups.shareholder_freshness_and_interaction import (
        build_shareholder_freshness_and_interaction_features,
    )

    df = _make_mock_df(n_stocks=30, n_dates=10)
    result = build_shareholder_freshness_and_interaction_features(df)

    interaction_cols = [
        "holder_concentration_x_value",
        "holder_concentration_x_growth",
        "holder_concentration_x_industry_rps",
    ]

    for col in interaction_cols:
        check(col in result.columns, f"'{col}' column exists")

    for col in interaction_cols:
        if col in result.columns:
            vals = result[col].dropna()
            check(
                len(vals) > 0,
                f"'{col}' has non-null values ({len(vals)} / {len(result)})",
            )

    # Verify no inf in interaction features
    for col in interaction_cols:
        if col in result.columns:
            has_inf = np.isinf(result[col].dropna()).any()
            check(not has_inf, f"'{col}' has no inf values")


def main():
    global pass_count, fail_count

    print("\n=== 1. Decay decreases with stale_days ===")
    test_decay_decreases_with_stale_days()

    print("\n=== 2. Missing optional field doesn't crash ===")
    test_missing_optional_field_does_not_crash()

    print("\n=== 3. Interactions produce non-null values ===")
    test_interactions_produce_non_null()

    print(f"\n{'=' * 40}")
    print(f"Results: {pass_count} passed, {fail_count} failed")
    if fail_count > 0:
        sys.exit(1)
    print("All checks passed ✅")


if __name__ == "__main__":
    main()
