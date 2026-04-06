
import unittest
import pandas as pd
import inspect
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from qsys.trader.account import Account
from qsys.strategy.engine import StrategyEngine
from qsys.feature.library import FeatureLibrary, phase123
from qsys.backtest import BacktestEngine
from qsys.live.account import RealAccount
from qsys.data.adapter import QlibAdapter

class TestCoreAPIContracts(unittest.TestCase):
    """
    This test suite enforces the Immutable Core API Contracts defined in context.md.
    It does not test logic correctness (functional testing), but ensures that 
    the interfaces (signatures, return types, attributes) remain consistent.
    """
    @classmethod
    def setUpClass(cls):
        # Initialize Qlib just in case it's needed for instantiation
        try:
            adapter = QlibAdapter()
            adapter.init_qlib()
        except Exception as e:
            print(f"Qlib init warning: {e}")

    def test_account_contract(self):
        """Verify Account API Contract"""
        # 1. Instantiation
        acc = Account(init_cash=100000)
        
        # 2. Properties
        self.assertTrue(hasattr(acc, 'total_assets'), "Account missing 'total_assets' property")
        self.assertTrue(hasattr(acc, 'positions'), "Account missing 'positions' attribute")
        self.assertTrue(hasattr(acc, 'cash'), "Account missing 'cash' attribute")
        
        # 3. Methods Signature
        # update_after_deal(self, symbol, amount, price, fee, side)
        sig = inspect.signature(acc.update_after_deal)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ['symbol', 'amount', 'price', 'fee', 'side'], 
                         "Account.update_after_deal signature mismatch")
        
        # settlement(self)
        self.assertTrue(hasattr(acc, 'settlement'))
        
        # get_metrics(self)
        self.assertTrue(hasattr(acc, 'get_metrics'))
        metrics = acc.get_metrics()
        self.assertIsInstance(metrics, dict)
        self.assertIn('total_return', metrics)
        self.assertIn('max_drawdown', metrics)

    def test_strategy_engine_contract(self):
        """Verify StrategyEngine API Contract"""
        engine = StrategyEngine()
        
        # Method: generate_target_weights(self, scores, market_status=None)
        self.assertTrue(hasattr(engine, 'generate_target_weights'))
        
        # Check Signature
        sig = inspect.signature(engine.generate_target_weights)
        params = list(sig.parameters.keys())
        self.assertIn('scores', params)
        self.assertIn('market_status', params)
        
        # Smoke Test with Dummy Data
        scores = pd.Series([0.1, 0.2], index=['A', 'B'])
        weights = engine.generate_target_weights(scores, market_status=None)
        self.assertIsInstance(weights, dict, "generate_target_weights must return a dict")

    def test_feature_library_contract(self):
        """Verify FeatureLibrary API Contract"""
        # Test base class or a concrete implementation like phase123
        lib = phase123(instruments='all')
        
        # Method: get_feature_config
        self.assertTrue(hasattr(lib, 'get_feature_config'))
        f_conf = lib.get_feature_config()
        # Should return tuple (fields, names) or dict or list depending on implementation, 
        # but context.md says Tuple[List, List]. 
        # Let's check what the current implementation actually returns to align context.md or code.
        # Currently phase123.get_feature_config returns (fields, fields).
        self.assertIsInstance(f_conf, tuple)
        self.assertEqual(len(f_conf), 2)
        
        # Method: get_label_config
        self.assertTrue(hasattr(lib, 'get_label_config'))
        l_conf = lib.get_label_config()
        self.assertIsInstance(l_conf, tuple)

    def test_backtest_engine_contract(self):
        """Verify BacktestEngine API Contract"""
        # Method: run
        self.assertTrue(hasattr(BacktestEngine, 'run'))
        
        # Check init arguments
        sig = inspect.signature(BacktestEngine.__init__)
        params = list(sig.parameters.keys())
        self.assertIn('account', params, "BacktestEngine must accept 'account' injection")
        self.assertIn('daily_predictions', params, "BacktestEngine must accept 'daily_predictions'")

    def test_real_account_contract(self):
        """Verify RealAccount API Contract (Live Persistence)"""
        # 1. Instantiation
        # Using a temporary db file
        import tempfile
        with tempfile.NamedTemporaryFile() as tmp:
            acc = RealAccount(db_path=tmp.name)
            
            # 2. Methods
            self.assertTrue(hasattr(acc, 'sync_broker_state'))
            self.assertTrue(hasattr(acc, 'get_state'))
            
            # 3. Signature Check
            sig = inspect.signature(acc.sync_broker_state)
            params = list(sig.parameters.keys())
            self.assertIn('date', params)
            self.assertIn('cash', params)
            self.assertIn('positions', params)
            self.assertIn('account_name', params)

if __name__ == '__main__':
    unittest.main()
