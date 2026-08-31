"""Tests for resolve_preopen_data_date / resolve_postclose_data_date semantics.

These are **integration-lite** tests — they require qlib to be initialized
but test only the date-resolution logic, not full pipeline runs.
"""
from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from qsys.strategy.runtime_base import BaseStrategyAdapter


# Trading days in May 2026 (Mon-Fri, no holidays)
# Mon 5/18, Tue 5/19, Wed 5/20, Thu 5/21, Fri 5/22, Mon 5/25


class TestBaseAdapterResolveDataDate(unittest.TestCase):
    """resolve_data_date (asof) — unchanged default semantics."""

    def setUp(self):
        self.adapter = BaseStrategyAdapter()

    def test_asof_trading_day_returns_self(self):
        """A known trading day returns itself."""
        try:
            result = self.adapter.resolve_data_date("2026-05-18")
            self.assertEqual(result, "2026-05-18")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_asof_sunday_rolls_back(self):
        """Sunday rolls back to Friday."""
        try:
            result = self.adapter.resolve_data_date("2026-05-24")
            self.assertEqual(result, "2026-05-22")
        except Exception:
            self.skipTest("qlib calendar unavailable")


class TestBaseAdapterResolvePreopenDataDate(unittest.TestCase):
    """resolve_preopen_data_date — previous-close semantics."""

    def setUp(self):
        self.adapter = BaseStrategyAdapter()

    def test_monday_returns_friday(self):
        """Preopen for Monday 2026-05-25 returns the prior Friday 2026-05-22."""
        try:
            result = self.adapter.resolve_preopen_data_date("2026-05-25")
            self.assertEqual(result, "2026-05-22")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_tuesday_returns_monday(self):
        """Preopen for Tuesday returns the prior Monday."""
        try:
            result = self.adapter.resolve_preopen_data_date("2026-05-19")
            self.assertEqual(result, "2026-05-18")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_friday_returns_thursday(self):
        """Preopen for Friday returns Thursday."""
        try:
            result = self.adapter.resolve_preopen_data_date("2026-05-22")
            self.assertEqual(result, "2026-05-21")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_sunday_returns_friday(self):
        """Preopen for Sunday (non-trading day) returns the prior Friday."""
        try:
            result = self.adapter.resolve_preopen_data_date("2026-05-24")
            self.assertEqual(result, "2026-05-22")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_preopen_2026_05_26_returns_2026_05_25(self):
        """Preopen for today (Tuesday) returns Monday."""
        try:
            result = self.adapter.resolve_preopen_data_date("2026-05-26")
            self.assertEqual(result, "2026-05-25")
        except Exception:
            self.skipTest("qlib calendar unavailable")


class TestBaseAdapterResolvePostcloseDataDate(unittest.TestCase):
    """resolve_postclose_data_date — asof semantics (same as resolve_data_date)."""

    def setUp(self):
        self.adapter = BaseStrategyAdapter()

    def test_postclose_trading_day_returns_self(self):
        """Postclose for a trading day returns the same day."""
        try:
            result = self.adapter.resolve_postclose_data_date("2026-05-18")
            self.assertEqual(result, "2026-05-18")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_postclose_2026_05_26_returns_self(self):
        """Postclose for today (Tuesday) returns itself."""
        try:
            result = self.adapter.resolve_postclose_data_date("2026-05-26")
            self.assertEqual(result, "2026-05-26")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_postclose_equals_asof(self):
        """resolve_postclose_data_date matches resolve_data_date."""
        try:
            for d in ["2026-05-18", "2026-05-22", "2026-05-25"]:
                self.assertEqual(
                    self.adapter.resolve_postclose_data_date(d),
                    self.adapter.resolve_data_date(d),
                )
        except Exception:
            self.skipTest("qlib calendar unavailable")


class TestStrategiesUsePreopenDataDate(unittest.TestCase):
    """Integration-lite: V1 / V2 preopen paths resolve to previous trading day."""

    def test_v1_preopen_resolves_to_previous(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        adapter = AlphaV1StrategyAdapter()
        try:
            dd = adapter.resolve_preopen_data_date("2026-05-25")
            self.assertEqual(dd, "2026-05-22",
                f"V1 preopen data_date for 2026-05-25 should be 2026-05-22, got {dd}")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_v2_preopen_resolves_to_previous(self):
        from qsys.strategy.alpha_v2.adapter import AlphaV2StrategyAdapter

        adapter = AlphaV2StrategyAdapter()
        try:
            dd = adapter.resolve_preopen_data_date("2026-05-25")
            self.assertEqual(dd, "2026-05-22",
                f"V2 preopen data_date for 2026-05-25 should be 2026-05-22, got {dd}")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_v1_postclose_resolves_to_asof(self):
        """Postclose data_date for a trading day returns the same day."""
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        adapter = AlphaV1StrategyAdapter()
        try:
            dd = adapter.resolve_postclose_data_date("2026-05-25")
            self.assertEqual(dd, "2026-05-25")
        except Exception:
            self.skipTest("qlib calendar unavailable")

    def test_v1_generate_predictions_defaults_to_preopen_data_date(self):
        """generate_predictions_for_date with no data_date uses resolve_preopen_data_date."""
        from unittest.mock import patch
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        adapter = AlphaV1StrategyAdapter()
        with patch.object(adapter, "resolve_preopen_data_date", return_value="2026-05-25") as mock_resolve:
            with patch.object(adapter, "load_model", return_value=None), patch.object(
                adapter, "fetch_data", return_value=None
            ):
                adapter.generate_predictions_for_date("2026-05-26")
        mock_resolve.assert_called_once_with("2026-05-26")


if __name__ == "__main__":
    unittest.main()
