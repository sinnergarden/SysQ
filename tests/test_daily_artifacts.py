import json
import tempfile
import unittest
from pathlib import Path

from qsys.live.daily_artifacts import archive_daily_artifacts, build_daily_summary_bundle, extract_account_snapshot
from qsys.live.ops_manifest import update_manifest


class _FakeAccount:
    def __init__(self, states):
        self.states = states

    def get_state(self, date, account_name):
        return self.states.get((date, account_name))


class TestDailyArtifacts(unittest.TestCase):
    def test_extract_account_snapshot_from_account_state(self):
        account = _FakeAccount(
            {
                ("2026-04-03", "shadow"): {
                    "date": "2026-04-03",
                    "cash": 12345.0,
                    "total_assets": 23456.0,
                    "positions": {
                        "SH600000": {"amount": 100, "price": 10.5, "cost_basis": 10.0},
                    },
                }
            }
        )

        snapshot = extract_account_snapshot(account, date="2026-04-03", account_name="shadow")
        self.assertEqual(snapshot["status"], "available")
        self.assertEqual(snapshot["position_count"], 1)
        self.assertEqual(snapshot["positions"][0]["symbol"], "SH600000")

    def test_archive_and_build_daily_summary_bundle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            archive_root = tmp / "daily"
            source = tmp / "source"
            source.mkdir()

            pre_report = source / "daily_ops_pre_open_20260403.json"
            pre_report.write_text('{"status":"success"}', encoding="utf-8")
            pre_manifest = Path(
                update_manifest(
                    report_dir=source,
                    execution_date="2026-04-03",
                    signal_date="2026-04-02",
                    stage="pre_open",
                    status="success",
                    report_path=str(pre_report),
                    artifacts={"report": str(pre_report)},
                    summary={
                        "shadow_plan": {
                            "status": "ready",
                            "trades": 5,
                            "buy_trades": 3,
                            "sell_trades": 2,
                            "total_value": 100000.0,
                            "symbols": ["AAA", "BBB"],
                        },
                        "real_plan": {
                            "status": "ready",
                            "trades": 2,
                            "buy_trades": 1,
                            "sell_trades": 1,
                            "total_value": 20000.0,
                            "symbols": ["CCC"],
                        },
                        "account_snapshots": {
                            "shadow": {
                                "status": "available",
                                "as_of_date": "2026-04-02",
                                "cash": 800000.0,
                                "total_assets": 1000000.0,
                                "position_count": 5,
                            }
                        },
                    },
                )
            )

            post_report = source / "daily_ops_post_close_20260402.json"
            post_report.write_text('{"status":"partial"}', encoding="utf-8")
            post_manifest = Path(
                update_manifest(
                    report_dir=source,
                    execution_date="2026-04-02",
                    signal_date="2026-04-01",
                    stage="post_close",
                    status="partial",
                    report_path=str(post_report),
                    artifacts={"report": str(post_report)},
                    summary={
                        "reconciliation": {
                            "cash": {"diff": 100.0},
                            "total_assets": {"diff": -200.0},
                        },
                        "signal_quality": {"status": "partial"},
                    },
                )
            )

            archive_daily_artifacts(
                execution_date="2026-04-03",
                signal_date="2026-04-02",
                stage="pre_open",
                artifacts={"report": str(pre_report), "manifest": str(pre_manifest)},
                archive_root=archive_root,
            )
            archive_daily_artifacts(
                execution_date="2026-04-02",
                signal_date="2026-04-01",
                stage="post_close",
                artifacts={"report": str(post_report), "manifest": str(post_manifest)},
                archive_root=archive_root,
            )

            bundle = build_daily_summary_bundle(execution_date="2026-04-03", archive_root=archive_root, lookback_days=2)

            self.assertTrue(Path(bundle.report_markdown_path).exists())
            self.assertTrue(Path(bundle.report_json_path).exists())
            self.assertIn("Current Day Predictions", bundle.report_text)
            self.assertIn("status=available", bundle.report_text)
            self.assertIn("2026-04-02: cash_diff=100.0", bundle.report_text)
            self.assertTrue(bundle.report_markdown_path.endswith("pre_open/reports/daily_ops_digest_2026-04-03.md"))

            index_payload = json.loads(Path(bundle.snapshot_index_path).read_text(encoding="utf-8"))
            self.assertIn("pre_open", index_payload["stages"])
            self.assertTrue(index_payload["stages"]["pre_open"]["report_path"].endswith(pre_report.name))
            self.assertTrue(index_payload["stages"]["pre_open"]["manifest_path"].endswith(pre_manifest.name))

            digest_payload = json.loads(Path(bundle.report_json_path).read_text(encoding="utf-8"))
            self.assertNotIn("manifest", digest_payload)
            self.assertTrue(digest_payload["stages"]["pre_open"]["report_path"].endswith(pre_report.name))
            self.assertTrue(digest_payload["stages"]["pre_open"]["manifest_path"].endswith(pre_manifest.name))

    def test_archive_daily_artifacts_keeps_account_db_as_external_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            db_path = tmp / "data" / "meta" / "real_account.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_text("placeholder", encoding="utf-8")

            archive_info = archive_daily_artifacts(
                execution_date="2026-04-03",
                signal_date="2026-04-02",
                stage="pre_open",
                artifacts={"account_db": str(db_path)},
                archive_root=tmp / "daily",
            )

            index_payload = json.loads(Path(archive_info["index_path"]).read_text(encoding="utf-8"))
            db_artifact = index_payload["stages"]["pre_open"]["artifacts"]["account_db"]
            self.assertEqual(db_artifact["category"], "external_reference")
            self.assertEqual(db_artifact["path"], str(db_path))
            self.assertFalse((tmp / "daily" / "2026-04-03" / "pre_open" / "accounts").exists())

    def test_build_daily_summary_bundle_merges_stage_manifests(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            archive_root = tmp / "daily"
            execution_date = "2026-04-03"
            pre_manifest_dir = archive_root / execution_date / "pre_open" / "manifests"
            post_manifest_dir = archive_root / execution_date / "post_close" / "manifests"
            pre_report = archive_root / execution_date / "pre_open" / "reports" / "daily_ops_pre_open.json"
            post_report = archive_root / execution_date / "post_close" / "reports" / "daily_ops_post_close.json"
            pre_report.parent.mkdir(parents=True, exist_ok=True)
            post_report.parent.mkdir(parents=True, exist_ok=True)
            pre_report.write_text('{"status":"success"}', encoding="utf-8")
            post_report.write_text('{"status":"partial"}', encoding="utf-8")

            pre_manifest = update_manifest(
                report_dir=pre_manifest_dir,
                execution_date=execution_date,
                signal_date="2026-04-02",
                stage="pre_open",
                status="success",
                report_path=str(pre_report),
                summary={"shadow_plan": {"status": "ready", "trades": 1, "symbols": ["AAA"]}},
            )
            post_manifest = update_manifest(
                report_dir=post_manifest_dir,
                execution_date=execution_date,
                signal_date="2026-04-02",
                stage="post_close",
                status="partial",
                report_path=str(post_report),
                summary={"reconciliation": {"cash": {"diff": 12.0}}},
            )

            archive_daily_artifacts(
                execution_date=execution_date,
                signal_date="2026-04-02",
                stage="pre_open",
                artifacts={"report": str(pre_report), "manifest": str(pre_manifest)},
                archive_root=archive_root,
            )
            archive_daily_artifacts(
                execution_date=execution_date,
                signal_date="2026-04-02",
                stage="post_close",
                artifacts={"report": str(post_report), "manifest": str(post_manifest)},
                archive_root=archive_root,
            )

            bundle = build_daily_summary_bundle(execution_date=execution_date, archive_root=archive_root)
            digest_payload = json.loads(Path(bundle.report_json_path).read_text(encoding="utf-8"))

            self.assertIn("pre_open", digest_payload["stages"])
            self.assertIn("post_close", digest_payload["stages"])
            self.assertEqual(digest_payload["stages"]["pre_open"]["status"], "success")
            self.assertEqual(digest_payload["stages"]["post_close"]["status"], "partial")


if __name__ == "__main__":
    unittest.main()
