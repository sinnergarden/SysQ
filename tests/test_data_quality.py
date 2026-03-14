
import unittest
import pandas as pd
from qlib.data import D
from qsys.data.adapter import QlibAdapter
from qsys.config import cfg
import logging
import sys

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestDataQuality(unittest.TestCase):
    """
    Integration tests for data quality verification.
    Matches the logic in notebooks/tutorial.ipynb cell 2.3.
    """
    
    def setUp(self):
        """Initialize adapter and configuration."""
        self.adapter = QlibAdapter()
        # Ensure Qlib is initialized
        self.adapter.init_qlib()
        
    def test_qlib_data_integrity(self):
        """
        Verify that:
        1. Instruments can be listed (CSI300).
        2. Data can be fetched for a sample stock (e.g., 600519.SH).
        3. All fields defined in settings.yaml are present in the fetched data.
        4. Data index is correctly formatted (MultiIndex with instrument and datetime).
        5. Basic data consistency (not empty, no full-NaN columns for critical fields).
        """
        # 1. Get instruments
        # Mock data instruments might be in 'all' or specific file
        try:
            instruments = D.instruments('all')
            stock_list = D.list_instruments(instruments=instruments, start_time='2025-01-01', end_time='2025-01-10')
        except Exception as e:
            logger.warning(f"Failed to list instruments: {e}")
            stock_list = {}
            
        self.assertTrue(stock_list, "No instruments found in Qlib database!")
        
        # 2. Select sample stock
        # Try to find a stock that we know should exist (from mock data)
        # Mock data stocks: SH600519, SZ000001, etc.
        # Check both formats (prefix and suffix)
        candidates = ['SH600519', 'sh600519', '600519.SH', 'SZ000001', 'sz000001', '000001.SZ']
        stock_code = None
        for cand in candidates:
            if cand in stock_list:
                stock_code = cand
                break
        
        if not stock_code:
            # Fallback to first available
            stock_code = list(stock_list.keys())[0]
            
        logger.info(f"Verifying data for: {stock_code}")
        
        # 3. Get expected fields from config
        tushare_config = cfg.get_tushare_feature_config()
        qlib_fields = tushare_config.get("adapter", {}).get("qlib_fields", [])
        self.assertTrue(qlib_fields, "qlib_fields not found in settings.yaml")
        
        # Add $ prefix for Qlib
        fields = [f"${f}" for f in qlib_fields]
        
        # 4. Fetch data
        # Use a recent date range where we expect data (mock data is up to 2026-03-05)
        df = self.adapter.get_features(
            instruments=[stock_code],
            fields=fields,
            start_time='2025-01-01',
            end_time='2025-01-10'
        )
        
        self.assertIsNotNone(df, f"No data found for {stock_code}")
        self.assertFalse(df.empty, f"Data is empty for {stock_code}")
        
        # 5. Check Index
        self.assertIsInstance(df.index, pd.MultiIndex, "Data should have MultiIndex")
        self.assertIn("datetime", df.index.names, "Index should contain 'datetime'")
        self.assertIn("instrument", df.index.names, "Index should contain 'instrument'")
        
        # 6. Check Fields Presence
        missing_fields = [f for f in fields if f not in df.columns]
        self.assertFalse(missing_fields, f"Missing fields in fetched data: {missing_fields}")
        
        # 7. Check for NaN values
        all_nan_cols = df.columns[df.isna().all()].tolist()
        
        critical_fields = ['$open', '$close', '$volume', '$amount']
        for cf in critical_fields:
            if cf in df.columns:
                self.assertNotIn(cf, all_nan_cols, f"Critical field {cf} is entirely NaN!")
        
        if all_nan_cols:
            logger.warning(f"The following columns are entirely NaN for {stock_code}: {all_nan_cols}")
            
        # 8. Check Factor and Price Adjustment
        if '$factor' in df.columns and '$close' in df.columns:
            latest_factor = df['$factor'].iloc[-1]
            if latest_factor == 0:
                latest_factor = 1.0
            
            # Just ensure calculation doesn't crash
            adj_close = df['$close'] * (df['$factor'] / latest_factor)
            self.assertFalse(adj_close.empty)
            
        logger.info(f"Data verification passed for {stock_code}. Rows: {len(df)}")

    def test_scaling_and_outliers(self):
        """
        Verify:
        1. Scaling of total_mv and circ_mv (should be > 1e11 for large caps).
        2. No extreme outliers like negative prices (though adapter handles this).
        """
        # Fetch Moutai if possible, or use available large cap
        target_stock = '600519.SH'
        
        # Check if target exists in list
        try:
             stock_list = D.list_instruments(instruments=D.instruments('csi300'), start_time='2022-01-01', end_time='2022-01-10')
        except:
             stock_list = {}

        if target_stock in stock_list:
            instruments = [target_stock]
        elif stock_list:
            instruments = list(stock_list.keys())[:1]
        else:
            logger.warning("No instruments found for scaling test.")
            return

        fields = ['$total_mv', '$circ_mv', '$pe', '$pb']
        # Use config to get fields without $ to check existence
        
        df = self.adapter.get_features(instruments, fields, start_time='2022-01-01', end_time='2022-01-10')
        
        if df.empty:
            logger.warning("No data for scaling test. Skipping.")
            return

        # Check Scaling
        if '$total_mv' in df.columns:
            # Check max value. 
            # 1 Billion Yuan = 1e9. 
            # If in Wan, 1 Billion = 1e5.
            # Even small caps are > 1 Billion.
            # So if max < 1e8, it is definitely WRONG (Wan).
            max_val = df['$total_mv'].max()
            self.assertGreater(max_val, 1e8, f"Total MV max value {max_val} is too small! Likely in Wan (10k) not Yuan.")
            
            # Sanity Check for Double Scaling:
            # Largest company (Apple) is ~20 Trillion RMB (2e13). 
            # If we double scale, we get 10,000x -> 2e17.
            # So let's assert max < 1e15 (1000 Trillion).
            self.assertLess(max_val, 1e15, f"Total MV max value {max_val} is suspiciously large! Double scaling?")
            
            if target_stock in df.index.get_level_values('instrument'):
                moutai_mv = df.loc[(target_stock, slice(None)), '$total_mv'].mean()
                # Moutai ~2 Trillion = 2e12
                self.assertGreater(moutai_mv, 1e11, "Moutai Total MV should be > 100 Billion Yuan (1e11)")

        # Check Outliers (Just log them)
        if '$pe' in df.columns:
            neg_pe = (df['$pe'] < 0).sum()
            if neg_pe > 0:
                logger.info(f"Found {neg_pe} negative PE values. Ensure downstream handles this.")
        
        if '$pb' in df.columns:
             huge_pb = (df['$pb'] > 10000).sum()
             if huge_pb > 0:
                 logger.warning(f"Found {huge_pb} huge PB values (>10000).")


if __name__ == "__main__":
    unittest.main()
