
import pandas as pd
from qsys.utils.logger import log
from qsys.strategy.engine import StrategyEngine
from qsys.trader.plan import PlanGenerator
from qsys.live.account import RealAccount
from qsys.live.reconciliation import export_plan_bundle
from qlib.data import D
from qsys.data.adapter import QlibAdapter

class LiveManager:
    """
    Orchestrates the Daily Live Trading Process:
    1. Load Real Account State (Yesterday's close or Today's sync).
    2. Load Market Data & Predictions (Today's Pre-Market or Close).
    3. Generate Target Weights.
    4. Generate Trading Plan (Orders).
    """
    def __init__(self, model_path, db_path="data/real_account.db"):
        self.real_account = RealAccount(db_path)
        self.strategy = StrategyEngine(top_k=30)
        self.planner = PlanGenerator()
        
        # We need a model wrapper that can predict for a single day
        # Assuming SignalGenerator or similar is passed/loaded
        self.model_path = model_path
        # Lazy load model
        self.model = None

    def load_model(self):
        if self.model is None:
            from qsys.strategy.generator import SignalGenerator
            self.model = SignalGenerator(self.model_path)
            log.info(f"Model loaded from {self.model_path}")

    def run_daily_plan(self, date, market_data=None, account_name="real", execution_date=None):
        """
        Generate Plan for `date` (usually Tomorrow, using Today's data).
        
        Args:
            date: str "YYYY-MM-DD". The date we are making decisions ON.
                  (Usually T, to trade on T+1, or T open).
            market_data: Optional DataFrame. If None, fetch from Qlib.
        """
        self.load_model()
        if self.model is None:
            log.error("Model not loaded.")
            return None
        
        # 1. Get Real State
        # We need the LATEST state available.
        # If we are running this on T night, we should have synced T's close.
        latest_date = self.real_account.get_latest_date()
        if not latest_date:
            log.error("No account state date found! Please sync broker state first.")
            return None
        state = self.real_account.get_state(latest_date)
        
        if not state:
            log.error("No account state found! Please sync broker state first.")
            return None
            
        log.info(f"Generating plan based on Account State from {state['date']}")
        log.info(f"Total Assets: {state['total_assets']:,.2f}, Cash: {state['cash']:,.2f}")
        
        # 2. Get Data & Predict
        # We need features for `date`.
        # Note: If date is "Today", ensure Qlib has data updated.
        try:
            instruments = D.instruments('csi300')
            if market_data is None:
                features = QlibAdapter().get_features(
                    instruments, 
                    self.model.model.feature_config, 
                    start_time=date, 
                    end_time=date
                )
            else:
                features = market_data

            if features is None or features.empty:
                log.error(f"No features found for {date}!")
                return None

            scores = self.model.predict(features)

            price_fields = ['$close', '$factor']
            prices_df = QlibAdapter().get_features(
                instruments, 
                price_fields, 
                start_time=date, 
                end_time=date
            )
            prices_df = prices_df.rename(columns={'$close': 'close', '$factor': 'factor'})

            target_weights = self.strategy.generate_target_weights(scores, market_status=None)

            current_positions = state['positions']
            total_assets = state['total_assets']
            current_prices = prices_df['close'].to_dict()

            plan_df = self.planner.generate_plan(
                target_weights, 
                current_positions, 
                total_assets, 
                current_prices
            )

            outputs = export_plan_bundle(
                plan_df,
                output_dir="data",
                plan_date=date,
                account_name=account_name,
                execution_date=execution_date or date,
            )
            log.info(f"Plan saved to {outputs['plan']}")
            log.info(f"Real sync template saved to {outputs['real_sync_template']}")
            log.info(f"\n{self.planner.to_markdown(plan_df)}")

            return plan_df
        except Exception as e:
            log.error(f"Failed to run live plan: {e}")
            import traceback
            traceback.print_exc()
            return None
