import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qsys.ops.universe_sync import build_universe_snapshot


class _FakeAdapter:
    def __init__(self, qlib_dir: Path):
        self.qlib_dir = qlib_dir


class TestUniverseSync(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.base_dir = Path(self.tmpdir.name)
        self.qlib_dir = self.base_dir / "data" / "qlib_bin"
        (self.qlib_dir / "instruments").mkdir(parents=True, exist_ok=True)
        self.adapter = _FakeAdapter(self.qlib_dir)

    def test_missing_csi800_registry_is_bootstrap_preview_on_dry_run(self):
        with patch("qsys.ops.universe_sync.TushareCollector") as mock_collector, patch(
            "qsys.ops.universe_sync.read_calendar_summary",
            return_value={"calendar_first_date": "2010-01-04", "calendar_last_date": "2026-04-17", "calendar_count": 1},
        ):
            mock_collector.return_value.get_universe.return_value = [f"{i:06d}.SZ" for i in range(800)]
            symbols, summary, _, _ = build_universe_snapshot(
                adapter=self.adapter,
                universe="csi800",
                as_of_date="2026-04-25",
                output_dir=self.base_dir / "out_preview",
                apply=False,
            )
        self.assertEqual(len(symbols), 800)
        self.assertEqual(summary["source"], "bootstrap_preview")
        self.assertFalse((self.qlib_dir / "instruments" / "csi800.txt").exists())

    def test_missing_csi800_registry_is_written_on_apply(self):
        with patch("qsys.ops.universe_sync.TushareCollector") as mock_collector, patch(
            "qsys.ops.universe_sync.read_calendar_summary",
            return_value={"calendar_first_date": "2010-01-04", "calendar_last_date": "2026-04-17", "calendar_count": 1},
        ):
            mock_collector.return_value.get_universe.return_value = [f"{i:06d}.SZ" for i in range(800)]
            _, summary, _, _ = build_universe_snapshot(
                adapter=self.adapter,
                universe="csi800",
                as_of_date="2026-04-25",
                output_dir=self.base_dir / "out_apply",
                apply=True,
            )
        path = self.qlib_dir / "instruments" / "csi800.txt"
        self.assertTrue(path.exists())
        self.assertEqual(summary["source"], "bootstrapped_registry")
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(first_line, "000000.SZ\t2010-01-04\t2026-04-17")


if __name__ == "__main__":
    unittest.main()
