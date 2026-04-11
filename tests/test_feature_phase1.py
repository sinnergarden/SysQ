import unittest
import pandas as pd

from qsys.feature.builder import _repair_research_input_columns, build_phase1_features
from qsys.feature.groups.fundamental_context import build_fundamental_context_features
from qsys.feature.transforms import apply_cross_sectional_standardization


class TestFeaturePhase1(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame(
            {
                "trade_date": pd.to_datetime([
                    "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20",
                    "2026-03-17", "2026-03-18", "2026-03-19", "2026-03-20",
                ]),
                "ts_code": ["AAA", "AAA", "AAA", "AAA", "BBB", "BBB", "BBB", "BBB"],
                "open": [10, 10.3, 10.6, 10.8, 20, 19.8, 20.2, 20.4],
                "high": [10.5, 10.8, 10.9, 11.0, 20.5, 20.1, 20.6, 20.7],
                "low": [9.8, 10.1, 10.2, 10.5, 19.6, 19.5, 19.9, 20.1],
                "close": [10.2, 10.6, 10.7, 10.9, 19.9, 20.0, 20.4, 20.5],
                "volume": [100, 120, 150, 180, 200, 210, 220, 230],
                "amount": [1000, 1400, 1700, 2100, 4000, 4200, 4500, 4700],
                "turnover_rate": [0.01, 0.012, 0.015, 0.018, 0.02, 0.021, 0.022, 0.023],
                "high_limit": [11, 11, 11, 11, 22, 22, 22, 22],
                "low_limit": [9, 9, 9, 9, 18, 18, 18, 18],
                "paused": [0, 0, 0, 0, 0, 0, 0, 0],
            }
        )

    def test_phase1_feature_build(self):
        out = build_phase1_features(self.df)
        required = [
            "close_to_open_gap_1d",
            "amount_log",
            "is_limit_up",
            "tradability_score",
            "ret_3d_rank",
            "open_to_close_ret_z",
            "amount_log_rank",
        ]
        for col in required:
            self.assertIn(col, out.columns)
        self.assertTrue(((out["tradability_score"] >= 0) & (out["tradability_score"] <= 1)).all())

    def test_feature_flag_ablation(self):
        out = build_phase1_features(self.df, flags={"enable_tradability_features": False})
        self.assertNotIn("tradability_score", out.columns)
        self.assertIn("amount_log", out.columns)

    def test_phase2_flags(self):
        out = build_phase1_features(self.df, flags={"enable_industry_context_features": True, "enable_regime_features": True})
        self.assertIn("industry_ret_1d", out.columns)
        self.assertIn("stock_minus_industry_ret", out.columns)
        self.assertIn("market_breadth", out.columns)
        self.assertIn("stock_minus_index_ret_3d", out.columns)

    def test_phase3_fundamental_features(self):
        df = self.df.copy()
        df['total_mv'] = [100, 101, 103, 105, 200, 201, 203, 205]
        df['circ_mv'] = [80, 81, 82, 84, 150, 151, 152, 154]
        df['pe'] = [10, 10, 11, 12, 20, 20, 21, 22]
        df['pb'] = [1, 1.1, 1.1, 1.2, 2, 2.1, 2.1, 2.2]
        df['net_income'] = [5, 5, 6, 6, 8, 8, 9, 9]
        df['revenue'] = [20, 20, 21, 21, 30, 30, 31, 31]
        df['total_assets'] = [100, 100, 100, 100, 200, 200, 200, 200]
        df['equity'] = [50, 50, 50, 50, 90, 90, 90, 90]
        df['op_cashflow'] = [6, 6, 7, 7, 10, 10, 11, 11]
        df['grossprofit_margin'] = [0.3, 0.3, 0.31, 0.31, 0.25, 0.25, 0.26, 0.26]
        df['debt_to_assets'] = [0.5, 0.5, 0.5, 0.5, 0.55, 0.55, 0.55, 0.55]
        out = build_fundamental_context_features(df)
        self.assertIn('log_mktcap', out.columns)
        self.assertIn('roa', out.columns)
        self.assertIn('net_margin', out.columns)
        self.assertIn('operating_cf_to_profit', out.columns)

    def test_repair_research_input_columns_handles_duplicate_target_columns(self):
        duplicated = self.df.copy()
        duplicated['close_x'] = duplicated['close'] + 0.1
        duplicated['close_y'] = duplicated['close'] + 0.2
        duplicated.insert(duplicated.columns.get_loc('close') + 1, 'close', duplicated['close'] + 0.3, allow_duplicates=True)

        repaired = _repair_research_input_columns(duplicated)

        self.assertEqual(repaired.columns.tolist().count('close'), 1)
        first_close = duplicated.loc[:, duplicated.columns == 'close'].iloc[:, 0]
        repaired_close = repaired['close']
        pd.testing.assert_series_equal(repaired_close.reset_index(drop=True), first_close.reset_index(drop=True), check_names=False)


if __name__ == "__main__":
    unittest.main()
