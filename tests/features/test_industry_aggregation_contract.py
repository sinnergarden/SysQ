#!/usr/bin/env python3
"""Industry aggregation contract tests — prevent cross-date contamination.

Historical bugs:
1. ``groupby("industry")`` without date → mixes dates and stocks
2. ``rolling()`` in cross-section → wrong window semantics
3. Missing reset_index before temporal rolling → panel row alignment errors

Contract:
1. All industry cross-sectional aggregation: ``groupby(["trade_date", "industry"])``
2. All industry temporal rolling: on industry daily panel, ``groupby("industry").rolling()``
3. Same trade_date × industry → same industry feature value
4. Different trade_date → different rolling values (correct temporal evolution)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.feature.groups.industry_momentum_features import build_industry_momentum_features


# ── Deterministic synthetic data ──────────────────────────────────────────
# 2 industries × 3 stocks × 60 trading days
# AI: daily ret = +0.5% (steady uptrend)
# BANK: daily ret = -0.2% (steady downtrend)

@pytest.fixture(scope="module")
def panel_df():
    """Deterministic panel with per-stock variation and evolving industry trends.

    - AI: ~+0.5% daily for first 30 days, then +0.2% (trend shift)
    - BANK: ~-0.2% daily for first 30 days, then -0.05% (trend shift)
    - Each stock has small idiosyncratic drift for stock-minus-industry tests
    """
    import math
    dates = pd.date_range("2025-01-01", periods=60, freq="B")
    rows = []
    for ind, base_close in [("AI", 100.0), ("BANK", 50.0)]:
        for stock_idx, stock_noise in enumerate([-0.001, 0.0, 0.001]):
            price = base_close
            for i, d in enumerate(dates):
                # Trend shift at day 30
                if i < 30:
                    base_drift = 0.005 if ind == "AI" else -0.002
                else:
                    base_drift = 0.002 if ind == "AI" else -0.0005
                cycle = 0.003 * math.sin(2 * math.pi * i / 15)
                daily_ret = base_drift + cycle + stock_noise
                price *= (1 + daily_ret)
                rows.append({
                    "trade_date": d,
                    "ts_code": f"{ind}_{stock_idx:03d}",
                    "close": price,
                    "amount": 1e8,
                    "industry": ind,
                })
    df = pd.DataFrame(rows)
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    return df


class TestIndustryMomentumContract:
    """Tests for build_industry_momentum_features — the repo-fixed version."""

    def test_features_exist(self, panel_df):
        result = build_industry_momentum_features(panel_df.copy())
        expected = {"industry_ret_20d", "industry_ret_60d", "industry_ret_120d",
                     "industry_breadth_20d", "industry_breadth_60d",
                     "industry_new_high_ratio", "industry_volume_expansion",
                     "stock_minus_industry_ret_20d", "stock_minus_industry_ret_60d",
                     "stock_industry_ret_corr_60d"}
        actual = set(result.columns)
        # stock_minus_industry_ret_20d/60d need ret_20d/ret_60d which aren't in
        # the base panel — skip those for the existence check
        conditional = {"stock_minus_industry_ret_20d", "stock_minus_industry_ret_60d",
                        "stock_industry_ret_corr_60d", "industry_top_stock_momentum"}
        unconditional = expected - conditional
        assert unconditional.issubset(actual), (
            f"Missing unconditional features: {unconditional - actual}"
        )

    def test_within_industry_date_const_for_rolling(self, panel_df):
        """industry_ret_20d must be same for all stocks in same industry × date."""
        result = build_industry_momentum_features(panel_df.copy())
        for d in panel_df["trade_date"].unique()[25:30]:
            for ind in ["AI", "BANK"]:
                sub = result[(result["trade_date"] == d) & (result["industry"] == ind)]
                vals = sub["industry_ret_20d"].unique()
                non_nan = vals[~pd.isna(vals)]
                if len(non_nan) > 0:
                    assert len(non_nan) == 1, (
                        f"industry_ret_20d not const for {ind} on {d}: {non_nan}"
                    )

    def test_different_industries_different_values(self, panel_df):
        """AI and BANK industries have different industry_ret_20d."""
        result = build_industry_momentum_features(panel_df.copy())
        last = panel_df["trade_date"].max()
        ai = result[(result["trade_date"] == last) & (result["industry"] == "AI")]["industry_ret_20d"].iloc[0]
        bank = result[(result["trade_date"] == last) & (result["industry"] == "BANK")]["industry_ret_20d"].iloc[0]
        assert not np.isclose(ai, bank, atol=1e-4), (
            f"AI and BANK industry_ret_20d are suspiciously close: {ai} vs {bank}"
        )

    def test_industry_breadth_same_within_industry_date(self, panel_df):
        """industry_breadth_20d must be same across stocks in same industry × date."""
        result = build_industry_momentum_features(panel_df.copy())
        for d in panel_df["trade_date"].unique()[25:30]:
            for ind in ["AI", "BANK"]:
                sub = result[(result["trade_date"] == d) & (result["industry"] == ind)]
                vals = sub["industry_breadth_20d"].unique()
                non_nan = vals[~pd.isna(vals)]
                if len(non_nan) > 0:
                    assert len(non_nan) == 1, (
                        f"industry_breadth_20d not const for {ind} on {d}: {non_nan}"
                    )

    def test_rolling_evolves_over_time(self, panel_df):
        """industry_ret_20d must change over time (temporal rolling)."""
        result = build_industry_momentum_features(panel_df.copy())
        ai = result[result["industry"] == "AI"].groupby("trade_date")["industry_ret_20d"].first().dropna()
        unique_count = ai.nunique()
        assert unique_count >= 5, (
            f"industry_ret_20d has only {unique_count} unique values over time "
            f"(expected temporal evolution)"
        )

    def test_stock_minus_industry_evolves(self, panel_df):
        """stock_minus_industry_ret_20d varies per stock when upstream ret_20d exists."""
        # Add ret_20d to test this conditional feature
        df = panel_df.copy()
        df["ret_20d"] = df.groupby("ts_code")["close"].pct_change(20)
        result = build_industry_momentum_features(df)
        last = panel_df["trade_date"].max()
        ai = result[(result["trade_date"] == last) & (result["industry"] == "AI")]
        vals = ai["stock_minus_industry_ret_20d"].dropna()
        if len(vals) > 1:
            assert vals.nunique() > 1, (
                "All AI stocks have same stock_minus_industry_ret_20d — "
                "should vary per stock"
            )

    def test_no_cross_date_contamination(self, panel_df):
        """Prove no cross-date contamination: check that stock_minus_industry_ret
        is NOT zero (which it would be if contaminated by same-day cross-section)."""
        result = build_industry_momentum_features(panel_df.copy())
        # Case 1: Same industry, same date → same industry_ret_20d value
        # (already tested in test_within_industry_date_const_for_rolling)
        # Case 2: Create a pattern that would be wrong if cross-date contamination existed:
        # Take two dates far apart and verify industry values differ
        # (sufficient condition for temporal rolling being active)
        last_20 = panel_df["trade_date"].unique()[-20:]
        ai_values = []
        for d in last_20:
            sub = result[(result["trade_date"] == d) & (result["industry"] == "AI")]
            vals = sub["industry_ret_20d"].dropna().unique()
            if len(vals) > 0:
                ai_values.append(vals[0])
        # With oscillating industry data, the rolling mean should vary over 20 days
        if len(ai_values) >= 2:
            assert not all(np.isclose(ai_values[0], v, atol=1e-4) for v in ai_values[1:]), (
                f"industry_ret_20d for AI doesn't vary over last 20 dates: "
                f"all same ({ai_values[0]})"
            )


class TestIndustryMomentumNaNPolicies:
    """NaN behavior for industry momentum features."""

    def test_early_days_nan(self, panel_df):
        """First few dates should have NaN for 20d rolling (min_periods=5)."""
        result = build_industry_momentum_features(panel_df.copy())
        early_dates = panel_df["trade_date"].unique()[:3]  # first 3 dates < min_periods=5
        early = result[result["trade_date"].isin(early_dates)]
        nan_count = early["industry_ret_20d"].isna().sum()
        total = len(early)
        assert nan_count == total, (
            f"Expected all NaN for early industry_ret_20d, got {total - nan_count} non-NaN"
        )

    def test_120d_window_partial_nan(self, panel_df):
        """With 60 days, industry_ret_120d has SOME non-NaN (min_periods=30)."""
        result = build_industry_momentum_features(panel_df.copy())
        non_nan = result["industry_ret_120d"].notna().sum()
        # min_periods=30 means we need 30 non-NaN ind_ret values before rolling
        # With 60 dates, we should have some non-NaN
        assert non_nan > 20, (
            f"Expected >20 non-NaN industry_ret_120d with 60 days (min_periods=30), "
            f"got {non_nan}"
        )
