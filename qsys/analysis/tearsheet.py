import pandas as pd
import numpy as np
from qsys.utils.logger import log

class PerformanceAnalyzer:
    @staticmethod
    def show(daily_df, benchmark_df=None):
        """
        Calculate and print performance metrics.
        daily_df: DataFrame with columns ['date', 'total_assets', 'daily_fee', 'daily_turnover']
        """
        df = daily_df.copy()
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        
        # 1. Returns
        df['return'] = df['total_assets'].pct_change().fillna(0.0)
        
        # 2. Basic Metrics
        total_days = len(df)
        if total_days < 2:
            log.warning("Not enough data for analysis")
            return

        # Total Return
        start_assets = df['total_assets'].iloc[0]
        end_assets = df['total_assets'].iloc[-1]
        total_return = (end_assets / start_assets) - 1
        
        # Annualized
        ann_factor = 252
        ann_return = (1 + total_return) ** (ann_factor / total_days) - 1
        
        # Volatility
        daily_std = df['return'].std()
        ann_vol = daily_std * np.sqrt(ann_factor)
        
        # Sharpe (Rf=0 for simplicity)
        sharpe = (df['return'].mean() / daily_std) * np.sqrt(ann_factor) if daily_std > 0 else 0
        
        # Max Drawdown
        cum_max = df['total_assets'].cummax()
        drawdown = (df['total_assets'] - cum_max) / cum_max
        max_dd = drawdown.min()
        
        # 3. Transaction Costs
        total_fee = df['daily_fee'].sum()
        total_turnover = df['daily_turnover'].sum()
        avg_assets = df['total_assets'].mean()
        
        fee_ratio = total_fee / total_turnover if total_turnover > 0 else 0
        
        # 4. Win Rate (Daily)
        win_days = len(df[df['return'] > 0])
        loss_days = len(df[df['return'] < 0])
        win_rate_daily = win_days / (win_days + loss_days) if (win_days + loss_days) > 0 else 0
        
        # 5. Output
        print("\n" + "="*40)
        print(f" Performance Summary ({df.index[0].date()} to {df.index[-1].date()})")
        print("="*40)
        print(f"Total Return      : {total_return:>10.2%}")
        print(f"Annualized Return : {ann_return:>10.2%}")
        print(f"Annualized Vol    : {ann_vol:>10.2%}")
        print(f"Sharpe Ratio      : {sharpe:>10.4f}")
        print(f"Max Drawdown      : {max_dd:>10.2%}")
        print("-" * 40)
        print(f"Total Trade Count : {df['trade_count'].sum():>10}")
        print(f"Avg Daily Turnover: {df['daily_turnover'].mean():>10.2f}")
        print(f"Total Fees Paid   : {total_fee:>10.2f}")
        print(f"Avg Fee Rate      : {fee_ratio:>10.4%}")
        print(f"Daily Win Rate    : {win_rate_daily:>10.2%}")
        print("="*40 + "\n")
        
        return {
            "total_return": total_return,
            "sharpe": sharpe,
            "max_dd": max_dd,
            "total_fee": total_fee
        }
