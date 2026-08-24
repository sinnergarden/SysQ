import pandas as pd

import qsys.data.collector as collector_module
from qsys.data.collector import TushareCollector


def _collector_for_validation():
    collector = TushareCollector.__new__(TushareCollector)
    collector.store = type("Store", (), {"get_calendar": lambda self: pd.DataFrame()})()
    collector._get_expected_columns = lambda: [
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
    ]
    collector._get_numeric_columns = lambda: ["open", "high", "low", "close"]
    collector._get_stock_industry = lambda code: None
    collector._non_negative_cols = {"open", "high", "low", "close"}
    collector._signed_numeric_cols = set()
    collector._sparse_event_cols = set()
    collector._financial_sparse_by_industry = {}
    return collector


def test_cross_sectional_prices_are_not_compared_as_time_series(monkeypatch):
    collector = _collector_for_validation()
    warnings = []
    monkeypatch.setattr(collector_module.log, "warning", warnings.append)
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "trade_date": ["20260824", "20260824"],
            "open": [10.0, 100.0],
            "high": [10.0, 100.0],
            "low": [10.0, 100.0],
            "close": [10.0, 100.0],
        }
    )

    collector._validate_and_clean(frame, "ALL")

    assert not any("extreme moves" in message for message in warnings)


def test_price_moves_are_still_checked_within_each_symbol(monkeypatch):
    collector = _collector_for_validation()
    warnings = []
    monkeypatch.setattr(collector_module.log, "warning", warnings.append)
    frame = pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ"],
            "trade_date": ["20260821", "20260824"],
            "open": [10.0, 20.0],
            "high": [10.0, 20.0],
            "low": [10.0, 20.0],
            "close": [10.0, 20.0],
        }
    )

    collector._validate_and_clean(frame, "000001.SZ")

    assert any("found 1 extreme moves >25%" in message for message in warnings)
