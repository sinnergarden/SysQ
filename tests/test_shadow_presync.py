import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.shadow_presync import run_shadow_presync
from qsys.ops.state import load_json


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class _FakeAdapter:
    def __init__(self, qlib_dir: Path):
        self.qlib_dir = qlib_dir
        self._last_date = pd.Timestamp("2026-04-17")
        self.convert_incremental_calls = []

    def init_qlib(self):
        return None

    def get_last_qlib_date(self):
        return self._last_date

    def convert_incremental(self, since_date):
        self.convert_incremental_calls.append(since_date)
        self._last_date = pd.Timestamp("2026-04-25")


class _FakeStore:
    def __init__(self, frames: dict[str, pd.DataFrame] | None = None):
        self.frames = frames or {}

    def load_daily(self, code: str):
        return self.frames.get(code)


class _FakeCollector:
    def __init__(self, fail_symbols: set[str] | None = None):
        self.fail_symbols = fail_symbols or set()
        self.calls: list[tuple[list[str], str, str]] = []

    def update_universe_history(self, universe="csi300", start_date=None, end_date=None, **kwargs):
        symbols = list(universe) if isinstance(universe, list) else [universe]
        self.calls.append((symbols, start_date, end_date))
        symbol = symbols[0]
        if symbol in self.fail_symbols:
            raise RuntimeError(f"fetch failed for {symbol}")


