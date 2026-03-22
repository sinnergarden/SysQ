"""
Research comparison utility.

Purpose:
- compare alternative strategy implementations or backtest behaviors
- exploratory tool; not a production daily-ops entrypoint

Typical usage:
- python scripts/run_compare.py

Note:
- keep for research/debug use; do not treat as production workflow.
"""

import pandas as pd
import matplotlib.pyplot as plt
import sys
from pathlib import Path
# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.backtest import BacktestEngine
from qsys.strategy.swap import TopKSwapStrategy
from qsys.strategy.engine import StrategyEngine # Baseline
from qsys.utils.logger import log

def run_comparison():
    root_path = cfg.get_path("root")
    if root_path is None:
        raise ValueError("Root path not configured")
    model_dir = root_path / "models" / "qlib_lgbm"
    start_date = '2022-01-01'
    end_date = '2022-03-01'
    
    # 1. Run Baseline (TopK Rebalance)
    log.info("Running Baseline Strategy...")
    engine_base = BacktestEngine(model_dir, start_date=start_date, end_date=end_date)
    # Default is StrategyEngine(top_k=50)
    # Let's make it comparable: Top 5 Rebalance
    engine_base.strategy = StrategyEngine(top_k=5, method='equal_weight')
    res_base = engine_base.run()
    
    # 2. Run Swap Strategy
    log.info("Running Swap Strategy...")
    # Inject new strategy
    # Note: We need to monkey-patch run() or subclass BacktestEngine because 
    # the new strategy signature requires 'account' and 'current_prices'
    # But wait, run_backtest.py already has access to account and prices.
    # We can just update run_backtest.py to support checking signature or passing kwargs.
    
    # Or for this script, we can create a subclass inline.
    class SwapBacktestEngine(BacktestEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.strategy = TopKSwapStrategy(max_slots=5)
            
        def run(self):
            # We need to override run loop to pass extra args to strategy
            # Copy-paste core logic for simplicity or refactor original
            self.prepare()
            history = []
            from tqdm import tqdm
            from qlib.data import D
            signal_gen = self.signal_gen
            if signal_gen is None or signal_gen.model is None:
                raise ValueError("Model not loaded")
            
            for date in tqdm(self.trade_dates, desc="Swap Backtest"):
                try:
                    instruments = D.instruments('all')
                    features = D.features(instruments, signal_gen.model.feature_config, start_time=date, end_time=date)
                    if features is None or features.empty: continue
                    
                    scores = signal_gen.predict(features).droplevel('datetime')
                    
                    price_fields = ['$close', '$open', '$factor', '$paused', '$high_limit', '$low_limit']
                    market_data = D.features(instruments, price_fields, start_time=date, end_time=date).droplevel('datetime')
                    market_data.columns = ['close', 'open', 'factor', 'is_suspended', 'limit_up', 'limit_down']
                    market_data['is_suspended'] = market_data['is_suspended'].fillna(0).astype(bool)
                    market_data['is_limit_up'] = market_data['close'] >= market_data['limit_up']
                    market_data['is_limit_down'] = market_data['close'] <= market_data['limit_down']
                    current_prices = market_data['close'].to_dict()
                    
                    # --- CALL STRATEGY WITH EXTRA ARGS ---
                    target_weights = self.strategy.generate_target_weights(
                        scores, market_data, self.account, current_prices
                    )
                    # -------------------------------------
                    
                    orders = self.order_gen.generate_orders(target_weights, self.account, current_prices)
                    trades = self.matcher.match(orders, self.account, market_data, current_prices)
                    self.account.settlement()
                    
                    history.append({
                        'date': date,
                        'total_assets': self.account.get_total_equity(current_prices)
                    })
                except Exception as e:
                    log.error(e)
            return pd.DataFrame(history)

    engine_swap = SwapBacktestEngine(model_dir, start_date=start_date, end_date=end_date)
    res_swap = engine_swap.run()
    
    # 3. Plot
    res_base['date'] = pd.to_datetime(res_base['date'])
    res_base = res_base.set_index('date')
    
    res_swap['date'] = pd.to_datetime(res_swap['date'])
    res_swap = res_swap.set_index('date')
    
    plt.figure(figsize=(12, 6))
    plt.plot(res_base['total_assets'], label='Baseline (Top 5 Rebalance)')
    plt.plot(res_swap['total_assets'], label='Swap (Strict Entry/Wide Exit)')
    plt.title('Strategy Comparison')
    plt.legend()
    plt.grid(True)
    plt.savefig('SysQ/experiments/strategy_comparison.png')
    print("Comparison plot saved to SysQ/experiments/strategy_comparison.png")

if __name__ == "__main__":
    run_comparison()
