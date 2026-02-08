from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict
import pandas as pd
from qsys.utils.logger import log

@dataclass
class Position:
    symbol: str
    total_amount: int = 0      # Total holdings
    sellable_amount: int = 0   # T+1 sellable holdings
    avg_cost: float = 0.0      # Average cost per share

class Account:
    def __init__(self, init_cash=1_000_000.0):
        self.init_cash = init_cash
        self.cash = init_cash
        self.positions: Dict[str, Position] = {} # symbol -> Position
        self.frozen_cash = 0.0 # Cash locked in pending buy orders
        
        # History for metrics
        self.history = []

    @property
    def total_assets(self):
        # Requires current prices to calculate real-time assets.
        # This is base value (Cash + Frozen).
        return self.cash + self.frozen_cash

    def record_daily(self, date, total_assets):
        """Record daily state"""
        self.history.append({
            'date': date,
            'total_assets': total_assets,
            'cash': self.cash
        })
        
    def get_metrics(self):
        """Calculate simple metrics from history"""
        if not self.history:
            return {'total_return': 0.0, 'max_drawdown': 0.0}
            
        df = pd.DataFrame(self.history)
        df['total_assets'] = df['total_assets'].astype(float)
        
        # Total Return
        start_val = self.init_cash
        end_val = df['total_assets'].iloc[-1]
        total_return = (end_val / start_val) - 1
        
        # Max Drawdown
        roll_max = df['total_assets'].cummax()
        drawdown = (df['total_assets'] - roll_max) / roll_max
        max_drawdown = drawdown.min()
        
        return {
            'total_return': total_return,
            'max_drawdown': max_drawdown
        }

    def get_market_value(self, current_prices: dict):
        mv = 0.0
        for sym, pos in self.positions.items():
            price = current_prices.get(sym, 0.0)
            mv += pos.total_amount * price
        return mv

    def get_total_equity(self, current_prices: dict):
        return self.total_assets + self.get_market_value(current_prices)

    def update_after_deal(self, symbol, amount, price, fee, side):
        """
        Update account after a trade execution.
        side: 'buy' or 'sell'
        amount: number of shares (positive)
        price: deal price
        fee: total transaction fee
        """
        cost = amount * price
        
        if side == 'buy':
            # Buy: Deduct Cash, Add Total Position (Not Sellable yet)
            total_cost = cost + fee
            self.cash -= total_cost
            
            if symbol not in self.positions:
                self.positions[symbol] = Position(symbol=symbol)
            
            pos = self.positions[symbol]
            # Update Avg Cost
            # New Avg = (Old_Total * Old_Avg + Buy_Cost) / New_Total
            old_cost = pos.total_amount * pos.avg_cost
            new_amount = pos.total_amount + amount
            pos.avg_cost = (old_cost + cost) / new_amount
            pos.total_amount = new_amount
            # Sellable amount doesn't change on T+0 buy
            
        elif side == 'sell':
            # Sell: Add Cash, Reduce Total AND Sellable
            revenue = cost - fee
            self.cash += revenue
            
            pos = self.positions[symbol]
            pos.total_amount -= amount
            pos.sellable_amount -= amount
            
            if pos.total_amount <= 0:
                del self.positions[symbol]

    def settlement(self):
        """
        Daily Settlement (End of Day).
        Move Total -> Sellable (T+1 logic).
        """
        for sym, pos in self.positions.items():
            pos.sellable_amount = pos.total_amount
            
    def __repr__(self):
        return f"Account(Cash={self.cash:.2f}, Positions={len(self.positions)})"
