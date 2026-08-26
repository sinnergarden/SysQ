import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from qlib.utils import code_to_fname

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter
from qsys.ops.qlib_sync import (
    QLIB_SYMBOL_SYNC_COLUMNS,
    refresh_selected_symbols_from_raw,
    run_targeted_qlib_sync,
)


class _FakeAdapter:
    def __init__(self):
        self._last = pd.Timestamp("2026-04-17")

    def get_last_qlib_date(self):
        return self._last

    def convert_fix_symbols(self, symbols):
        return {"status": "success", "post_sync_qlib_last_date": self._last.strftime("%Y-%m-%d")}

    def convert_incremental(self, target_date):
        pass  # exists so can_run_incremental_qlib_sync returns True when appropriate


class TestQlibSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_dirs = cfg.dirs.copy()
        cfg.dirs = {
            "root": self.root,
            "raw": self.root / "raw",
            "raw_daily": self.root / "raw" / "daily",
            "canonical_dir": self.root / "canonical" / "daily",
            "meta": self.root / "meta",
            "db": self.root,
            "qlib_bin": self.root / "qlib_bin",
            "feature": self.root / "feature",
            "clean": self.root / "clean",
        }
        for path in cfg.dirs.values():
            path.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        cfg.dirs = self.original_dirs
        self.temp_dir.cleanup()

    # ── run_targeted_qlib_sync: no-op paths ──────────────────────────────

    def test_dry_run_skips(self):
        adapter = _FakeAdapter()
        summary, affected_path, _, symbol_sync_path = run_targeted_qlib_sync(
            adapter=adapter,
            previous_qlib_last_date="2026-04-17",
            affected_symbols=["000001.SZ"],
            apply=False,
            output_dir=self.root,
            base_dir=self.root,
            target_date="2026-04-17",
        )
        rows = list(csv.DictReader(affected_path.open("r", encoding="utf-8")))
        sync_rows = list(csv.DictReader(symbol_sync_path.open("r", encoding="utf-8")))
        self.assertEqual(summary["qlib_update_status"], "skipped")
        self.assertEqual(len(rows), 1)
        self.assertEqual(sync_rows, [])

    def test_skip_sync_flag(self):
        adapter = _FakeAdapter()
        summary, _, _, symbol_sync_path = run_targeted_qlib_sync(
            adapter=adapter,
            previous_qlib_last_date="2026-04-17",
            affected_symbols=["000001.SZ"],
            apply=True,
            output_dir=self.root,
            skip_sync=True,
            base_dir=self.root,
            target_date="2026-04-17",
        )
        sync_rows = list(csv.DictReader(symbol_sync_path.open("r", encoding="utf-8")))
        self.assertEqual(summary["qlib_update_status"], "skipped")
        self.assertEqual(summary["symbols_attempted"], 0)
        self.assertEqual(sync_rows, [])

    # ── run_targeted_qlib_sync: fallback to convert_fix_symbols ──────────

    def test_fallback_to_fix_symbols_when_incremental_unavailable(self):
        """When target_date <= last_qlib_date, falls through to fix_symbols."""
        adapter = _FakeAdapter()
        with patch.object(adapter, "convert_fix_symbols", return_value={"status": "success"}) as mock_fix, \
             patch.object(adapter, "get_last_qlib_date", return_value=pd.Timestamp("2026-04-17")):
            summary, _, _, symbol_sync_path = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=True,
                output_dir=self.root,
                base_dir=self.root,
                target_date="2026-04-16",
            )
        mock_fix.assert_called_once_with(["000001.SZ"])
        self.assertEqual(summary["qlib_update_status"], "success")
        self.assertEqual(summary["convert_mode"], "fix_symbols")

    def test_fallback_to_fix_symbols_when_incremental_fails(self):
        """When incremental raises, falls through to fix_symbols."""
        adapter = _FakeAdapter()
        with patch.object(adapter, "convert_incremental", side_effect=RuntimeError("mock fail")), \
             patch.object(adapter, "convert_fix_symbols", return_value={"status": "success"}) as mock_fix, \
             patch.object(adapter, "get_last_qlib_date", return_value=pd.Timestamp("2026-04-17")):
            summary, _, _, _ = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=True,
                output_dir=self.root,
                base_dir=self.root,
                target_date="2026-04-20",
            )
        mock_fix.assert_called_once_with(["000001.SZ"])
        self.assertEqual(summary["qlib_update_status"], "success")

    def test_fix_symbols_failure_propagated(self):
        """When convert_fix_symbols fails, summary shows failed status."""
        adapter = _FakeAdapter()
        with patch.object(adapter, "convert_fix_symbols", side_effect=RuntimeError("disk full")):
            summary, _, _, symbol_sync_path = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=True,
                output_dir=self.root,
                base_dir=self.root,
                target_date="2026-04-16",
            )
        self.assertEqual(summary["qlib_update_status"], "failed")
        self.assertIn("disk full", summary["reason"])
        sync_rows = list(csv.DictReader(symbol_sync_path.open("r", encoding="utf-8")))
        self.assertEqual(len(sync_rows), 1)
        self.assertEqual(sync_rows[0]["sync_status"], "failed")

    def test_return_value_shape(self):
        """run_targeted_qlib_sync still returns 4 values with correct types."""
        adapter = _FakeAdapter()
        with patch.object(adapter, "convert_fix_symbols", return_value={"status": "success"}), \
             patch.object(adapter, "get_last_qlib_date", return_value=pd.Timestamp("2026-04-17")):
            result = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=True,
                output_dir=self.root,
                base_dir=self.root,
                target_date="2026-04-16",
            )
        summary, affected_path, summary_path, symbol_sync_path = result
        self.assertIsInstance(summary, dict)
        self.assertIn("qlib_update_status", summary)
        self.assertIn("post_sync_qlib_last_date", summary)
        self.assertTrue(affected_path.exists())
        self.assertTrue(summary_path.exists())
        self.assertTrue(symbol_sync_path.exists())

    # ── refresh_selected_symbols_from_raw wrapper ────────────────────────

    def test_refresh_selected_symbol_dry_run(self):
        """Wrapper dry-run returns skipped + row stubs."""
        result = refresh_selected_symbols_from_raw(
            self.root,
            ["000001.SZ"],
            universe="csi800",
            target_date="2026-04-17",
            apply=False,
            output_dir=self.root / "out",
        )
        self.assertEqual(result["summary"]["qlib_update_status"], "skipped")
        self.assertEqual(len(result["rows"]), 1)

    def test_refresh_selected_symbol_apply_delegates(self):
        """Wrapper delegates to convert_fix_symbols on apply."""
        raw_dir = cfg.get_path("canonical_dir")
        pd.DataFrame({"trade_date": ["20260417"]}).to_feather(raw_dir / "000001.SZ.feather")

        with patch.object(QlibAdapter, "init_qlib", return_value=None), \
             patch.object(QlibAdapter, "_prepare_csvs", return_value=(raw_dir, 1)), \
             patch.object(QlibAdapter, "_run_dump_script"):
            result = refresh_selected_symbols_from_raw(
                self.root,
                ["000001.SZ"],
                universe="csi800",
                target_date="2026-04-17",
                apply=True,
                output_dir=self.root / "out_apply",
            )
        self.assertEqual(result["summary"]["qlib_update_status"], "success")
        self.assertEqual(len(result["rows"]), 1)

    def test_refresh_selected_symbol_apply_failure(self):
        """Wrapper propagates failure from convert_fix_symbols."""
        with patch.object(QlibAdapter, "init_qlib", return_value=None), \
             patch.object(QlibAdapter, "convert_fix_symbols", side_effect=ValueError("oops")):
            result = refresh_selected_symbols_from_raw(
                self.root,
                ["000001.SZ"],
                universe="csi800",
                target_date="2026-04-17",
                apply=True,
                output_dir=self.root / "out_fail",
            )
        self.assertEqual(result["summary"]["qlib_update_status"], "failed")
        self.assertIn("oops", result["summary"]["reason"])

    def test_refresh_selected_symbol_empty_symbols_apply_returns_skipped(self):
        """Empty symbols + apply=True must map skipped status correctly."""
        with patch.object(QlibAdapter, "init_qlib", return_value=None):
            result = refresh_selected_symbols_from_raw(
                self.root,
                [],
                universe="csi800",
                target_date="2026-04-17",
                apply=True,
                output_dir=self.root / "out_empty",
            )
        self.assertEqual(result["summary"]["qlib_update_status"], "skipped")
        self.assertEqual(result["summary"]["symbols_attempted"], 0)
        self.assertEqual(result["summary"]["symbols_failed"], 0)

    # ── QlibAdapter._prepare_csvs behavior (unchanged) ───────────────────

    def test_prepare_csvs_preserves_history_up_to_target_date(self):
        raw_dir = cfg.get_path("raw_daily")
        pd.DataFrame(
            {
                "trade_date": ["20250102", "20260417", "20260420"],
                "open": [10.0, 11.0, 12.0],
                "high": [10.5, 11.5, 12.5],
                "low": [9.5, 10.5, 11.5],
                "close": [10.2, 11.2, 12.2],
                "vol": [1.0, 2.0, 3.0],
                "amount": [1.0, 2.0, 3.0],
                "adj_factor": [1.0, 1.0, 1.0],
                "paused": [0, 0, 0],
            }
        ).to_feather(raw_dir / "000001.SZ.feather")

        adapter = QlibAdapter()
        with patch("qsys.data.adapter.StockDataStore.get_stock_list", return_value=pd.DataFrame(columns=["ts_code", "industry"])), patch.object(
            adapter, "_load_industry_map", return_value={}
        ):
            csv_dir, count = adapter._prepare_csvs(
                until_date=pd.Timestamp("2026-04-17"),
                selected_symbols=["000001.SZ"],
                output_dir=self.root / "csv_out",
            )

        self.assertEqual(count, 1)
        out = pd.read_csv(csv_dir / "000001.SZ.csv")
        self.assertEqual(out["date"].tolist(), ["2025-01-02", "2026-04-17"])
        self.assertEqual(len(out), 2)

    def test_prepare_csvs_without_until_date_keeps_later_rows_for_incremental_path(self):
        raw_dir = cfg.get_path("raw_daily")
        pd.DataFrame(
            {
                "trade_date": ["20260417", "20260420"],
                "open": [11.0, 12.0],
                "high": [11.5, 12.5],
                "low": [10.5, 11.5],
                "close": [11.2, 12.2],
                "vol": [2.0, 3.0],
                "amount": [2.0, 3.0],
                "adj_factor": [1.0, 1.0],
                "paused": [0, 0],
            }
        ).to_feather(raw_dir / "000001.SZ.feather")

        adapter = QlibAdapter()
        with patch("qsys.data.adapter.StockDataStore.get_stock_list", return_value=pd.DataFrame(columns=["ts_code", "industry"])), patch.object(
            adapter, "_load_industry_map", return_value={}
        ):
            csv_dir, count = adapter._prepare_csvs(
                since_date=pd.Timestamp("2026-04-17"),
                selected_symbols=["000001.SZ"],
                output_dir=self.root / "csv_out_incremental",
            )

        self.assertEqual(count, 1)
        out = pd.read_csv(csv_dir / "000001.SZ.csv")
        self.assertEqual(out["date"].tolist(), ["2026-04-17", "2026-04-20"])
        self.assertEqual(len(out), 2)

    def test_prepare_csvs_uses_per_date_pit_industry_and_certified_mode_blocks_fallback(self):
        canonical = cfg.get_path("canonical_dir")
        frame = pd.DataFrame({
            "trade_date": ["20180313", "20260417"],
            "open": [10.0, 11.0], "high": [10.5, 11.5],
            "low": [9.5, 10.5], "close": [10.2, 11.2],
            "vol": [1.0, 2.0], "amount": [1.0, 2.0],
            "adj_factor": [1.0, 1.0], "paused": [0, 0],
            "industry": ["OldSector", "NewSector"],
        })
        path = canonical / "000001.SZ.feather"
        frame.to_feather(path)
        adapter = QlibAdapter(raw_dir=canonical)
        stock = pd.DataFrame({"ts_code": ["000001.SZ"], "industry": ["NewSector"]})
        with patch("qsys.data.adapter.StockDataStore.get_stock_list", return_value=stock), patch.object(
            adapter, "_load_industry_map", return_value={"OldSector": 1, "NewSector": 2}
        ):
            csv_dir, count = adapter._prepare_csvs(
                selected_symbols=["000001.SZ"], output_dir=self.root / "csv_pit_industry",
                require_pit_industry=True,
            )
        self.assertEqual(count, 1)
        self.assertEqual(pd.read_csv(csv_dir / "000001.SZ.csv")["industry"].tolist(), [1, 2])

        frame.loc[0, "industry"] = pd.NA
        frame.to_feather(path)
        with patch("qsys.data.adapter.StockDataStore.get_stock_list", return_value=stock), patch.object(
            adapter, "_load_industry_map", return_value={"NewSector": 2}
        ), self.assertRaisesRegex(RuntimeError, "PIT industry coverage missing"):
            adapter._prepare_csvs(
                selected_symbols=["000001.SZ"], output_dir=self.root / "csv_pit_missing",
                require_pit_industry=True,
            )

        frame.drop(columns=["trade_date"]).assign(industry="NewSector").to_feather(path)
        with patch("qsys.data.adapter.StockDataStore.get_stock_list", return_value=stock), patch.object(
            adapter, "_load_industry_map", return_value={"NewSector": 2}
        ), self.assertRaisesRegex(RuntimeError, "PIT industry date identity missing"):
            adapter._prepare_csvs(
                until_date=pd.Timestamp("2026-04-17"),
                selected_symbols=["000001.SZ"], output_dir=self.root / "csv_pit_bad_date",
                require_pit_industry=True,
            )


if __name__ == "__main__":
    unittest.main()
