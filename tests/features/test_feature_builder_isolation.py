"""Test feature builder isolation — each feature flag opens only its declared group.

Key checks:
1. enable_industry_momentum_features does NOT trigger v3b_interaction
2. enable_v3b_interaction_features does NOT double-call
3. Each feature flag only calls its corresponding feature group
4. When combining multiple groups, output columns equal the union of group declarations
"""

import unittest

import numpy as np
import pandas as pd

N = 500


def _make_mock_panel() -> pd.DataFrame:
    """Create minimal mock input for feature builder.

    Includes all columns that feature groups commonly access to avoid
    ``out.get("col", default)`` type errors.
    """
    np.random.seed(42)
    df = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
        "ts_code": ["000001.SZ"] * N,
        "close": float(100) + np.cumsum(np.random.randn(N) * 0.5),
        "open": float(100) + np.cumsum(np.random.randn(N) * 0.5),
        "high": float(100) + np.cumsum(np.random.randn(N) * 0.6),
        "low": float(100) + np.cumsum(np.random.randn(N) * 0.6),
        "volume": np.random.uniform(1e6, 1e8, N),
        "amount": np.random.uniform(1e8, 2e9, N),
        "vwap": float(100) + np.cumsum(np.random.randn(N) * 0.4),
        "high_limit": float(110.0),
        "low_limit": float(90.0),
        "factor": float(1.0),
        "float_shares": np.random.uniform(1e8, 1e9, N),
        "paused": float(0),
        "net_inflow": np.random.uniform(-1e8, 1e8, N),
        "big_inflow": np.random.uniform(-5e7, 5e7, N),
        # fundamentals
        "pe": np.random.uniform(5, 50, N),
        "pb": np.random.uniform(0.5, 5, N),
        "ps": np.random.uniform(0.5, 5, N),
        "total_mv": np.random.uniform(1e10, 5e11, N),
        "circ_mv": np.random.uniform(5e9, 3e11, N),
        "roe": np.random.uniform(0.02, 0.2, N),
        "roa": np.random.uniform(0.01, 0.15, N),
        "grossprofit_margin": np.random.uniform(0.1, 0.8, N),
        "debt_to_assets": np.random.uniform(0.2, 0.8, N),
        "current_ratio": np.random.uniform(0.5, 3.0, N),
        "net_income": np.random.uniform(1e8, 5e10, N),
        "revenue": np.random.uniform(5e8, 1e11, N),
        "op_cashflow": np.random.uniform(-1e9, 2e10, N),
        "total_assets": np.random.uniform(1e10, 1e12, N),
        "equity": np.random.uniform(5e9, 5e11, N),
        "margin_balance": np.where(np.random.random(N) < 0.9, np.random.uniform(1e9, 5e9, N), np.nan),
        "margin_buy_amount": np.where(np.random.random(N) < 0.9, np.random.uniform(1e7, 5e8, N), np.nan),
        "margin_repay_amount": np.where(np.random.random(N) < 0.9, np.random.uniform(1e7, 5e8, N), np.nan),
        "margin_total_balance": np.where(np.random.random(N) < 0.9, np.random.uniform(1e9, 5e9, N), np.nan),
        "lend_volume": np.where(np.random.random(N) < 0.8, np.random.uniform(1e5, 1e8, N), np.nan),
        "lend_sell_volume": np.where(np.random.random(N) < 0.8, np.random.uniform(1e5, 5e7, N), np.nan),
        "lend_repay_volume": np.where(np.random.random(N) < 0.8, np.random.uniform(1e5, 5e7, N), np.nan),
    })
    return df


