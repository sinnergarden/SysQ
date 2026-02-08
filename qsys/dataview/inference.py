from .base import IDataView
import pandas as pd
import numpy as np
from qsys.data.storage import StockDataStore
from qsys.utils.logger import log

class InferenceDataView(IDataView):
    def __init__(self):
        self.store = StockDataStore()
        
    def get_feature(self, codes, fields, start_date, end_date=None) -> pd.DataFrame:
        # Basic implementation reusing loading logic or strict check
        # For inference, we usually use get_window_data
        # But we implement this for compatibility
        return self._load_data(codes, fields, start_date, end_date)

    def get_window_data(self, codes, fields, as_of_date, window_size):
        """
        Get data for a specific window ending at as_of_date.
        Handles lookback calculation and padding.
        """
        # 1. Calculate Start Date
        start_date = self._calculate_start_date(as_of_date, window_size)
        log.info(f"Inference Window: {start_date} to {as_of_date} (Window: {window_size})")
        
        # 2. Load Data
        df = self._load_data(codes, fields, start_date, as_of_date)
        
        if df.empty:
            return None

        # 3. Pivot and Pad
        # We need a dense tensor-like structure: (Time, Stock, Feature)
        # But DataFrame is 2D. 
        # We ensure that for each stock, we have exactly 'window_size' rows? 
        # Or just 'trading days in range'?
        # The requirement says "停牌填充 (Padding)... 保证输出的 Tensor 形状是规整的".
        
        # Get all trading dates in range
        calendar = self.store.get_calendar()
        trading_dates = calendar[(calendar['cal_date'] >= start_date) & 
                                 (calendar['cal_date'] <= as_of_date) & 
                                 (calendar['is_open'] == 1)]['cal_date'].tolist()
        
        # If window_size > len(trading_dates), we might need more history? 
        # Assuming window_size is number of trading days.
        # If we calculated start_date correctly, len(trading_dates) should be >= window_size.
        
        # Create a MultiIndex of (Date, Code) for all combinations
        full_idx = pd.MultiIndex.from_product([trading_dates, codes], names=['trade_date', 'ts_code'])
        
        # Reindex
        # df index is (trade_date, ts_code) from _load_data
        df_padded = df.reindex(full_idx)
        
        # Fill NA (Forward Fill then Fill 0)
        # Group by code and ffill?
        # Reindex puts NaNs.
        # We need to sort by code, date to ffill correctly?
        # Or swap level to (ts_code, trade_date)
        
        df_padded = df_padded.swaplevel('trade_date', 'ts_code').sort_index()
        
        # Forward fill within each stock group
        # groupby apply is slow.
        # Since it's sorted by code, date, we can just ffill? 
        # But we must not bleed data from one stock to another.
        # Actually `df_padded` is (ts_code, trade_date). 
        # We can use groupby().ffill()
        
        df_padded = df_padded.groupby(level='ts_code').ffill()
        df_padded = df_padded.fillna(0) # Remaining NaNs (start of window) fill with 0
        
        # Swap back to (trade_date, ts_code) if needed, or keep as is.
        # Usually for tensor construction (Batch, Time, Feat) -> (Code, Date, Feat)
        
        return df_padded

    def _calculate_start_date(self, end_date, window_size):
        calendar = self.store.get_calendar()
        # Filter open days <= end_date
        open_days = calendar[(calendar['cal_date'] <= end_date) & (calendar['is_open'] == 1)]
        open_days = open_days.sort_values('cal_date', ascending=False)
        
        if len(open_days) < window_size:
            log.warning("Not enough history for window, taking available")
            return open_days.iloc[-1]['cal_date']
            
        return open_days.iloc[window_size - 1]['cal_date']

    def _load_data(self, codes, fields, start_date, end_date):
        # Similar to ResearchDataView but maybe single threaded for simplicity or reuse
        # Ideally reuse logic. For now, simple loop.
        
        dfs = []
        for code in codes:
            df = self.store.load_daily(code)
            if df is None:
                continue
                
            mask = (df['trade_date'] >= start_date)
            if end_date:
                mask &= (df['trade_date'] <= end_date)
            df = df[mask].copy()
            
            if df.empty:
                continue

            # Forward Adjustment (Same as Research)
            if 'adj_factor' in df.columns:
                latest_factor = df['adj_factor'].iloc[-1] if not df['adj_factor'].isna().all() else 1.0
                if not latest_factor or latest_factor == 0:
                    latest_factor = 1.0
                ratio = df['adj_factor'] / latest_factor
                if 'adj_close' in fields and 'adj_close' not in df.columns and 'close' in df.columns:
                    df['adj_close'] = df['close'] * ratio
                if 'adj_open' in fields and 'adj_open' not in df.columns and 'open' in df.columns:
                    df['adj_open'] = df['open'] * ratio
                if 'adj_high' in fields and 'adj_high' not in df.columns and 'high' in df.columns:
                    df['adj_high'] = df['high'] * ratio
                if 'adj_low' in fields and 'adj_low' not in df.columns and 'low' in df.columns:
                    df['adj_low'] = df['low'] * ratio

            available_fields = [f for f in fields if f in df.columns]
            cols_to_keep = list(set(['trade_date', 'ts_code'] + available_fields))
            dfs.append(df[cols_to_keep])
            
        if not dfs:
            return pd.DataFrame()
            
        full_df = pd.concat(dfs, ignore_index=True)
        return full_df.set_index(['trade_date', 'ts_code']).sort_index()
