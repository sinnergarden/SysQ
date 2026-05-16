import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from qsys.ops.qlib_candidate import build_candidate


class _FakeAdapter:
    def __init__(self, *args, qlib_dir=None, raw_dir=None, **kwargs):
        self.qlib_dir = Path(qlib_dir) if qlib_dir is not None else Path(raw_dir).parent.parent / "qlib_bin"
        self.raw_dir = Path(raw_dir) if raw_dir is not None else Path.cwd()

    def convert_all(self, *, output_qlib_dir=None, selected_symbols=None, until_date=None, csv_output_dir=None, refresh_universes=None):
        out = Path(output_qlib_dir)
        (out / "calendars").mkdir(parents=True, exist_ok=True)
        (out / "instruments").mkdir(parents=True, exist_ok=True)
        (out / "features").mkdir(parents=True, exist_ok=True)
        (out / "calendars" / "day.txt").write_text("2026-04-29\n2026-04-30\n", encoding="utf-8")
        (out / "instruments" / "all.txt").write_text("000001.SZ\t2025-01-01\t2026-04-30\n", encoding="utf-8")
        self.called_output_qlib_dir = Path(output_qlib_dir)
        self.called_refresh_universes = refresh_universes


class TestQlibCandidateBuild(unittest.TestCase):
    def test_build_candidate_uses_configurable_output_dir_and_not_formal_qlib(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            raw_dir = base_dir / "data" / "raw" / "daily"
            raw_dir.mkdir(parents=True)
            pd.DataFrame({"trade_date": ["2025-01-02", "2026-04-30"], "open": [1, 2]}).to_feather(raw_dir / "000001.SZ.feather")
            formal_qlib_dir = base_dir / "data" / "qlib_bin"
            (formal_qlib_dir / "instruments").mkdir(parents=True)
            marker = formal_qlib_dir / "marker.txt"
            marker.write_text("formal", encoding="utf-8")
            candidate_dir = base_dir / "data" / "qlib_bin_candidate_test"

            def _fake_adapter_factory(*args, qlib_dir=None, raw_dir=None, **kwargs):
                return _FakeAdapter(qlib_dir=qlib_dir, raw_dir=raw_dir or raw_dir_default)

            raw_dir_default = raw_dir
            with patch("qsys.ops.qlib_candidate.TushareCollector") as mock_collector, patch(
                "qsys.ops.qlib_candidate.QlibAdapter",
                side_effect=lambda *args, qlib_dir=None, raw_dir=None, **kwargs: _FakeAdapter(qlib_dir=qlib_dir, raw_dir=raw_dir or raw_dir_default),
            ), patch(
                "qsys.ops.qlib_candidate.build_universe_snapshot",
                side_effect=[(["000001.SZ"], {"source": "bootstrapped_registry"}, None, None), (["000001.SZ"], {"source": "bootstrapped_registry"}, None, None)],
            ), patch(
                "qsys.ops.qlib_candidate.read_calendar_summary",
                return_value={"calendar_first_date": "2025-01-02", "calendar_last_date": "2026-04-30", "calendar_count": 2},
            ):
                mock_collector.return_value.get_universe.return_value = ["000001.SZ"]
                result = build_candidate(
                    base_dir=base_dir,
                    universe="csi800",
                    end_date="2026-04-30",
                    output_qlib_dir=candidate_dir,
                    output_dir=base_dir / "out",
                )

            self.assertEqual(Path(result["candidate_dir"]), candidate_dir)
            self.assertTrue((candidate_dir / "instruments" / "all.txt").exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "formal")


if __name__ == "__main__":
    unittest.main()
