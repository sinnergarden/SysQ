import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.qlib_sync import run_targeted_qlib_sync


class _FakeAdapter:
    def __init__(self):
        self._last = pd.Timestamp("2026-04-17")

    def get_last_qlib_date(self):
        return self._last


class TestQlibSync(unittest.TestCase):
    def test_dry_run_skips(self):
        adapter = _FakeAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            summary, affected_path, _, symbol_sync_path = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=False,
                output_dir=Path(tmpdir),
                base_dir=tmpdir,
                target_date="2026-04-17",
            )
            rows = list(csv.DictReader(affected_path.open("r", encoding="utf-8")))
            sync_rows = list(csv.DictReader(symbol_sync_path.open("r", encoding="utf-8")))
        self.assertEqual(summary["qlib_update_status"], "skipped")
        self.assertEqual(len(rows), 1)
        self.assertEqual(sync_rows, [])

    def test_skip_sync_flag(self):
        adapter = _FakeAdapter()
        with tempfile.TemporaryDirectory() as tmpdir:
            summary, _, _, symbol_sync_path = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ"],
                apply=True,
                output_dir=Path(tmpdir),
                skip_sync=True,
                base_dir=tmpdir,
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
                    "raw_last_date": "2026-04-17",
                    "qlib_last_date_before": "2026-04-03",
                    "qlib_last_date_after": "2026-04-17",
                    "sync_status": "success",
                    "validated_on_target_date": True,
                    "backup_path": "/tmp/backup/000001_sz",
                    "backup_status": "success",
                    "error": "",
                },
                {
                    "symbol": "000157.SZ",
                    "original_feature_path": "/tmp/features/000157_sz",
                    "raw_last_date": "2026-04-17",
                    "qlib_last_date_before": "2026-04-03",
                    "qlib_last_date_after": "2026-04-17",
                    "sync_status": "success",
                    "validated_on_target_date": True,
                    "backup_path": "/tmp/backup/000157_sz",
                    "backup_status": "success",
                    "error": "",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "qsys.ops.qlib_sync.refresh_selected_symbols_from_raw", return_value=refresh_result
        ):
            summary, _, _, symbol_sync_path = run_targeted_qlib_sync(
                adapter=adapter,
                previous_qlib_last_date="2026-04-17",
                affected_symbols=["000001.SZ", "000157.SZ"],
                apply=True,
                output_dir=Path(tmpdir),
                base_dir=tmpdir,
                target_date="2026-04-17",
            )
            sync_rows = list(csv.DictReader(symbol_sync_path.open("r", encoding="utf-8")))
        self.assertEqual(summary["qlib_update_status"], "success")
        self.assertEqual(summary["convert_mode"], "selected_symbol_refresh")
        self.assertEqual(len(sync_rows), 2)
        self.assertIn("original_feature_path", sync_rows[0])
        self.assertIn("backup_status", sync_rows[0])


if __name__ == "__main__":
    unittest.main()
