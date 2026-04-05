import json
import tempfile
import unittest
from pathlib import Path

from qsys.live.ops_manifest import build_manifest_path, load_manifest, update_manifest


class TestOpsManifest(unittest.TestCase):
    def test_update_manifest_merges_pre_open_and_post_close_stages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = update_manifest(
                report_dir=tmpdir,
                execution_date="2026-04-03",
                signal_date="2026-04-02",
                stage="pre_open",
                status="success",
                report_path="/tmp/pre_open.json",
                artifacts={"signal_basket": "/tmp/signal_basket.csv"},
                blockers=["pre_open_blocker"],
                summary={"shadow_plan": {"trades": 5}},
            )
            manifest_path = update_manifest(
                report_dir=tmpdir,
                execution_date="2026-04-03",
                signal_date="2026-04-02",
                stage="post_close",
                status="partial",
                report_path="/tmp/post_close.json",
                artifacts={"reconciliation": "/tmp/reconcile.csv"},
                blockers=["post_close_blocker"],
                summary={"signal_quality": {"horizon_1d": {"status": "success"}}},
            )

            manifest = load_manifest(manifest_path)
            self.assertEqual(manifest["execution_date"], "2026-04-03")
            self.assertEqual(manifest["signal_date"], "2026-04-02")
            self.assertIn("pre_open", manifest["stages"])
            self.assertIn("post_close", manifest["stages"])
            self.assertEqual(manifest["stages"]["pre_open"]["status"], "success")
            self.assertEqual(manifest["stages"]["post_close"]["status"], "partial")
            self.assertEqual(manifest["artifacts"]["signal_basket"], "/tmp/signal_basket.csv")
            self.assertEqual(manifest["artifacts"]["reconciliation"], "/tmp/reconcile.csv")
            self.assertIn("pre_open_blocker", manifest["blockers"])
            self.assertIn("post_close_blocker", manifest["blockers"])

    def test_rerun_replaces_stage_blockers_in_top_level_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = update_manifest(
                report_dir=tmpdir,
                execution_date="2026-04-03",
                signal_date="2026-04-02",
                stage="pre_open",
                status="partial",
                blockers=["stale_pre_open_blocker"],
                notes=["old_note"],
            )
            manifest_path = update_manifest(
                report_dir=tmpdir,
                execution_date="2026-04-03",
                signal_date="2026-04-02",
                stage="pre_open",
                status="success",
                blockers=[],
                notes=["new_note"],
            )

            manifest = load_manifest(manifest_path)
            self.assertEqual(manifest["blockers"], [])
            self.assertEqual(manifest["notes"], ["new_note"])
            self.assertEqual(manifest["stages"]["pre_open"]["blockers"], [])

    def test_build_manifest_path_uses_execution_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = build_manifest_path(tmpdir, "2026-04-03")
            self.assertEqual(path.name, "daily_ops_manifest_2026-04-03.json")


if __name__ == "__main__":
    unittest.main()
