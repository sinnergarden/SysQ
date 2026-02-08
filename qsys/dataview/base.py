from abc import ABC, abstractmethod
import pandas as pd

class IDataView(ABC):
    @abstractmethod
    def get_feature(self, codes, fields, start_date, end_date=None) -> pd.DataFrame:
        """
        Get features for a list of codes.
        
        Args:
            codes: list of stock codes
            fields: list of field names
            start_date: start date string (YYYYMMDD)
            end_date: end date string (YYYYMMDD)
        
        Returns:
            pd.DataFrame: MultiIndex (trade_date, ts_code) or (ts_code, trade_date)
        """
        pass
