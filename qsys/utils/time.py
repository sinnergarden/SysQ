import pandas as pd

class TimeUtils:
    @staticmethod
    def get_trading_days(start_date, end_date):
        # Placeholder for trading calendar logic
        return pd.date_range(start_date, end_date, freq='B')

    @staticmethod
    def next_trading_day(current_date):
        return current_date + pd.Timedelta(days=1)
