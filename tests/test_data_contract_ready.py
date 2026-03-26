import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.config import cfg
from qsys.data.adapter import QlibAdapter


class TestDataContractReady(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.original_dirs = cfg.dirs.copy()
        cfg.dirs = {
            "root": self.root,
            "raw": self.root / "raw",
            "raw_daily": self.root / "raw" / "daily",
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

    def test_prepare_csvs_derives_vwap_and_limit_fields(self):
        df = pd.DataFrame(
            {
                "trade_date": ["2026-03-20"],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [float("nan")],
                "close_x": [10.5],
                "vol": [2.0],
                "amount": [3.0],  # thousand RMB from tushare
                "adj_factor": [1.0],
                "up_limit": [11.55],
                "down_limit": [9.45],
                "paused": [0],
            }
        )
        df.to_feather(cfg.get_path("raw_daily") / "000001.SZ.feather")

        adapter = QlibAdapter()
        csv_dir, count = adapter._prepare_csvs()
        self.assertEqual(count, 1)

        out = pd.read_csv(csv_dir / "000001.SZ.csv")
        self.assertIn("vwap", out.columns)
        self.assertIn("high_limit", out.columns)
        self.assertIn("low_limit", out.columns)
        self.assertIn("paused", out.columns)
        self.assertAlmostEqual(out.loc[0, "volume"], 200.0)
        self.assertAlmostEqual(out.loc[0, "amount"], 3000.0)
        self.assertAlmostEqual(out.loc[0, "close"], 10.5)
        self.assertAlmostEqual(out.loc[0, "vwap"], 15.0)
        self.assertAlmostEqual(out.loc[0, "high_limit"], 11.55)
        self.assertAlmostEqual(out.loc[0, "low_limit"], 9.45)

    def test_incremental_no_new_rows_rebuilds_when_aligned_core_fields_unusable(self):
        adapter = QlibAdapter()

        with patch.object(adapter, "_prepare_csvs", return_value=(self.root / "tmp_csv", 0)), \
             patch.object(adapter, "_should_rebuild_corrupted_aligned_data", return_value=True), \
             patch.object(adapter, "convert_all") as mock_convert_all, \
             patch("qsys.data.adapter.os.utime") as mock_utime:
            adapter.convert_incremental(pd.Timestamp("2026-03-25"))

        mock_convert_all.assert_called_once()
        mock_utime.assert_not_called()

    def test_run_dump_script_refreshes_csi300_instruments(self):
        adapter = QlibAdapter()
        csv_dir = self.root / "csvs"
        csv_dir.mkdir(parents=True, exist_ok=True)

        with patch("qsys.data.adapter.subprocess.run") as mock_run, \
             patch.object(adapter, "_clean_artifacts"):
            adapter._run_dump_script(csv_dir, mode="dump_all")

        self.assertGreaterEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
