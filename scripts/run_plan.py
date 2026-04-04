"""
DEPRECATED: Use run_daily_trading.py instead.
This script is kept for backward compatibility only.
"""
import pandas as pd
import sys
from pathlib import Path
# Add project root to sys.path
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root))

from qsys.config import cfg
from qsys.utils.logger import log
from qsys.data.adapter import QlibAdapter
from qsys.strategy.generator import SignalGenerator
from qsys.strategy.engine import StrategyEngine
from qsys.trader.broker import BrokerAdapter
from qsys.trader.plan import PlanGenerator
from qsys.trader.notifier import Notifier
from qlib.data import D

def main():
    log.info("Starting Daily Plan Generation...")
    log.warning("Legacy entrypoint detected: scripts/run_plan.py. Recommended entrypoint is scripts/run_daily_trading.py.")
    
    # Config
    root_path = cfg.get_path("root")
    if root_path is None:
        raise ValueError("Root path not configured")
    model_path = root_path / "models" / "qlib_lgbm"
    position_file = root_path / "broker" / "holding.csv"
    webhook_url = cfg.get("wechat_webhook")
    
    # 1. Init Qlib & Data
    QlibAdapter().init_qlib()
    # Assuming run_update.py has run and data is up-to-date for TODAY
    # T = Today (Plan for T+1)
    # We need Today's Close to predict.
    today = pd.Timestamp.now().strftime('%Y-%m-%d')
    # Or use specific date for testing
    # today = '2022-01-04' 
    
    log.info(f"Planning for date after: {today}")
    
    # 2. Load Real Positions (Reality Wins)
    broker = BrokerAdapter()
    if position_file.exists():
        current_positions = broker.parse_positions(position_file)
        log.info(f"Loaded {len(current_positions)} positions from broker.")
    else:
        log.warning("No position file found, assuming empty portfolio.")
        current_positions = {}
        
    # Calculate Total Assets (Cash + Stock)
    # Cash should also be in file or input
    # For now assume fixed cash or read from file
    current_cash = 100000.0 # TODO: Read from file
    
    # 3. Predict & Strategy
    # Fetch today's data
    instruments = D.instruments('all')
    
    # Load Model
    signal_gen = SignalGenerator(model_path)
    
    try:
        # Fetch features for TODAY
        features = D.features(instruments, 
                            signal_gen.model.feature_config,
                            start_time=today, end_time=today)
                            
        if features is None or features.empty:
            log.error("No data found for today. Is market closed or data not updated?")
            return

        # Predict
        scores = signal_gen.predict(features)
        scores = scores.droplevel('datetime')
        
        # Get Market Status (for soft filter)
        price_fields = ['$close', '$open', '$factor', '$paused', '$high_limit', '$low_limit']
        market_data = D.features(instruments, price_fields, start_time=today, end_time=today)
        market_data = market_data.droplevel('datetime')
        market_data.columns = ['close', 'open', 'factor', 'is_suspended', 'limit_up', 'limit_down']
        
        market_data['is_suspended'] = market_data['is_suspended'].fillna(0).astype(bool)
        market_data['is_limit_up'] = market_data['close'] >= market_data['limit_up']
        
        # Strategy
        strategy = StrategyEngine(top_k=30)
        target_weights = strategy.generate_target_weights(scores, market_data)
        
        # 4. Generate Plan
        # Need current prices for asset calc and diff
        current_prices = market_data['close'].to_dict()
        
        # Recalc Total Assets based on REAL prices
        stock_value = sum(pos['total_amount'] * current_prices.get(pos['symbol'], 0) for pos in current_positions.values())
        total_assets = current_cash + stock_value
        
        log.info(f"Total Assets: {total_assets:,.2f} (Cash: {current_cash:,.2f}, Stock: {stock_value:,.2f})")
        
        plan_gen = PlanGenerator(min_trade_amount=5000)
        df_plan = plan_gen.generate_plan(target_weights, current_positions, total_assets, current_prices)
        
        # 5. Output & Notify
        print("\n" + "="*20 + " TRADING PLAN " + "="*20)
        print(df_plan)
        print("="*54 + "\n")
        
        if webhook_url:
            notifier = Notifier(webhook_url)
            msg = f"## 📅 Trading Plan ({today})\n\n"
            msg += f"**Assets**: {total_assets:,.0f}\n"
            msg += plan_gen.to_markdown(df_plan)
            notifier.send_markdown(msg)
            
        # Save to local CSV for record
        save_path = root_path / "plans" / f"plan_{today}.csv"
        save_path.parent.mkdir(exist_ok=True)
        df_plan.to_csv(save_path, index=False)
        log.info(f"Plan saved to {save_path}")
        
    except Exception as e:
        log.error(f"Plan generation failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
