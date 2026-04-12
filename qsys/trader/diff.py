import pandas as pd
import numpy as np
from qsys.utils.logger import log

class OrderGenerator:
    def __init__(self, min_trade_buffer_ratio: float = 0.0):
        self.min_trade_buffer_ratio = max(float(min_trade_buffer_ratio), 0.0)
        self.last_buffer_audit = []

    def generate_orders(self, target_weights: dict, account, current_prices: dict, *, trade_date: str | None = None):
        """
        Generate orders to move from current portfolio to target weights.
        
        Logic:
        1. Calc Target Value for each stock: TargetWeight * TotalEquity
        2. Calc Current Value: Pos.TotalAmount * Price
        3. Diff Value = Target - Current
        4. Diff Amount = Diff Value / Price
        5. Round to Lots (100 shares)
        6. Separate Buy/Sell
        """
        total_equity = account.get_total_equity(current_prices)
        orders = [] # List of dict: {'symbol', 'amount', 'side', 'price'}
        self.last_buffer_audit = []
        
        # 1. Identify all involved symbols (Current holdings + Target)
        all_symbols = set(account.positions.keys()) | set(target_weights.keys())
        
        for sym in all_symbols:
            price = current_prices.get(sym)
            if not price or np.isnan(price) or price <= 0:
                log.warning(f"Skipping order gen for {sym}: Invalid price {price}")
                continue
                
            # Target
            t_weight = target_weights.get(sym, 0.0)
            t_value = total_equity * t_weight
            
            # Current
            pos = account.positions.get(sym)
            c_amount = pos.total_amount if pos else 0
            c_value = c_amount * price
            
            # Diff
            diff_value = t_value - c_value
            if total_equity > 0 and abs(diff_value) / total_equity < self.min_trade_buffer_ratio:
                self.last_buffer_audit.append({
                    'date': trade_date,
                    'instrument': sym,
                    'current_value': float(c_value),
                    'target_value': float(t_value),
                    'diff_value': float(diff_value),
                    'diff_ratio': float(abs(diff_value) / total_equity),
                    'threshold_ratio': float(self.min_trade_buffer_ratio),
                    'skip_reason': 'skipped_due_to_turnover_buffer',
                })
                continue
            diff_amount_raw = diff_value / price
            
            # Round to lot (100)
            # Use floor for buy (conservative), ceil/round for sell?
            # Standard: Round to nearest 100
            # Or: int(diff_amount_raw / 100) * 100
            
            lot_size = 100
            amount_lots = int(diff_amount_raw / lot_size) * lot_size
            
            if amount_lots == 0:
                continue
                
            side = 'buy' if amount_lots > 0 else 'sell'
            abs_amount = abs(amount_lots)
            
            # For Sell: Check sellable? 
            # Ideally OrderGen just generates intent. MatchEngine checks constraints.
            # But we can optimize: if we know we can't sell, maybe don't generate?
            # Let's leave hard checks to MatchEngine.
            
            orders.append({
                'symbol': sym,
                'amount': abs_amount,
                'side': side,
                'price': price, # Use current price as reference/limit
                'order_type': 'market' # Default
            })
            
        # Sort: Sells first!
        # This releases cash for buys.
        orders.sort(key=lambda x: 0 if x['side'] == 'sell' else 1)
        
        return orders
