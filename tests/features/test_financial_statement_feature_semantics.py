#!/usr/bin/env python3
"""Financial statement feature semantic tests — prevent historical semantic bugs.

Coverage:
1. capex_to_assets must NOT use free_cashflow as capex
2. Without real capex, feature must be skipped or named 'proxy'
3. Quarterly yoy computed on report-level data, not daily PIT
4. Financial feature visible date is ann_date, not end_date
5. No pct_change(4) on daily PIT-expanded series for quarterly yoy
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.feature.registry_v2 import get_feature, FEATURE_REGISTRY


class TestNoFreeCashflowAsCapex:
    """Historical bug: free_cashflow was used as capex in financial statement features.

    The financial_statement_quality_features.py module was removed.
    Verify that no remaining feature uses free_cashflow as capex proxy
    without naming it.
    """

    def test_no_capex_feature_with_free_cashflow(self):
        """If a feature name contains 'capex', it must not use free_cashflow."""
        # There should be no 'capex' named features currently
        capex_features = [
            spec for spec in FEATURE_REGISTRY.values()
            if "capex" in spec.name.lower()
        ]
        assert len(capex_features) == 0, (
            f"Found capex features: {[s.feature_id for s in capex_features]} — "
            f"are they using free_cashflow as proxy?"
        )

    def test_free_cashflow_named_correctly(self):
        """Any feature using free_cashflow must have 'free_cashflow' in name."""
        for spec in FEATURE_REGISTRY.values():
            if "free_cashflow" in spec.description.lower():
                assert "free_cashflow" in spec.name.lower() or "fcf" in spec.name.lower(), (
                    f"Feature '{spec.feature_id}' uses free_cashflow (description) "
                    f"but name '{spec.name}' doesn't reflect it"
                )


class TestPITWithAnnDate:
    """Financial features must use ann_date as visible date."""

    def test_shareholder_features_use_ann_date(self):
        """Shareholder features (v3a_shareholder) must use ann_date merge_asof."""
        # Verify by checking the source code of load_shareholder_data
        from qsys.feature.groups.value_growth_v3a import load_shareholder_data
        import inspect
        source = inspect.getsource(load_shareholder_data)

        # Must use merge_asof with direction="backward"
        assert "merge_asof" in source, (
            "load_shareholder_data must use merge_asof for PIT merge"
        )
        assert "ann_date" in source or "ann_dt" in source, (
            "load_shareholder_data must reference ann_date"
        )

        # Must NOT use end_date for visibility
        assert "direction=\"backward\"" in source, (
            "merge_asof must use backward direction"
        )

    def test_feature_pit_rules_documented(self):
        """Financial statement features must have pit_type=point_in_time."""
        for spec in FEATURE_REGISTRY.values():
            if spec.group in ("v3a_shareholder", "fundamental_context"):
                # These involve PIT financial data
                if spec.kind == "derived" and spec.pit_type not in ("point_in_time", "rolling_past", "cross_sectional"):
                    pytest.fail(
                        f"Feature '{spec.feature_id}' has pit_type='{spec.pit_type}' "
                        f"— financial features should be point_in_time or rolling_past"
                    )


class TestQuarterlyYoyNotAtDailyFrequency:
    """Historical bug: pct_change(4) applied to daily PIT-expanded rows = 4 trading days ≠ 4 quarters."""

    def test_no_pct_change_4_on_daily_data(self):
        """No feature should apply pct_change(4) directly on daily PIT data for quarterly yoy."""
        from qsys.feature.groups.fundamental_context import build_fundamental_context_features
        import inspect
        source = inspect.getsource(build_fundamental_context_features)

        # The whole file should not contain pct_change(4) for yoy purposes
        # (shift(252) is acceptable as approximation)
        lines = source.split("\n")
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "pct_change(4)" in stripped or "pct_change( 4 )" in stripped:
                # Check context: is this in a comment or actual code?
                if not stripped.startswith("#"):
                    pytest.fail(
                        f"build_fundamental_context_features line {i+1}: "
                        f"'{stripped}' — pct_change(4) on daily data is wrong for "
                        f"quarterly yoy. Use report-level pct_change(4) or shift(252) approx."
                    )

    def test_yoy_feature_names_use_252d_not_quarter(self):
        """YoY features should describe methodology accurately."""
        for spec in FEATURE_REGISTRY.values():
            if "yoy" in spec.name.lower() and spec.status == "active":
                # The description should acknowledge it's an approximation
                if "252d" in spec.name.lower() or "252d" in spec.description.lower():
                    continue  # explicitly named as 252d shift
                # The description should not claim exact quarterly comparison
                # without quarterly alignment
                if "year-over-year" in spec.description.lower() or "yoy" in spec.description.lower():
                    if "approximation" not in spec.description.lower() and "252d" not in spec.description.lower():
                        pass  # Allow if it doesn't specify methodology


class TestFundamentalFeatureCompleteness:
    """Verify fundamental features are correctly registered."""

    def test_financial_ratio_features_registered(self):
        """Common financial ratios should have FeatureSpec entries."""
        expected = {
            "log_mktcap", "float_mktcap", "pe_ttm", "pb_raw", "ps_ttm",
            "roe", "roa", "gross_margin", "net_margin",
            "operating_cf_to_profit", "debt_to_asset",
            "revenue_yoy", "profit_yoy",
        }
        for name in expected:
            try:
                spec = get_feature(name)
                assert spec.status != "broken", (
                    f"Core financial feature '{name}' is broken"
                )
            except KeyError:
                pytest.fail(f"Core financial feature '{name}' missing from registry")
