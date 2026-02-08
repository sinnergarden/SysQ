import pandas as pd
import numpy as np
import re
from qsys.utils.logger import log

class FeatureCalculator:
    """
    Pure Python implementation of Qlib operators for Inference.
    Ensures consistency with Qlib's C++ engine.
    """
    
    @staticmethod
    def calculate(df: pd.DataFrame, expressions: list) -> pd.DataFrame:
        """
        Calculate features based on Qlib expressions.
        df index should be (trade_date, ts_code) or (ts_code, trade_date).
        We expect data to be sorted by date for each stock.
        """
        # Ensure index is sorted by date within each stock
        # But usually df passed here is for a specific window, possibly single stock or multi stock.
        # If multi-stock, we need to groupby.
        
        # Check index structure
        is_multi_index = isinstance(df.index, pd.MultiIndex)
        if not is_multi_index:
             # Assume single stock, simple index or no index?
             # Let's assume standard format from DataView
             pass

        # We need (ts_code, trade_date) sorting for rolling ops
        # If it's (trade_date, ts_code), swap level
        df_calc = df.copy()
        if is_multi_index and df_calc.index.names == ['trade_date', 'ts_code']:
            df_calc = df_calc.swaplevel('trade_date', 'ts_code').sort_index()
        elif is_multi_index and df_calc.index.names == ['ts_code', 'trade_date']:
            df_calc = df_calc.sort_index()
        else:
            # Fallback or assume single stock sorted by date
            pass

        result_df = pd.DataFrame(index=df_calc.index)
        
        for expr in expressions:
            try:
                # 0. Fast path: if expr is already a column, use it
                if expr in df.columns:
                    result_df[expr] = df[expr]
                    continue

                # Basic parsing
                col_name = expr
                # Supports: Ref, Mean, Std, basic arithmetic
                # Example: Ref($close, 1) -> shift 1
                # Example: $close / Ref($close, 1) - 1
                
                # We use a simple eval-based approach with custom locals for operators
                # This is a simplified implementation. Full parser is complex.
                # Qlib actually allows using `qlib.data.dataset.loader.QlibDataLoader` even for inference?
                # No, Qlib relies on bin files. We want pure python here.
                
                col_name = expr
                # We define functions for Ref, Mean, etc. that work on Series (with GroupBy if needed)
                
                # For simplicity, let's implement a few core operators and use eval
                # But eval on large df is slow. 
                # Better: Parse the expression and apply ops vectorially.
                
                # Implementation Strategy:
                # Use regex to find function calls like Ref(..., N)
                # This is hard to do robustly with regex.
                # For now, let's assume we map standard Qlib feature names to our implementation 
                # OR we implement a restricted set.
                
                # Let's try to map the expression to a pandas operation series.
                # "Ref($close, 1)"
                
                series = FeatureCalculator._eval_expr(df_calc, expr)
                result_df[col_name] = series
                
            except Exception as e:
                col_name = expr
                log.error(f"Failed to calculate feature {expr}: {e}")
                result_df[col_name] = np.nan
                
        # Restore index order if needed (to match input)
        if is_multi_index and df.index.names == ['trade_date', 'ts_code']:
             result_df = result_df.swaplevel('ts_code', 'trade_date').sort_index()
             
        return result_df

    @staticmethod
    def _eval_expr(df, expr):
        allowed_funcs = {"Ref", "Mean", "Std", "Max", "Min"}
        if "__" in expr:
            raise ValueError("Invalid expression")
        if re.search(r"[^\w\$\s\+\-\*\/\(\)\.,]", expr):
            raise ValueError("Invalid expression")
        funcs = re.findall(r"([A-Za-z_]\w*)\s*\(", expr)
        for func in funcs:
            if func not in allowed_funcs:
                raise ValueError(f"Unsupported function: {func}")
        fields = re.findall(r"\$(\w+)", expr)
        for field in fields:
            if field not in df.columns:
                raise ValueError(f"Unknown field: {field}")

        has_group = isinstance(df.index, pd.MultiIndex) and 'ts_code' in df.index.names

        def Ref(series, d):
            if has_group:
                return series.groupby(level='ts_code').shift(d)
            return series.shift(d)
            
        def Mean(series, d):
            if has_group:
                return series.groupby(level='ts_code').rolling(d).mean().reset_index(level=0, drop=True)
            return series.rolling(d).mean()
            
        def Std(series, d):
            if has_group:
                return series.groupby(level='ts_code').rolling(d).std().reset_index(level=0, drop=True)
            return series.rolling(d).std()
            
        def Max(series, d):
            if has_group:
                return series.groupby(level='ts_code').rolling(d).max().reset_index(level=0, drop=True)
            return series.rolling(d).max()
            
        def Min(series, d):
            if has_group:
                return series.groupby(level='ts_code').rolling(d).min().reset_index(level=0, drop=True)
            return series.rolling(d).min()

        # Replace $field with "df['field']"
        # Regex to match $word
        py_expr = re.sub(r'\$(\w+)', r"df['\1']", expr)
        
        # Eval with context
        # Note: reset_index in rolling ops is needed because rolling adds a level? 
        # Actually groupby().rolling() adds the grouping key to index. 
        # If original index is (ts_code, date), rolling gives (ts_code, date).
        # We need to ensure alignment.
        
        local_dict = {
            'Ref': Ref,
            'Mean': Mean,
            'Std': Std,
            'Max': Max,
            'Min': Min,
            'df': df
        }
        
        return eval(py_expr, {"__builtins__": None}, local_dict)
