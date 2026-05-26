"""Tests for BaseStrategyAdapter — shared utility methods for strategy adapters."""
from __future__ import annotations

import csv
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.strategy.runtime_base import BaseStrategyAdapter


class _ConcreteAdapter(BaseStrategyAdapter):
    """Minimal concrete subclass for testing base methods."""

    def __init__(self, project_root: Path, predictions_dir: Path) -> None:
        super().__init__()
        self._project_root = project_root
        self._predictions_dir_val = predictions_dir

    @property
    def _predictions_dir(self) -> Path:
        return self._predictions_dir_val


class TestGetStockName(unittest.TestCase):
    """get_stock_name fallback and caching behavior."""

    def test_fallback_when_csv_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            self.assertEqual(adapter.get_stock_name("000001.SZ"), "000001.SZ")

    def test_loads_names_from_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            # Write stock_names.csv
            csv_path = root / "data" / "stock_names.csv"
            csv_path.parent.mkdir(parents=True)
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ts_code", "name"])
                w.writerow(["000001.SZ", "平安银行"])
                w.writerow(["000002.SZ", "万科A"])
            adapter = _ConcreteAdapter(root, pred)
            self.assertEqual(adapter.get_stock_name("000001.SZ"), "平安银行")
            self.assertEqual(adapter.get_stock_name("000002.SZ"), "万科A")

    def test_cache_avoids_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            csv_path = root / "data" / "stock_names.csv"
            csv_path.parent.mkdir(parents=True)
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ts_code", "name"])
                w.writerow(["000001.SZ", "平安银行"])
            adapter = _ConcreteAdapter(root, pred)
            # First call loads cache
            self.assertEqual(adapter.get_stock_name("000001.SZ"), "平安银行")
            # Delete csv and verify cache still works
            csv_path.unlink()
            self.assertEqual(adapter.get_stock_name("000001.SZ"), "平安银行")

    def test_unknown_code_returns_code(self):
        """Unknown stock codes should fall back to the code itself."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            csv_path = root / "data" / "stock_names.csv"
            csv_path.parent.mkdir(parents=True)
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["ts_code", "name"])
                w.writerow(["000001.SZ", "平安银行"])
            adapter = _ConcreteAdapter(root, pred)
            self.assertEqual(adapter.get_stock_name("999999.SZ"), "999999.SZ")


class TestPrintPredictionsSummary(unittest.TestCase):
    """print_predictions_summary output format."""

    def test_prints_top_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            df = pd.DataFrame({
                "instrument": [f"STOCK{i:04d}" for i in range(10)],
                "score": [float(i) for i in range(10)],
            })
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                adapter.print_predictions_summary(df)
            output = buf.getvalue()
            # Should contain at least the top 5 entries
            self.assertIn("#1", output)
            self.assertIn("STOCK0009", output)  # highest score (9)
            # Should NOT contain #6 (only top 5)
            self.assertNotIn("#6", output)

    def test_empty_predictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            df = pd.DataFrame({"instrument": [], "score": []})
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                adapter.print_predictions_summary(df)
            self.assertEqual(buf.getvalue(), "")


class TestLoadPlanInstruments(unittest.TestCase):
    """load_plan_instruments reads order_intents.csv."""

    def test_returns_empty_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            self.assertEqual(adapter.load_plan_instruments(tmp), [])

    def test_returns_sorted_instruments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            plan_dir = Path(tmp) / "plan"
            plan_dir.mkdir()
            intents = plan_dir / "order_intents.csv"
            with open(intents, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["instrument", "quantity"])
                w.writerow(["000003.SZ", "100"])
                w.writerow(["000001.SZ", "200"])
                w.writerow(["000002.SZ", "300"])
            adapter = _ConcreteAdapter(root, pred)
            self.assertEqual(
                adapter.load_plan_instruments(plan_dir),
                ["000001.SZ", "000002.SZ", "000003.SZ"],
            )


class TestSavePredictions(unittest.TestCase):
    """save_predictions writes CSV to _predictions_dir."""

    def test_writes_csv_to_predictions_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred_dir = root / "predictions"
            pred_dir.mkdir(parents=True)
            adapter = _ConcreteAdapter(root, pred_dir)
            df = pd.DataFrame({
                "trade_date": ["2026-05-26", "2026-05-26"],
                "instrument": ["000001.SZ", "000002.SZ"],
                "score": [0.5, -0.3],
            })
            adapter.save_predictions(df, root, "2026-05-26")
            expected_path = pred_dir / "predictions_2026-05-26.csv"
            self.assertTrue(expected_path.exists())
            loaded = pd.read_csv(expected_path)
            self.assertEqual(len(loaded), 2)
            self.assertIn("instrument", loaded.columns)


class TestFetchOpenPrices(unittest.TestCase):
    """fetch_open_prices with mocked QlibAdapter."""

    def _mock_adapter(self, mock_get_features):
        """Configure a mock QlibAdapter.get_features return value."""
        import pandas as pd

        idx = pd.MultiIndex.from_tuples(
            [
                ("000001.SZ", pd.Timestamp("2026-05-26 09:30:00")),
                ("000002.SZ", pd.Timestamp("2026-05-26 09:31:00")),
            ],
            names=["instrument", "datetime"],
        )
        mock_get_features.return_value = pd.DataFrame(
            {"$open": [12.5, 25.0]},
            index=idx,
        )

    @patch("qsys.data.adapter.QlibAdapter")
    def test_returns_open_price_dict(self, MockAdapter):
        mock_instance = MockAdapter.return_value
        self._mock_adapter(mock_instance.get_features)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            result = adapter.fetch_open_prices(
                "2026-05-26", ["000001.SZ", "000002.SZ"]
            )
            self.assertEqual(result["000001.SZ"], 12.5)
            self.assertEqual(result["000002.SZ"], 25.0)

    @patch("qsys.data.adapter.QlibAdapter")
    def test_empty_when_no_data(self, MockAdapter):
        mock_instance = MockAdapter.return_value
        mock_instance.get_features.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            result = adapter.fetch_open_prices(
                "2026-05-26", ["000001.SZ"]
            )
            self.assertEqual(result, {})


class TestSendNotification(unittest.TestCase):
    """send_notification with monkeypatched telegram sender."""

    @patch("qsys.ops.telegram.send_telegram_message")
    def test_sends_message(self, mock_send):
        mock_send.return_value = {"status": "success"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                adapter.send_notification("Hello")
            mock_send.assert_called_once_with("Hello")
            self.assertIn("Telegram", buf.getvalue())

    @patch("qsys.ops.telegram.send_telegram_message")
    def test_skipped_message(self, mock_send):
        mock_send.return_value = {"status": "skipped", "message": "not configured"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pred = root / "pred"
            pred.mkdir()
            adapter = _ConcreteAdapter(root, pred)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                adapter.send_notification("Hello")
            self.assertIn("未配置", buf.getvalue())


class TestInheritance(unittest.TestCase):
    """alpha_v1 and alpha_v2 inherit BaseStrategyAdapter methods."""

    def test_alpha_v1_has_base_methods(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        adapter = AlphaV1StrategyAdapter()
        self.assertTrue(hasattr(adapter, "get_stock_name"))
        self.assertTrue(hasattr(adapter, "send_notification"))
        self.assertTrue(hasattr(adapter, "print_predictions_summary"))
        self.assertTrue(hasattr(adapter, "load_plan_instruments"))
        self.assertTrue(hasattr(adapter, "save_predictions"))
        self.assertTrue(hasattr(adapter, "fetch_open_prices"))

    def test_alpha_v2_has_base_methods(self):
        from qsys.strategy.alpha_v2.adapter import AlphaV2StrategyAdapter

        adapter = AlphaV2StrategyAdapter()
        self.assertTrue(hasattr(adapter, "get_stock_name"))
        self.assertTrue(hasattr(adapter, "send_notification"))
        self.assertTrue(hasattr(adapter, "print_predictions_summary"))
        self.assertTrue(hasattr(adapter, "load_plan_instruments"))
        self.assertTrue(hasattr(adapter, "save_predictions"))
        self.assertTrue(hasattr(adapter, "fetch_open_prices"))

    def test_alpha_v1_get_stock_name_works(self):
        from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter

        adapter = AlphaV1StrategyAdapter()
        # Should not crash and return some value
        result = adapter.get_stock_name("000001.SZ")
        self.assertIsInstance(result, str)

    def test_alpha_v2_get_stock_name_works(self):
        from qsys.strategy.alpha_v2.adapter import AlphaV2StrategyAdapter

        adapter = AlphaV2StrategyAdapter()
        result = adapter.get_stock_name("000001.SZ")
        self.assertIsInstance(result, str)


if __name__ == "__main__":
    unittest.main()
