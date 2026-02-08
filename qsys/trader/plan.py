import pandas as pd
from qsys.utils.logger import log

class PlanGenerator:
    def __init__(self, cash_buffer=0.02, min_trade_amount=5000):
        self.cash_buffer = cash_buffer
        self.min_trade_amount = min_trade_amount

    def generate_plan(self, target_weights, current_positions, total_assets, current_prices):
        """
        Generate trading plan (buy/sell list) based on target weights and current reality.
        
        1. Calc Target Value
        2. Calc Current Value
        3. Diff
        4. Sort & Filter
        """
        plan = []
        
        # Available Cash for Buying = Total Assets * (1 - Buffer) - Current Stock Value
        # Wait, Total Assets includes Cash.
        # Safe Equity = Total Assets * (1 - Buffer)
        # Actually buffer is usually for Fee.
        # Let's say we target 98% position if fully invested.
        
        # Better logic:
        # Target Value = Total Assets * Target Weight.
        # If buying, max buy = Current Cash * (1 - Buffer).
        
        # Identify all symbols
        all_symbols = set(target_weights.keys()) | set(current_positions.keys())
        
        for sym in all_symbols:
            price = current_prices.get(sym, 0)
            if price <= 0:
                log.warning(f"Skipping plan for {sym}: No price")
                continue
                
            # Target
            t_weight = target_weights.get(sym, 0.0)
            t_value = total_assets * t_weight
            
            # Current
            pos = current_positions.get(sym)
            c_amount = pos['total_amount'] if pos else 0
            c_value = c_amount * price
            
            # Diff
            diff_value = t_value - c_value
            
            # Action
            side = 'buy' if diff_value > 0 else 'sell'
            abs_diff_value = abs(diff_value)
            
            # Min Trade Filter
            if abs_diff_value < self.min_trade_amount:
                continue
                
            # Calc Amount (Lots)
            diff_amount_raw = diff_value / price
            amount_lots = int(diff_amount_raw / 100) * 100
            
            if amount_lots == 0:
                continue
                
            plan.append({
                'symbol': sym,
                'side': side,
                'price': price, # Reference price (e.g. yesterday close)
                'amount': abs(amount_lots),
                'est_value': abs(amount_lots) * price,
                'weight': t_weight
            })
            
        # Format Plan
        df_plan = pd.DataFrame(plan)
        if df_plan.empty:
            return df_plan
            
        # Sort: Sell first (descending value), then Buy (descending value)
        # Actually usually Sell first to free cash.
        df_sell = df_plan[df_plan['side'] == 'sell'].sort_values('est_value', ascending=False)
        df_buy = df_plan[df_plan['side'] == 'buy'].sort_values('est_value', ascending=False)
        
        return pd.concat([df_sell, df_buy])

    def to_markdown(self, df_plan):
        if df_plan.empty:
            return "No trades planned."
            
        return df_plan.to_markdown(index=False, floatfmt=".2f")