class TestShadowPresync(unittest.TestCase):
    def _prepare_registry(self, base_dir: Path, active_symbols: int = 300) -> Path:
        qlib_dir = base_dir / "data" / "qlib_bin"
        inst_dir = qlib_dir / "instruments"
        inst_dir.mkdir(parents=True, exist_ok=True)
        lines = []
        for i in range(300):
            symbol = f"{i:06d}.SZ"
            end_date = "2026-04-25" if i < active_symbols else "2026-04-03"
            lines.append(f"{symbol}\t2020-01-01\t{end_date}\n")
        (inst_dir / "csi300.txt").write_text("".join(lines), encoding="utf-8")
        return qlib_dir

    def _run(
        self,
        *,
        active_symbols: int = 300,
        apply: bool = False,
        collector=None,
        store=None,
        can_sync=False,
        **kwargs,
    ):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        base_dir = Path(tmpdir.name)
        qlib_dir = self._prepare_registry(base_dir, active_symbols=active_symbols)
        adapter = _FakeAdapter(qlib_dir)
        collector = collector if collector is not None else _FakeCollector()
        store = store if store is not None else _FakeStore()
        date_resolution = {
            "requested_date": "2026-04-25",
            "resolved_trade_date": "2026-04-25",
            "last_qlib_date": "2026-04-17",
            "status": "success",
            "reason": "requested_date is available in qlib",
            "is_exact_match": True,
        }
        coverage_rows = [{"instrument": f"{i:06d}.SZ", "coverage_mismatch_reason": "ok"} for i in range(300)]
        repair_plan = {"stale_but_feature_available_count": 0, "stale_but_feature_available": []}
        refresh_result = {
            "summary": {
                "previous_qlib_last_date": "2026-04-17",
                "post_sync_qlib_last_date": "2026-04-17",
                "affected_symbol_count": 5,
                "symbols_attempted": 5,
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
        with patch("qsys.ops.shadow_presync.resolve_daily_trade_date", return_value=date_resolution), patch(
            "qsys.ops.shadow_presync.QlibAdapter", return_value=adapter
        ), patch("qsys.ops.raw_sync.StockDataStore", return_value=store), patch(
            "qsys.ops.raw_sync.TushareCollector", return_value=collector
        ), patch("qsys.ops.qlib_sync.refresh_selected_symbols_from_raw", return_value=refresh_result), patch(
            "qsys.ops.shadow_presync.build_instrument_coverage_rows", return_value=coverage_rows
        ), patch("qsys.ops.shadow_presync.build_repair_plan", return_value=repair_plan):
            result = run_shadow_presync(
                base_dir,
                run_id="shadow_presync_2026-04-25_090807",
                universe="csi300",
                target_date="2026-04-25",
                lookback_days=20,
                apply=apply,
                triggered_by="test",
                **kwargs,
            )
        return base_dir, result, adapter, collector

    def test_presync_artifact_contract(self):
        base_dir, result, _, _ = self._run(active_symbols=300, apply=False)
        run_dir = Path(result["run_dir"])
        self.assertTrue((run_dir / "01_universe" / "universe_snapshot.csv").exists())
        self.assertTrue((run_dir / "02_raw" / "raw_update_summary.json").exists())
        self.assertTrue((run_dir / "03_qlib" / "qlib_sync_summary.json").exists())
        self.assertTrue((run_dir / "04_instrument" / "instrument_coverage_summary.json").exists())
        self.assertTrue((run_dir / "presync_summary.json").exists())
        self.assertTrue((base_dir / "runs" / "latest_shadow_presync.json").exists())

    def test_staged_symbol_selection(self):
        _, result, _, _ = self._run(active_symbols=300, apply=False, max_symbols=5)
        summary = load_json(Path(result["summary_path"]))
        self.assertEqual(summary["selected_symbol_count"], 5)

    def test_symbols_and_symbols_file_selection(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        symbols_file = Path(tmpdir.name) / "symbols.txt"
        symbols_file.write_text("000003.SZ\n000004.SZ\n", encoding="utf-8")
        _, result, _, _ = self._run(active_symbols=300, apply=False, symbols=["000001.SZ", "000002.SZ"], symbols_file=symbols_file)
        summary = load_json(Path(result["summary_path"]))
        self.assertEqual(summary["selected_symbols"], ["000001.SZ", "000002.SZ", "000003.SZ", "000004.SZ"])

    def test_dry_run_does_not_mutate(self):
        _, result, adapter, collector = self._run(active_symbols=300, apply=False, max_symbols=5)
        summary = load_json(Path(result["summary_path"]))
        self.assertFalse(collector.calls)
        self.assertFalse(adapter.convert_incremental_calls)
        self.assertFalse(summary["apply"])
        self.assertEqual(summary["raw_update_status"], "skipped")

    def test_raw_only_does_not_trigger_qlib_or_repair(self):
        _, result, adapter, collector = self._run(active_symbols=300, apply=True, raw_only=True, max_symbols=5)
        summary = load_json(Path(result["summary_path"]))
        repair_result = load_json(Path(result["run_dir"]) / "04_instrument" / "repair_result.json")
        self.assertTrue(collector.calls)
        self.assertFalse(adapter.convert_incremental_calls)
        self.assertEqual(summary["qlib_update_status"], "skipped")
        self.assertIn("raw-only", repair_result["reason"])

    def test_qlib_only_does_not_trigger_raw_collector(self):
        _, result, adapter, collector = self._run(active_symbols=300, apply=True, qlib_only=True, max_symbols=5)
        summary = load_json(Path(result["summary_path"]))
        self.assertFalse(collector.calls)
        self.assertFalse(adapter.convert_incremental_calls)
        self.assertEqual(summary["qlib_update_status"], "skipped_requires_manual_rebuild")

    def test_resume_skips_previous_success(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        base_dir = Path(tmpdir.name)
        qlib_dir = self._prepare_registry(base_dir, active_symbols=300)
        adapter = _FakeAdapter(qlib_dir)
        collector = _FakeCollector()
        store = _FakeStore()
        date_resolution = {
            "requested_date": "2026-04-25",
            "resolved_trade_date": "2026-04-25",
            "last_qlib_date": "2026-04-17",
            "status": "success",
            "reason": "requested_date is available in qlib",
            "is_exact_match": True,
        }
        coverage_rows = [{"instrument": f"{i:06d}.SZ", "coverage_mismatch_reason": "ok"} for i in range(300)]
        repair_plan = {"stale_but_feature_available_count": 0, "stale_but_feature_available": []}
        previous_run_dir = base_dir / "runs" / "2026-04-25" / "shadow_presync_2026-04-25_080000"
        previous_run_dir.mkdir(parents=True, exist_ok=True)
        previous_manifest = previous_run_dir / "manifest.json"
        _write_json(previous_manifest, {"run_id": "shadow_presync_2026-04-25_080000"})
        _write_json(base_dir / "runs" / "latest_shadow_presync.json", {"manifest_path": str(previous_manifest)})
        (previous_run_dir / "02_raw").mkdir(parents=True, exist_ok=True)
        with (previous_run_dir / "02_raw" / "raw_update_plan.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["symbol", "status"])
            writer.writeheader()
            writer.writerow({"symbol": "000000.SZ", "status": "success"})
        with patch("qsys.ops.shadow_presync.resolve_daily_trade_date", return_value=date_resolution), patch(
            "qsys.ops.shadow_presync.QlibAdapter", return_value=adapter
        ), patch("qsys.ops.raw_sync.StockDataStore", return_value=store), patch(
            "qsys.ops.raw_sync.TushareCollector", return_value=collector
        ), patch("qsys.ops.qlib_sync.refresh_selected_symbols_from_raw", return_value={
            "summary": {
                "previous_qlib_last_date": "2026-04-17",
                "post_sync_qlib_last_date": "2026-04-17",
                "affected_symbol_count": 5,
                "symbols_attempted": 5,
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
        }), patch(
            "qsys.ops.shadow_presync.build_instrument_coverage_rows", return_value=coverage_rows
        ), patch("qsys.ops.shadow_presync.build_repair_plan", return_value=repair_plan):
            result = run_shadow_presync(
                base_dir,
                run_id="shadow_presync_2026-04-25_090807",
                universe="csi300",
                target_date="2026-04-25",
                lookback_days=20,
                apply=True,
                triggered_by="test",
                max_symbols=5,
                resume=True,
            )
        self.assertTrue(all(call[0][0] != "000000.SZ" for call in collector.calls))
        rows = list(csv.DictReader((Path(result["run_dir"]) / "02_raw" / "raw_update_plan.csv").open("r", encoding="utf-8")))
        skipped = [row for row in rows if row["symbol"] == "000000.SZ"][0]
        self.assertEqual(skipped["error"], "resume_skip_previous_success")

    def test_active_coverage_gate(self):
        _, result_ok, _, _ = self._run(active_symbols=300, apply=False)
        summary_ok = load_json(Path(result_ok["summary_path"]))
        self.assertTrue(summary_ok["ready_for_daily_shadow"])
        self.assertEqual(summary_ok["overall_status"], "success")
        _, result_bad, _, _ = self._run(active_symbols=3, apply=False)
        summary_bad = load_json(Path(result_bad["summary_path"]))
        self.assertFalse(summary_bad["ready_for_daily_shadow"])
        self.assertIn(summary_bad["overall_status"], {"failed", "partial"})


if __name__ == "__main__":
    unittest.main()
