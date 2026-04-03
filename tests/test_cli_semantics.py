import unittest
from unittest.mock import patch

from qsys.data.adapter import QlibAdapter
import scripts.run_daily_trading as run_daily_trading
from scripts.run_daily_trading import resolve_signal_and_execution_date
from scripts.run_update import _normalize_date


class TestCliSemantics(unittest.TestCase):
    def test_run_update_accepts_dash_date(self):
        self.assertEqual(_normalize_date("2023-01-01"), "20230101")
        self.assertEqual(_normalize_date("20230101"), "20230101")

    @patch("scripts.run_daily_trading.datetime")
    def test_future_date_is_treated_as_execution_date(self, mock_datetime):
        QlibAdapter().init_qlib()
        mock_now = unittest.mock.Mock()
        mock_now.strftime.return_value = "2026-03-21"
        mock_datetime.now.return_value = mock_now
        signal_date, execution_date = resolve_signal_and_execution_date("2026-03-23", None)
        self.assertEqual(signal_date, "2026-03-20")
        self.assertEqual(execution_date, "2026-03-23")

    def test_explicit_execution_date_keeps_signal_date(self):
        signal_date, execution_date = resolve_signal_and_execution_date("2026-03-20", "2026-03-23")
        self.assertEqual(signal_date, "2026-03-20")
        self.assertEqual(execution_date, "2026-03-23")


if __name__ == "__main__":
    unittest.main()
