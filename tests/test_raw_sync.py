import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.raw_sync import build_raw_update_plan, load_success_symbols_from_plan, run_targeted_raw_update


class _FakeStore:
    def __init__(self, frames=None):
        self.frames = frames or {}

    def load_daily(self, code: str):
        return self.frames.get(code)


class _FakeCollector:
    def __init__(self, fail_symbols=None):
        self.fail_symbols = fail_symbols or set()
        self.calls = []

    def update_universe_history(self, universe=None, start_date=None, end_date=None, **kwargs):
        symbols = list(universe) if isinstance(universe, list) else [universe]
        self.calls.append((symbols, start_date, end_date))
        if symbols[0] in self.fail_symbols:
            raise RuntimeError(f"boom {symbols[0]}")


class TestRawSync(unittest.TestCase):
    def test_build_raw_update_plan_marks_selection_and_resume(self):
        store = _FakeStore({"000001.SZ": pd.DataFrame({"trade_date": ["2026-04-17"]})})
        rows = build_raw_update_plan(
            store=store,
            symbols=["000001.SZ", "000002.SZ", "000003.SZ"],
            selected_symbols={"000001.SZ", "000003.SZ"},
            resume_success_symbols={"000003.SZ"},
            target_date="2026-04-25",
            lookback_days=20,
        )
        self.assertEqual(sum(1 for row in rows if row["selected_for_apply"]), 1)
        self.assertEqual(rows[0]["status"], "planned")
        self.assertEqual(rows[1]["status"], "skipped")
        self.assertEqual(rows[2]["error"], "resume_skip_previous_success")

    def test_load_success_symbols_from_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plan_path = Path(tmpdir) / "raw_update_plan.csv"
            with plan_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["symbol", "status"])
                writer.writeheader()
                writer.writerow({"symbol": "000001.SZ", "status": "success"})
                writer.writerow({"symbol": "000002.SZ", "status": "failed"})
            self.assertEqual(load_success_symbols_from_plan(plan_path), {"000001.SZ"})

    def test_dry_run_does_not_call_collector(self):
        store = _FakeStore()
        collector = _FakeCollector()
        with tempfile.TemporaryDirectory() as tmpdir, patch("qsys.ops.raw_sync.StockDataStore", return_value=store), patch(
            "qsys.ops.raw_sync.TushareCollector", return_value=collector
        ):
            summary, _, _, affected = run_targeted_raw_update(
                symbols=["000001.SZ", "000002.SZ"],
                selected_symbols={"000001.SZ"},
                target_date="2026-04-25",
                lookback_days=20,
                apply=False,
                output_dir=Path(tmpdir),
            )
        self.assertEqual(summary["status"], "skipped")
        self.assertFalse(collector.calls)
        self.assertEqual(affected, [])

    def test_apply_tracks_partial_failures(self):
        frames = {
            "000001.SZ": pd.DataFrame({"trade_date": ["2026-04-17"]}),
            "000002.SZ": pd.DataFrame({"trade_date": ["2026-04-17"]}),
        }
        store = _FakeStore(frames)
        collector = _FakeCollector(fail_symbols={"000002.SZ"})
        with tempfile.TemporaryDirectory() as tmpdir, patch("qsys.ops.raw_sync.StockDataStore", return_value=store), patch(
            "qsys.ops.raw_sync.TushareCollector", return_value=collector
        ):
            summary, plan_path, _, _ = run_targeted_raw_update(
                symbols=["000001.SZ", "000002.SZ"],
                selected_symbols={"000001.SZ", "000002.SZ"},
                target_date="2026-04-25",
                lookback_days=20,
                apply=True,
                output_dir=Path(tmpdir),
            )
            rows = list(csv.DictReader(plan_path.open("r", encoding="utf-8")))
        self.assertEqual(summary["status"], "partial")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row["attempt_started_at"] for row in rows))
        self.assertTrue(any(row["status"] == "failed" for row in rows))


if __name__ == "__main__":
    unittest.main()
