from .base import IDataView
import pandas as pd
from joblib import Parallel, delayed
from qsys.data.storage import StockDataStore
from qsys.utils.logger import log

class ResearchDataView(IDataView):
    def __init__(self, n_jobs=-1):
        self.store = StockDataStore()
        self.n_jobs = n_jobs

    def _load_single_stock(self, code, fields, start_date, end_date):
        df = self.store.load_daily(code)
        if df is None:
            return None
            
        # Filter by date
        mask = (df['trade_date'] >= start_date)
        if end_date:
            mask &= (df['trade_date'] <= end_date)
        df = df[mask].copy()
        
        if df.empty:
            return None
            
        # Forward Adjustment using latest adj_factor
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

        # Select fields
        # Always keep key columns for index
        available_fields = [f for f in fields if f in df.columns]
        cols_to_keep = list(set(['trade_date', 'ts_code'] + available_fields))
        
        return df[cols_to_keep]

    def get_feature(self, codes, fields, start_date, end_date=None) -> pd.DataFrame:
        log.info(f"Loading data for {len(codes)} stocks from {start_date} to {end_date}")
        
        results = Parallel(n_jobs=self.n_jobs)(
            delayed(self._load_single_stock)(code, fields, start_date, end_date) 
            for code in codes
        )
        
        # Filter None
        dfs = [df for df in results if df is not None]
        
        if not dfs:
            log.warning("No data found")
            return pd.DataFrame()
            
        # Concat
        full_df = pd.concat(dfs, ignore_index=True)
        
        # Set Index
        full_df = full_df.set_index(['trade_date', 'ts_code']).sort_index()
        
        return full_df
