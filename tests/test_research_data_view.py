"""Tests for ResearchDataView and ResearchCockpitRepository data layer fixes."""
import unittest
from unittest.mock import patch

import pandas as pd

from qsys.dataview.research import ResearchDataView
from qsys.research_ui.assembler import ResearchCockpitRepository


def _make_feather_df(trade_dates: list[str]) -> pd.DataFrame:
    """Simulate a DataFrame as returned by StockDataStore.load_daily() — YYYYMMDD dates."""
    return pd.DataFrame({
        "trade_date": trade_dates,
        "ts_code": ["000001.SZ"] * len(trade_dates),
        "open": [10.0] * len(trade_dates),
        "high": [11.0] * len(trade_dates),
        "low": [9.0] * len(trade_dates),
        "close": [10.5] * len(trade_dates),
        "volume": [1_000_000] * len(trade_dates),
    })


class TestDateNormalization(unittest.TestCase):
    """Regression: date format mismatch between feather (YYYYMMDD) and API (YYYY-MM-DD).

    _load_single_stock normalizes df.trade_date from YYYYMMDD → YYYY-MM-DD, then
    filters by string comparison against start_date/end_date. If the input dates
    are still in YYYYMMDD format, string comparison silently drops all rows.
    """

    def setUp(self):
        self.view = ResearchDataView(n_jobs=1)

    def test_yyyymmdd_input_filters_out_all_data(self):
        """YYYYMMDD input dates produce incorrect string comparison against YYYY-MM-DD dates."""
        df = _make_feather_df(["20100105", "20100106"])
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock("000001.SZ", ["open", "close"],
                                                  "20100105", "20100106")
        self.assertIsNone(result,
                          "YYYYMMDD input + YYYY-MM-DD df dates → string comparison fails")

    def test_yyyymmdd_input_still_fails_even_if_df_pre_normalized(self):
        """Even pre-normalized df + YYYYMMDD input fails: '-' < '0' in string comparison."""
        df = _make_feather_df(["20100105"])
        df["trade_date"] = pd.to_datetime(
            df["trade_date"], format="%Y%m%d"
        ).dt.strftime("%Y-%m-%d")
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock("000001.SZ", ["open", "close"],
                                                  "20100105", "20100106")
        self.assertIsNone(result)

    def test_yyyymmdd_input_works_when_input_matches_df_format(self):
        """If df trade_date is still YYYYMMDD (no normalization), string comparison works."""
        df = _make_feather_df(["20100105"])
        # Skip normalization by not calling _load_single_stock; verify the comparison directly
        # normalize: 20100105 -> 2010-01-05
        df["trade_date"] = pd.to_datetime(
            df["trade_date"].astype(str), format="%Y%m%d", errors='coerce'
        ).dt.strftime('%Y-%m-%d')
        # Now df has YYYY-MM-DD, but we pass YYYYMMDD input
        mask = df["trade_date"] >= "20100105"
        # Proves the string comparison fails
        self.assertEqual(mask.sum(), 0,
                         "2010-01-05 >= 20100105 is False in string comparison")

    def test_normalized_input_preserves_data(self):
        """YYYY-MM-DD input dates match correctly against normalized df dates."""
        df = _make_feather_df(["20100105", "20100106"])
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock("000001.SZ", ["open", "close"],
                                                  "2010-01-05", "2010-01-06")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_normalized_input_partial_range(self):
        """Sub-range filtering works with YYYY-MM-DD input."""
        df = _make_feather_df(["20100105", "20100106", "20100107"])
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock("000001.SZ", ["open", "close"],
                                                  "2010-01-06", "2010-01-07")
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 2)

    def test_assembler_normalize_trade_date_value(self):
        """_normalize_trade_date_value converts YYYYMMDD → YYYY-MM-DD."""
        repo = ResearchCockpitRepository()
        self.assertEqual(repo._normalize_trade_date_value("20100105"), "2010-01-05")
        self.assertEqual(repo._normalize_trade_date_value("2010-01-05"), "2010-01-05")
        self.assertEqual(repo._normalize_trade_date_value(""), "")
        self.assertEqual(repo._normalize_trade_date_value(None), "")


class TestColumnCoalesce(unittest.TestCase):
    """Enhanced coalesce: handle missing target columns gracefully."""

    _DATA_POOL = {
        "trade_date": ["20250103"],
        "ts_code": ["000001.SZ"],
        "open": [10.0],
        "high": [11.0],
        "low": [9.0],
        "close": [10.5],
        "close_x": [10.5],
        "volume": [1_000_000],
        "vol": [1_000_000],
    }

    def setUp(self):
        self.view = ResearchDataView(n_jobs=1)

    def _df_from_cols(self, columns: list[str]) -> pd.DataFrame:
        return pd.DataFrame(
            {col: self._DATA_POOL[col] for col in columns if col in self._DATA_POOL}
        )

    def test_volume_created_from_vol_when_volume_missing(self):
        """volume absent, vol present → should create volume from vol."""
        df = self._df_from_cols(["trade_date", "ts_code", "open", "high", "low", "vol"])
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock(
                "000001.SZ", ["close", "volume"], "2025-01-03", "2025-01-03"
            )
        self.assertIsNotNone(result)
        self.assertIn("volume", result.columns)
        self.assertEqual(result["volume"].iloc[0], 1_000_000)

    def test_close_created_from_close_x_when_close_missing(self):
        """close absent, close_x present → should create close from close_x."""
        df = self._df_from_cols(["trade_date", "ts_code", "open", "high", "low", "close_x"])
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock(
                "000001.SZ", ["close"], "2025-01-03", "2025-01-03"
            )
        self.assertIsNotNone(result)
        self.assertIn("close", result.columns)
        self.assertEqual(result["close"].iloc[0], 10.5)

    def test_coalesce_fills_nan_when_both_present(self):
        """If both target and source exist, combine_first fills NaN from source."""
        df = self._df_from_cols(
            ["trade_date", "ts_code", "open", "high", "low", "close", "close_x"]
        )
        df.loc[0, "close"] = None
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock(
                "000001.SZ", ["close"], "2025-01-03", "2025-01-03"
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["close"].iloc[0], 10.5)

    def test_coalesce_skips_when_neither_target_nor_source_exists(self):
        """No error when both target and source are absent."""
        df = self._df_from_cols(["trade_date", "ts_code", "open", "high", "low"])
        with patch.object(self.view.store, "load_daily", return_value=df):
            result = self.view._load_single_stock(
                "000001.SZ", ["close", "volume"], "2025-01-03", "2025-01-03"
            )
        self.assertIsNotNone(result)  # should still succeed, columns will just be absent


if __name__ == "__main__":
    unittest.main()
