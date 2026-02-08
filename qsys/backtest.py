import pandas as pd
from tqdm import tqdm
from qsys.config import cfg
from qsys.utils.logger import log
from qsys.strategy.generator import SignalGenerator
from qsys.strategy.engine import StrategyEngine
from qsys.trader.account import Account
from qsys.trader.diff import OrderGenerator
from qsys.trader.matcher import MatchEngine
from qsys.data.adapter import QlibAdapter
from qsys.analysis.tearsheet import PerformanceAnalyzer
from qlib.data import D

class BacktestEngine:
    def __init__(self, model_path=None, universe='csi300', start_date='2022-01-01', end_date='2022-12-31', 
                 account=None, daily_predictions=None, top_k=50, n_drop=0):
        self.start_date = start_date
        self.end_date = end_date
        self.universe = universe
        
        # Components
        if model_path:
            self.signal_gen = SignalGenerator(model_path)
        else:
            self.signal_gen = None
            
        self.daily_predictions = daily_predictions
        
        self.strategy = StrategyEngine(top_k=top_k, method='equal_weight')
        self.account = account if account else Account(init_cash=1_000_000)
        self.order_gen = OrderGenerator()
        self.matcher = MatchEngine()
        
        # Data Cache
        self.trade_dates = []

    def prepare(self):
        log.info("Preparing Backtest...")
        # Ensure Qlib
        # QlibAdapter().init_qlib() # Removed duplicate init
        
        # Get Calendar
        cal = D.calendar(start_time=self.start_date, end_time=self.end_date)
        self.trade_dates = [pd.Timestamp(x).strftime('%Y-%m-%d') for x in cal]
        log.info(f"Backtest Range: {self.start_date} to {self.end_date}, Total Days: {len(self.trade_dates)}")

    def validate_data(self, df, name="Data"):
        """
        Validate data for NaNs and missing values.
        """
        if df.empty:
            log.error(f"{name} is empty!")
            return False
            
        # Check for NaNs
        nan_count = df.isna().sum().sum()
        if nan_count > 0:
            log.warning(f"{name} contains {nan_count} NaNs. Filling with 0/ffill...")
            # Simple fill strategy
            df.ffill(inplace=True)
            df.fillna(0, inplace=True)
            
        # Check for Infinite values
        # Replace inf with NaN then 0
        # df.replace([np.inf, -np.inf], np.nan, inplace=True)
        # df.fillna(0, inplace=True)
        
        return True

    def run(self):
        self.prepare()
        
        log.info("Phase 1: Batch Data Fetching & Inference (Pre-loading)...")
        
        # 1. Batch Fetch Features & Market Data
        # We fetch ALL data for the backtest period at once.
        # This avoids spinning up Qlib's multiprocessing pool 120+ times.
        instruments = D.instruments(self.universe)
        
        # A. Features (Only if prediction needed)
        all_scores = pd.DataFrame()
        if self.daily_predictions is not None:
             log.info("Using provided daily predictions...")
             all_scores = self.daily_predictions
        else:
            log.info(f"Fetching features for {self.start_date} - {self.end_date}...")
            if not self.signal_gen:
                log.error("No predictions provided and no model loaded!")
                return pd.DataFrame()
                
            try:
                all_features = QlibAdapter().get_features(
                    instruments=instruments, 
                    fields=self.signal_gen.model.feature_config,
                    start_time=self.start_date, 
                    end_time=self.end_date
                )
            except Exception as e:
                log.error(f"Failed to fetch batch features: {e}")
                return pd.DataFrame()

            if all_features is None or all_features.empty:
                log.warning("No features found for the specified range.")
                return pd.DataFrame()

            # VALIDATION
            self.validate_data(all_features, "Features")

            # B. Batch Prediction
            log.info("Running Batch Prediction...")
            all_scores = self.signal_gen.predict(all_features)
            
        # Ensure MultiIndex order (datetime, instrument) for fast slicing
        # Qlib default is (instrument, datetime).
        if not all_scores.empty and all_scores.index.names == ['instrument', 'datetime']:
            all_scores = all_scores.swaplevel().sort_index()
        
        # C. Market Data
        log.info("Fetching Market Data...")
        # Note: $close in Qlib is usually Adjusted Close if factor is present in binary.
        # However, for realistic backtest, we might want Raw Close + Factor?
        # Standard Qlib Backtest usually uses $close (Adj) for pnl calculation.
        # To be safe, we fetch both if needed, but here we assume $close is actionable price (Adj).
        price_fields = ['$close', '$open', '$factor', '$paused', '$high_limit', '$low_limit']
        all_market_data = QlibAdapter().get_features(
            instruments, 
            price_fields, 
            start_time=self.start_date, 
            end_time=self.end_date
        )
        all_market_data.columns = ['close', 'open', 'factor', 'is_suspended', 'limit_up', 'limit_down']
        
        # VALIDATION
        self.validate_data(all_market_data, "Market Data")

        # Pre-calculate status
        all_market_data['is_suspended'] = all_market_data['is_suspended'].fillna(0).astype(bool)
        
        # Fix for missing limit data (where limit_up/down are 0)
        # Only calculate limit status if limit prices are valid (> 0)
        all_market_data['is_limit_up'] = False
        all_market_data['is_limit_down'] = False
        
        mask_valid_limit = all_market_data['limit_up'] > 0.01 # Use small epsilon
        if mask_valid_limit.any():
            all_market_data.loc[mask_valid_limit, 'is_limit_up'] = (
                all_market_data.loc[mask_valid_limit, 'close'] >= all_market_data.loc[mask_valid_limit, 'limit_up']
            )
            
        mask_valid_down = all_market_data['limit_down'] > 0.01
        if mask_valid_down.any():
            all_market_data.loc[mask_valid_down, 'is_limit_down'] = (
                all_market_data.loc[mask_valid_down, 'close'] <= all_market_data.loc[mask_valid_down, 'limit_down']
            )
        
        if all_market_data.index.names == ['instrument', 'datetime']:
            all_market_data = all_market_data.swaplevel().sort_index()

        log.info("Phase 2: Event-Driven Loop...")
        history = []
        trade_logs = [] # Detailed logs
        
        for date in tqdm(self.trade_dates, desc="Backtesting"):
            try:
                # Ensure date is Timestamp for lookup if needed
                ts_date = pd.Timestamp(date)
                
                # 1. Fast Data Lookup (In-Memory)
                try:
                    # Try lookup with Timestamp
                    scores = all_scores.loc[ts_date]
                    market_data = all_market_data.loc[ts_date]
                except KeyError:
                    continue
                    
                if not isinstance(scores, (pd.Series, pd.DataFrame)) or not isinstance(market_data, pd.DataFrame):
                    continue
                if scores.empty or market_data.empty:
                    continue
                
                # Prepare prices dict for fast access
                current_prices = market_data['close'].to_dict()
                
                # 2. Strategy
                # Strategy expects scores (Series) and market_data (DataFrame)
                target_weights = self.strategy.generate_target_weights(scores, market_data)
                
                # 3. Order Gen
                orders = self.order_gen.generate_orders(target_weights, self.account, current_prices)
                
                # 4. Match
                trades = self.matcher.match(orders, self.account, market_data, current_prices)
                
                # LOGGING
                for t in trades:
                    t['date'] = date
                    trade_logs.append(t)

                # Calculate Daily Stats from Trades
                daily_fee = sum(t['fee'] for t in trades)
                daily_turnover = sum(t['filled_amount'] * t['deal_price'] for t in trades)
                
                # 5. Settlement
                self.account.settlement()
                
                # 6. Record
                total_assets = self.account.get_total_equity(current_prices)
                
                # Update Account History
                self.account.record_daily(date, total_assets)
                
                history.append({
                    'date': date,
                    'total_assets': total_assets,
                    'cash': self.account.cash,
                    'position_count': len(self.account.positions),
                    'trade_count': len(trades),
                    'daily_fee': daily_fee,
                    'daily_turnover': daily_turnover
                })
                
            except Exception as e:
                log.error(f"Error on {date}: {e}")
                # Don't break, just skip day
                
        df_result = pd.DataFrame(history)
        df_trades = pd.DataFrame(trade_logs)
        
        # Analyze
        if not df_result.empty:
            log.info(f"Backtest finished. Final Assets: {df_result.iloc[-1]['total_assets']:.2f}")
            # Attach trades to result for inspection
            self.last_trades = df_trades
            PerformanceAnalyzer.show(df_result)
            
        return df_result
