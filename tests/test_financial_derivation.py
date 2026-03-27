import sys
import unittest
from pathlib import Path

import pandas as pd

project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.data.collector import TushareCollector


class TestFinancialDerivation(unittest.TestCase):
    def test_merge_financials_derives_missing_ratios(self):
        collector = TushareCollector()
        daily_df = pd.DataFrame({
            "ts_code": ["600176.SH"],
            "trade_date": ["20260325"],
            "close": [10.0],
            "open": [9.8],
            "high": [10.1],
            "low": [9.7],
            "vol": [1000.0],
        })
        fin_df = pd.DataFrame({
            "ts_code": ["600176.SH"],
            "ann_date": ["20260320"],
            "end_date": ["20251231"],
            "net_income": [200.0],
            "revenue": [1000.0],
            "oper_cost": [600.0],
            "total_assets": [5000.0],
            "equity": [2000.0],
            "total_cur_assets": [1200.0],
            "total_cur_liab": [800.0],
            "roe": [None],
            "grossprofit_margin": [None],
            "debt_to_assets": [None],
            "current_ratio": [None],
        })
        merged = collector._merge_financials(daily_df, fin_df)
        row = merged.iloc[0]
        self.assertAlmostEqual(row["roe"], 0.1, places=6)
        self.assertAlmostEqual(row["grossprofit_margin"], 0.4, places=6)
        self.assertAlmostEqual(row["debt_to_assets"], 0.6, places=6)
        self.assertAlmostEqual(row["current_ratio"], 1.5, places=6)


if __name__ == "__main__":
    unittest.main()
