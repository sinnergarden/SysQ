"""Tests for qsys.ops.market_snapshot."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pandas as pd


class TestFetchMarketSnapshot(unittest.TestCase):
    """fetch_market_snapshot — data fetching and status computation."""

    def _make_mock_market(self) -> pd.DataFrame:
        """Build a fake multi-index market DataFrame simulating QlibAdapter output."""
        import numpy as np

        idx = pd.MultiIndex.from_product(
            [["2026-05-22"], ["600001", "600002"]],
            names=["datetime", "instrument"],
        )
        return pd.DataFrame({
            "$close": [10.0, 9.5],
            "$open": [9.8, 9.3],
            "$factor": [1.0, 1.0],
            "$paused": [0, 0],
            "$high_limit": [11.0, 10.5],
            "$low_limit": [9.0, 8.5],
        }, index=idx)

    def test_fetch_snapshot_returns_tuple(self):
        """Mock QlibAdapter, verify (prices, status) returned."""
        from qsys.ops.market_snapshot import fetch_market_snapshot

        mock_market = self._make_mock_market()
        with patch("qsys.ops.market_snapshot.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.init_qlib.return_value = None
            instance.get_features.return_value = mock_market

            prices, status = fetch_market_snapshot("2026-05-22", ["600001", "600002"])

        self.assertIsInstance(prices, dict)
        self.assertIsInstance(status, pd.DataFrame)
        self.assertIn("600001", prices)
        self.assertIn("600002", prices)
        self.assertIn("is_suspended", status.columns)
        self.assertIn("is_limit_up", status.columns)
        self.assertIn("is_limit_down", status.columns)

    def test_fetch_snapshot_respects_price_col(self):
        """Verify price_col parameter is used to compute limit-up/down."""
        from qsys.ops.market_snapshot import fetch_market_snapshot

        mock_market = self._make_mock_market()
        with patch("qsys.ops.market_snapshot.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.init_qlib.return_value = None
            instance.get_features.return_value = mock_market

            prices, status = fetch_market_snapshot("2026-05-22", ["600001", "600002"], price_col="open")

        # With price_col="open", prices should be open prices
        self.assertAlmostEqual(prices["600001"], 9.8)

    def test_fetch_snapshot_no_data_raises(self):
        """No market data for trade_date raises ShadowRebalanceError."""
        from qsys.ops.market_snapshot import ShadowRebalanceError, fetch_market_snapshot

        with patch("qsys.ops.market_snapshot.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.init_qlib.return_value = None
            instance.get_features.return_value = None

            with self.assertRaises(ShadowRebalanceError):
                fetch_market_snapshot("2026-05-22", ["600001"])

    def test_fetch_snapshot_empty_frame_raises(self):
        """Empty market DataFrame raises ShadowRebalanceError."""
        from qsys.ops.market_snapshot import ShadowRebalanceError, fetch_market_snapshot

        with patch("qsys.ops.market_snapshot.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.init_qlib.return_value = None
            instance.get_features.return_value = pd.DataFrame()

            with self.assertRaises(ShadowRebalanceError):
                fetch_market_snapshot("2026-05-22", ["600001"])

    def test_fetch_snapshot_handles_suspended(self):
        """Verify is_suspended flag is correctly computed."""
        from qsys.ops.market_snapshot import fetch_market_snapshot

        import numpy as np

        idx = pd.MultiIndex.from_product(
            [["2026-05-22"], ["600001", "600002"]],
            names=["datetime", "instrument"],
        )
        mock_market = pd.DataFrame({
            "$close": [10.0, 9.5],
            "$open": [9.8, 9.3],
            "$factor": [1.0, 1.0],
            "$paused": [1, 0],  # 600001 is suspended
            "$high_limit": [11.0, 10.5],
            "$low_limit": [9.0, 8.5],
        }, index=idx)

        with patch("qsys.ops.market_snapshot.QlibAdapter") as MockAdapter:
            instance = MockAdapter.return_value
            instance.init_qlib.return_value = None
            instance.get_features.return_value = mock_market

            _, status = fetch_market_snapshot("2026-05-22", ["600001", "600002"])

        self.assertTrue(status.loc["600001", "is_suspended"])
        self.assertFalse(status.loc["600002", "is_suspended"])


if __name__ == "__main__":
    unittest.main()
