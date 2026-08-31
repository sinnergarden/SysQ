from __future__ import annotations

import numpy as np
import pandas as pd

from qsys.feature.groups.relative_strength import build_relative_strength_features


def test_index_relative_return_uses_trading_dates_not_panel_rows() -> None:
    dates = pd.bdate_range("2025-01-02", periods=6)
    rows = []
    for instrument, scale in (("AAA", 1.0), ("BBB", 2.0)):
        for position, date in enumerate(dates):
            rows.append(
                {
                    "trade_date": date,
                    "ts_code": instrument,
                    "close": scale * (100.0 + 2.0 * position),
                    "index_close": 100.0 + position,
                    "volume": 100.0,
                    "amount": 1_000.0,
                }
            )

    result = build_relative_strength_features(pd.DataFrame(rows))
    final = result[result["trade_date"].eq(dates[-1])]
    expected = (110.0 / 104.0 - 1.0) - (105.0 / 102.0 - 1.0)

    assert np.allclose(final["stock_minus_index_ret_3d"], expected)


def test_flat_sessions_are_not_counted_as_down_volume() -> None:
    dates = pd.bdate_range("2025-01-02", periods=61)
    closes = np.full(len(dates), 101.0)
    closes[0] = 100.0
    closes[-1] = 100.0
    volume = np.full(len(dates), 1_000.0)
    volume[1] = 100.0
    volume[-1] = 50.0
    frame = pd.DataFrame(
        {
            "trade_date": dates,
            "ts_code": "AAA",
            "close": closes,
            "volume": volume,
            "amount": volume * closes,
        }
    )

    result = build_relative_strength_features(frame)

    assert np.isclose(result.iloc[-1]["volume_up_down_ratio_60d"], 2.0)