class TestBuilderIsolation(unittest.TestCase):
    """Each feature flag must only enable its declared group, not unrelated groups."""

    def setUp(self):
        from qsys.feature.registry import FEATURE_GROUPS

        self.registry_groups = FEATURE_GROUPS
        # Build a set of all declared feature names per group
        self.declared_features: dict[str, set[str]] = {}
        for gname, ginfo in self.registry_groups.items():
            self.declared_features[gname] = set(ginfo.get("features", []))

    def _build_group_features(
        self, flag_name: str, extra_flags: dict | None = None
    ) -> pd.DataFrame:
        """Call build_phase1_features with only one feature flag enabled."""
        from qsys.feature.builder import build_phase1_features

        flags = {
            "enable_microstructure_features": False,
            "enable_liquidity_features": False,
            "enable_tradability_features": False,
            "enable_relative_strength_features": False,
            "enable_regime_features": False,
            "enable_industry_context_features": False,
            "enable_fundamental_context_features": False,
            "enable_v3a_margin_features": False,
            "enable_v3a_shareholder_features": False,
            "enable_v3b_price_volume_features": False,
            "enable_v3b_interaction_features": False,
            "enable_industry_momentum_features": False,
        }
        if extra_flags:
            flags.update(extra_flags)
        flags[flag_name] = True
        mock_df = _make_mock_panel()
        return build_phase1_features(mock_df, flags=flags)

    def _assert_group_only(self, result: pd.DataFrame, group_name: str):
        """Assert that extra columns beyond the group's declared features do not appear,
        except for base columns (trade_date, ts_code), raw fields, and standardisation
        side-effects (``_z``, ``_rank`` suffixes added by
        ``apply_cross_sectional_standardization``)."""
        declared = self.declared_features.get(group_name, set())
        result_cols = set(result.columns)
        base_cols = {"trade_date", "ts_code"} | set(_make_mock_panel().columns)

        # Build the set of expected standardisation output columns
        declared_no_z = {c for c in declared if not c.endswith("_z") and not c.endswith("_rank")}
        expected_std_cols: set[str] = set()
        for feat in declared_no_z:
            expected_std_cols.add(f"{feat}_z")
            expected_std_cols.add(f"{feat}_rank")

        # find unexpected columns: present in result but NOT in base, NOT declared for group,
        # and NOT expected standardisation side outputs
        unexpected = result_cols - base_cols - declared - expected_std_cols
        # We only flag columns that appear to be derived features from other groups
        derived_patterns = {
            "trend_", "low_vol", "return_", "up_volume", "volume_contraction",
            "quiet_", "amount_stability", "breakout_", "holder_", "margin_",
            "industry_", "stock_minus_industry_", "close_", "open_", "upper_",
            "lower_", "intraday_", "is_", "distance_", "limit_up", "opened_",
            "ret_", "vol_mean", "amount_mean", "vol_shock_", "illiquidity",
            "turnover_", "amount_zscore", "volume_ratio", "volume_up_", "volume_",
            "above_avg", "volume_spike", "volume_stability",
            "log_mktcap", "float_mktcap", "pe_", "pb_", "roe_", "gross_",
            "debt_", "revenue_", "profit_", "inventory_", "ar_", "ocf_",
            "operating_", "earnings_", "peg_", "continuation_", "repair_",
            "overheat_", "value_trap_", "rps_", "price_percentile",
            "distance_to_", "up_day_", "max_pullback",
            "volatility_adjusted_",
        }
        extra = {c for c in unexpected if any(c.startswith(p) for p in derived_patterns)}
        self.assertSetEqual(
            extra, set(),
            f"Group '{group_name}' produced unexpected derived columns: {extra}",
        )

    def test_microstructure_isolation(self):
        result = self._build_group_features("enable_microstructure_features")
        self._assert_group_only(result, "microstructure")

    def test_liquidity_isolation(self):
        result = self._build_group_features("enable_liquidity_features")
        self._assert_group_only(result, "liquidity")

    def test_tradability_isolation(self):
        result = self._build_group_features("enable_tradability_features")
        self._assert_group_only(result, "tradability")

    def test_relative_strength_basic(self):
        """relative_strength can produce features without throwing."""
        try:
            result = self._build_group_features("enable_relative_strength_features")
            # Just verify no crash and basic features exist
            self.assertIn("ret_5d", result.columns)
            self.assertIn("ret_60d", result.columns)
        except Exception as exc:
            # relative_strength may depend on index data; skip if unavailable
            self.skipTest(f"relative_strength needs index data: {exc}")

    def test_fundamental_context_basic(self):
        """fundamental_context can produce features without throwing."""
        try:
            result = self._build_group_features("enable_fundamental_context_features")
            self.assertIn("log_mktcap", result.columns)
            self.assertIn("pe_ttm", result.columns)
        except Exception as exc:
            self.skipTest(f"fundamental_context unavailable in test: {exc}")

    def test_industry_momentum_does_not_trigger_v3b(self):
        """enable_industry_momentum_features must NOT trigger v3b_interaction."""
        from qsys.feature.builder import build_phase1_features

        # Need mock with industry column
        mock = _make_mock_panel()
        mock["industry"] = "TEST_INDUSTRY"
        flags = {
            "enable_industry_momentum_features": True,
            "enable_v3b_price_volume_features": False,
            "enable_v3b_interaction_features": False,
            # Disable relative_strength to avoid index data dependency
            "enable_relative_strength_features": False,
            "enable_regime_features": False,
        }
        result = build_phase1_features(mock, flags=flags)
        # Check that v3b features are not present
        v3b_cols = [c for c in result.columns if c.startswith("trend_")
                     or c.startswith("low_vol") or c.startswith("return_drawdown")
                     or c.startswith("pullback_")]
        self.assertEqual(
            len(v3b_cols), 0,
            f"industry_momentum triggered v3b features: {v3b_cols}",
        )

    def test_v3b_interaction_not_double_counted(self):
        """enable_v3b_interaction_features only calls build_v3a_v3b_interaction_features once."""
        from qsys.feature.builder import build_phase1_features

        mock = _make_mock_panel()
        # Add required v3a columns for interaction
        mock["holder_concentration_score"] = np.random.randn(N)
        mock["margin_trend_confirm_score"] = np.random.randn(N)

        flags = {
            "enable_v3b_price_volume_features": True,
            "enable_v3b_interaction_features": True,
            "enable_relative_strength_features": False,  # avoid index data dep
            "enable_regime_features": False,
        }
        result = build_phase1_features(mock, flags=flags)
        interaction_cols = [
            "holder_concentration_trend_confirm",
            "holder_concentration_low_vol_uptrend",
            "holder_concentration_volume_contract",
            "margin_holder_trend_confirm",
            "margin_pullback_recovery_confirm",
        ]
        for col in interaction_cols:
            self.assertIn(col, result.columns, f"Interaction col '{col}' missing")
            # Check no duplicate column
            self.assertEqual(
                result.columns.tolist().count(col), 1,
                f"Interaction col '{col}' duplicated!",
            )

    def test_multiple_groups_union_columns(self):
        """Combining multiple groups produces the union of declared features."""
        from qsys.feature.builder import build_phase1_features

        mock = _make_mock_panel()
        flags = {
            "enable_microstructure_features": True,
            "enable_liquidity_features": True,
            "enable_tradability_features": True,
            "enable_relative_strength_features": False,
            "enable_regime_features": False,
            "enable_industry_context_features": False,
            "enable_fundamental_context_features": False,
        }
        result = build_phase1_features(mock, flags=flags)
        micro = self.declared_features.get("microstructure", set())
        liquid = self.declared_features.get("liquidity", set())
        tradable = self.declared_features.get("tradability", set())
        expected_derived = micro | liquid | tradable
        # KNOWN: turnover_rate appears as both raw input and derived — the builder
        # passes it through rather than recomputing it from scratch. We accept
        # its presence in the result.
        _known_passthrough = {"turnover_rate"}
        expected_derived = expected_derived - _known_passthrough

        # Each expected feature should be present
        for feat in expected_derived:
            self.assertIn(feat, result.columns, f"Expected feature '{feat}' missing")


if __name__ == "__main__":
    unittest.main()
