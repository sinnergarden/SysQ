
import unittest
import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
import qlib
from qlib.data import D
from qsys.feature.library import FeatureResearch

class TestSysQPipeline(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        print("\n=== Setting up SysQ Test Environment ===")
        cls.adapter = QlibAdapter()
        cls.adapter.init_qlib()
        
    def test_01_alpha158_import(self):
        """Test if phase123 class can be imported and instantiated"""
        try:
            from qsys.feature.library import phase123
            # Check if class exists
            self.assertIsNotNone(phase123)
            
            # Instantiate to check if it has get_feature_config
            obj = phase123(instruments='all', start_time='2023-01-01', end_time='2023-01-05', infer_processors=[], learn_processors=[])
            config = obj.get_feature_config()
            self.assertTrue(len(config) > 0, "phase123 config should not be empty")
            print("✅ phase123 Import & Instantiation Passed")
        except ImportError:
            self.fail("Could not import phase123 from qsys.feature.library")
        except Exception as e:
            self.fail(f"Instantiation failed: {e}")
            
    def test_02_data_availability(self):
        """Test if we can fetch data for a sample stock"""
        # Get all instruments
        instruments = D.instruments('all')
        inst_list = D.list_instruments(instruments=instruments, start_time='2023-01-01', end_time='2023-01-05')
        
        self.assertTrue(len(inst_list) > 0, "No instruments found in Qlib bin")
        
        # Pick one
        self.sample_stock = list(inst_list.keys())[0]
        print(f"✅ Found instruments. Using {self.sample_stock} for testing.")
        
        # Fetch features
        fields = ['$close', '$factor', '$volume']
        df = self.adapter.get_features([self.sample_stock], fields, start_time='2023-01-01', end_time='2023-02-01')
        
        self.assertFalse(df.empty, f"Data for {self.sample_stock} is empty")
        self.assertTrue('$factor' in df.columns, "$factor column missing")
        
        # Check if factor is valid (not all NaN)
        self.assertFalse(df['$factor'].isna().all(), "$factor is all NaN")
        print("✅ Data Availability Passed")

    def test_03_forward_adj_calculation(self):
        """Test calculation of forward adjusted price"""
        # We need to find a stock and calculate
        instruments = D.instruments('all')
        inst_list = D.list_instruments(instruments=instruments, start_time='2023-01-01', end_time='2023-01-05')
        stock = list(inst_list.keys())[0]
        
        df = self.adapter.get_features([stock], ['$close', '$factor'], start_time='2023-01-01', end_time='2023-01-10')
        latest_factor = df['$factor'].iloc[-1] if not df['$factor'].isna().all() else 1.0
        if not latest_factor or latest_factor == 0:
            latest_factor = 1.0
        df['adj_close'] = df['$close'] * (df['$factor'] / latest_factor)
        
        self.assertFalse(df['adj_close'].isna().any(), "Calculated adj_close contains NaNs")
        self.assertAlmostEqual(df['adj_close'].iloc[-1], df['$close'].iloc[-1], places=6)
        print("✅ Forward Adj Calculation Passed")

    def test_04_feature_research_report(self):
        original_get_features = QlibAdapter.get_features
        def dummy_get_features(self, instruments, fields, start_time=None, end_time=None, freq="day", inst_processors=None):
            dates = list(pd.date_range("2023-01-01", periods=10, freq="D"))
            idx = pd.MultiIndex.from_product(
                [["600519.SH", "000001.SZ"], dates],
                names=["ts_code", "trade_date"]
            )
            df = pd.DataFrame(index=idx)
            df["feat_a"] = np.linspace(0.01, 0.05, len(idx))
            df["feat_b"] = np.linspace(1.0, 2.0, len(idx))
            df["label"] = np.linspace(0.02, 0.03, len(idx))
            return df[[c for c in fields if c in df.columns]]
        QlibAdapter.get_features = dummy_get_features
        try:
            report = FeatureResearch.rank_features_by_ic(
                instruments="all",
                start_time="2023-01-01",
                end_time="2023-01-10",
                label=["label"],
                feature_fields=["feat_a", "feat_b"],
                topk=2
            )
        finally:
            QlibAdapter.get_features = original_get_features
        self.assertFalse(report.empty)
        self.assertIn("feature", report.columns)
        self.assertIn("ricir", report.columns)

if __name__ == '__main__':
    unittest.main()
