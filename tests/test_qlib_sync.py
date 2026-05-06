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
    _build_validation_probe_dates,
    _validate_symbol_probe_dates,
    refresh_selected_symbols_from_raw,
    run_targeted_qlib_sync,
)


class _FakeAdapter:
    def __init__(self):
        self._last = pd.Timestamp("2026-04-17")

    def get_last_qlib_date(self):
        return self._last


class TestQlibSync(unittest.TestCase):
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

    def _write_selected_refresh_fixture(self, *, universe: str = "csi800", instrument_end_date: str = "2026-04-16") -> None:
        qlib_dir = cfg.get_path("qlib_bin")
        raw_dir = cfg.get_path("raw_daily")
        feature_dir_name = code_to_fname("000001.sz").lower()
        (qlib_dir / "calendars").mkdir(parents=True, exist_ok=True)
        (qlib_dir / "instruments").mkdir(parents=True, exist_ok=True)
        (qlib_dir / "features" / feature_dir_name).mkdir(parents=True, exist_ok=True)
        (qlib_dir / "calendars" / "day.txt").write_text("2026-04-16\n2026-04-17\n", encoding="utf-8")
        (qlib_dir / "instruments" / "all.txt").write_text(
            f"000001.SZ\t2025-01-02\t{instrument_end_date}\n",
            encoding="utf-8",
        )
        if universe != "all":
            (qlib_dir / "instruments" / f"{universe}.txt").write_text(
                f"000001.SZ\t2025-01-02\t{instrument_end_date}\n",
                encoding="utf-8",
            )
        (qlib_dir / "features" / feature_dir_name / "close.day.bin").write_text("before", encoding="utf-8")
        pd.DataFrame(
            {
                "trade_date": ["20250102", "20260105", "20260417"],
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

    def _stub_dump_fix(self, *, csv_dir: Path, temp_qlib_dir: Path) -> None:
        feature_dir = temp_qlib_dir / "features" / code_to_fname("000001.sz").lower()
        feature_dir.mkdir(parents=True, exist_ok=True)
        (feature_dir / "close.day.bin").write_text("after", encoding="utf-8")

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

    def test_selected_symbol_refresh_result_is_propagated(self):
        adapter = _FakeAdapter()
        refresh_result = {
            "summary": {
                "previous_qlib_last_date": "2026-04-17",
                "post_sync_qlib_last_date": "2026-04-17",
                "affected_symbol_count": 2,
                "symbols_attempted": 2,
                "symbols_synced": 2,
                "symbols_failed": 0,
                "symbols_validated": 2,
                "history_validation_start_date": "2025-01-02",
                "backup_status": "success",
                "rollback_status": "not_needed",
                "qlib_update_status": "success",
                "convert_mode": "selected_symbol_refresh",
                "reason": "ok",
            },
            "rows": [
                {
                    "symbol": "000001.SZ",
                    "original_feature_path": "/tmp/features/000001_sz",
                    "raw_history_start": "2025-01-02",
                    "raw_last_date": "2026-04-17",
                    "raw_row_count": 300,
                    "qlib_history_start_before": "2026-04-17",
                    "qlib_last_date_before": "2026-04-17",
                    "qlib_row_count_before": 1,
                    "qlib_history_start_after": "2025-01-02",
                    "qlib_last_date_after": "2026-04-17",
                    "qlib_row_count_after": 300,
                    "sync_status": "success",
                    "validated_on_target_date": True,
                    "backup_path": "/tmp/backup/000001_sz",
                    "backup_status": "success",
                    "error": "",
                },
                {
                    "symbol": "000157.SZ",
                    "original_feature_path": "/tmp/features/000157_sz",
                    "raw_history_start": "2025-01-02",
                    "raw_last_date": "2026-04-17",
                    "raw_row_count": 300,
                    "qlib_history_start_before": "2026-04-17",
                    "qlib_last_date_before": "2026-04-17",
                    "qlib_row_count_before": 1,
                    "qlib_history_start_after": "2025-01-02",
                    "qlib_last_date_after": "2026-04-17",
                    "qlib_row_count_after": 300,
                    "sync_status": "success",
                    "validated_on_target_date": True,
                    "backup_path": "/tmp/backup/000157_sz",
                    "backup_status": "success",
                    "error": "",
                },
            ],
        }
        with patch("qsys.ops.qlib_sync.refresh_selected_symbols_from_raw", return_value=refresh_result):
            summary, _, _, symbol_sync_path = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ", "000157.SZ"],
                apply=True,
                output_dir=self.root,
                base_dir=self.root,
                target_date="2026-04-17",
            )
        sync_rows = list(csv.DictReader(symbol_sync_path.open("r", encoding="utf-8")))
        self.assertEqual(summary["qlib_update_status"], "success")
        self.assertEqual(summary["convert_mode"], "selected_symbol_refresh")
        self.assertEqual(len(sync_rows), 2)
        self.assertEqual(sorted(sync_rows[0].keys()), sorted(QLIB_SYMBOL_SYNC_COLUMNS))
        self.assertEqual(sync_rows[0]["qlib_row_count_after"], "300")

    def test_run_targeted_qlib_sync_passes_universe_to_selected_refresh(self):
        adapter = _FakeAdapter()
        refresh_result = {
            "summary": {
                "previous_qlib_last_date": "2026-04-17",
                "post_sync_qlib_last_date": "2026-04-17",
                "affected_symbol_count": 1,
                "symbols_attempted": 1,
                "symbols_synced": 0,
                "symbols_failed": 0,
                "symbols_validated": 0,
                "backup_status": "skipped",
                "rollback_status": "not_needed",
                "qlib_update_status": "skipped_requires_manual_rebuild",
                "convert_mode": "selected_symbol_refresh",
                "reason": "test stub",
            },
            "rows": [],
        }
        with patch("qsys.ops.qlib_sync.refresh_selected_symbols_from_raw", return_value=refresh_result) as mock_refresh:
            run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=True,
                output_dir=self.root,
                base_dir=self.root,
                target_date="2026-04-17",
                universe="csi800",
            )
        self.assertEqual(mock_refresh.call_args.kwargs["universe"], "csi800")

    def test_build_validation_probe_dates_spans_history(self):
        dates = ["2025-01-02", "2025-01-03", "2025-02-10", "2025-08-20", "2026-04-17"]
        probes = _build_validation_probe_dates(dates, "2026-04-17")
        self.assertEqual(probes[0], "2025-01-02")
        self.assertEqual(probes[-1], "2026-04-17")
        self.assertGreaterEqual(len(probes), 3)

    def test_validate_symbol_probe_dates_requires_all_core_fields(self):
        class _ProbeAdapter:
            def get_features(self, symbols, fields, start_time=None, end_time=None):
                idx = pd.MultiIndex.from_tuples(
                    [
                        ("000001.SZ", pd.Timestamp("2025-01-02")),
                        ("000001.SZ", pd.Timestamp("2026-04-17")),
                    ],
                    names=["instrument", "datetime"],
                )
                return pd.DataFrame(
                    {
                        "$open": [10.0, 12.0],
                        "$high": [10.5, 12.5],
                        "$low": [9.5, 11.5],
                        "$close": [10.2, 12.2],
                        "$volume": [100.0, 300.0],
                        "$amount": [None, 3000.0],
                    },
                    index=idx,
                )

        failed = _validate_symbol_probe_dates(_ProbeAdapter(), "000001.SZ", ["2025-01-02", "2026-04-17"])
        self.assertEqual(failed, ["2025-01-02"])

    def test_refresh_selected_symbol_requires_universe_registry(self):
        self._write_selected_refresh_fixture(universe="all")
        with patch("qsys.ops.qlib_sync._collect_qlib_history_stats", return_value={"000001.SZ": {"history_start": "2025-01-02", "history_end": "2026-04-17", "row_count": 3}}), patch.object(
            QlibAdapter, "init_qlib", return_value=None
        ), patch.object(QlibAdapter, "get_last_qlib_date", return_value=pd.Timestamp("2026-04-17")), patch(
            "qsys.data.adapter.StockDataStore.get_stock_list", return_value=pd.DataFrame(columns=["ts_code", "industry"])
        ), patch.object(QlibAdapter, "_load_industry_map", return_value={}):
            result = refresh_selected_symbols_from_raw(
                self.root,
                ["000001.SZ"],
                universe="csi800",
                target_date="2026-04-17",
                apply=True,
                output_dir=self.root / "out_missing_registry",
            )
        self.assertEqual(result["summary"]["qlib_update_status"], "skipped_requires_manual_rebuild")
        self.assertIn("missing_instrument_registry:csi800.txt", result["summary"]["reason"])

    def test_refresh_selected_symbol_updates_csi800_registry_when_present(self):
        self._write_selected_refresh_fixture(universe="csi800")
        stats = {"000001.SZ": {"history_start": "2025-01-02", "history_end": "2026-04-17", "row_count": 3}}
        with patch("qsys.ops.qlib_sync._collect_qlib_history_stats", side_effect=[stats, stats]), patch(
            "qsys.ops.qlib_sync._run_dump_fix", side_effect=self._stub_dump_fix
        ), patch("qsys.ops.qlib_sync._symbol_has_target_feature", return_value=True), patch(
            "qsys.ops.qlib_sync._validate_symbol_probe_dates", return_value=[]
        ), patch.object(QlibAdapter, "init_qlib", return_value=None), patch.object(
            QlibAdapter, "get_last_qlib_date", return_value=pd.Timestamp("2026-04-17")
        ), patch.object(QlibAdapter, "touch_qlib_mtime", return_value=None), patch(
            "qsys.data.adapter.StockDataStore.get_stock_list", return_value=pd.DataFrame(columns=["ts_code", "industry"])
        ), patch.object(QlibAdapter, "_load_industry_map", return_value={}):
            result = refresh_selected_symbols_from_raw(
                self.root,
                ["000001.SZ"],
                universe="csi800",
                target_date="2026-04-17",
                apply=True,
                output_dir=self.root / "out_csi800_apply",
            )
        csi800_text = (cfg.get_path("qlib_bin") / "instruments" / "csi800.txt").read_text(encoding="utf-8")
        self.assertIn("2026-04-17", csi800_text)
        self.assertTrue((self.root / "out_csi800_apply" / "backups" / "csi800.txt").exists())
        self.assertEqual(result["summary"]["rollback_status"], "not_needed")
        self.assertEqual(result["summary"]["qlib_update_status"], "success")

    def test_refresh_selected_symbol_rolls_back_csi800_registry_on_validation_failure(self):
        self._write_selected_refresh_fixture(universe="csi800", instrument_end_date="2026-04-16")
        stats = {"000001.SZ": {"history_start": "2025-01-02", "history_end": "2026-04-17", "row_count": 3}}
        with patch("qsys.ops.qlib_sync._collect_qlib_history_stats", side_effect=[stats, stats, stats]), patch(
            "qsys.ops.qlib_sync._run_dump_fix", side_effect=self._stub_dump_fix
        ), patch("qsys.ops.qlib_sync._symbol_has_target_feature", return_value=False), patch(
            "qsys.ops.qlib_sync._validate_symbol_probe_dates", return_value=[]
        ), patch.object(QlibAdapter, "init_qlib", return_value=None), patch.object(
            QlibAdapter, "get_last_qlib_date", return_value=pd.Timestamp("2026-04-17")
        ), patch.object(QlibAdapter, "touch_qlib_mtime", return_value=None), patch(
            "qsys.data.adapter.StockDataStore.get_stock_list", return_value=pd.DataFrame(columns=["ts_code", "industry"])
        ), patch.object(QlibAdapter, "_load_industry_map", return_value={}):
            result = refresh_selected_symbols_from_raw(
                self.root,
                ["000001.SZ"],
                universe="csi800",
                target_date="2026-04-17",
                apply=True,
                output_dir=self.root / "out_csi800_rollback",
            )
        csi800_text = (cfg.get_path("qlib_bin") / "instruments" / "csi800.txt").read_text(encoding="utf-8")
        self.assertIn("2026-04-16", csi800_text)
        self.assertEqual(result["summary"]["qlib_update_status"], "failed")
        self.assertEqual(result["summary"]["rollback_status"], "success")

    def test_selected_symbol_prepare_csvs_preserves_history_up_to_target_date(self):
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


if __name__ == "__main__":
    unittest.main()
