import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.run_signal_quality as run_signal_quality
from qsys.live.signal_monitoring import SignalQualitySnapshot


class TestSignalQualityCli(unittest.TestCase):
    def test_require_ready_exits_non_zero_when_horizon_blocked(self):
        snapshot = SignalQualitySnapshot(
            summary={
                "horizon_1d": {"status": "failed", "reason": "missing_end_price", "signal_date": "2025-01-02"},
                "horizon_2d": {"status": "success", "reason": "ok", "signal_date": "2025-01-01"},
                "horizon_3d": {"status": "success", "reason": "ok", "signal_date": "2024-12-31"},
            },
            observations=None,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.run_signal_quality.QlibAdapter"), patch(
                "scripts.run_signal_quality.collect_signal_quality_snapshot",
                return_value=snapshot,
            ), patch(
                "scripts.run_signal_quality.write_signal_quality_outputs",
                return_value={"signal_quality_summary": str(Path(tmpdir) / "summary.json")},
            ), patch(
                "sys.argv",
                [
                    "run_signal_quality.py",
                    "--date",
                    "2025-01-03",
                    "--signal_dir",
                    tmpdir,
                    "--output_dir",
                    tmpdir,
                    "--require_ready",
                ],
            ):
                code = run_signal_quality.main()

        self.assertEqual(code, 2)

    def test_main_returns_zero_for_successful_snapshot(self):
        snapshot = SignalQualitySnapshot(
            summary={
                "horizon_1d": {"status": "success", "reason": "ok", "signal_date": "2025-01-02"},
                "horizon_2d": {"status": "success", "reason": "ok", "signal_date": "2025-01-01"},
                "horizon_3d": {"status": "success", "reason": "ok", "signal_date": "2024-12-31"},
            },
            observations=None,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.run_signal_quality.QlibAdapter"), patch(
                "scripts.run_signal_quality.collect_signal_quality_snapshot",
                return_value=snapshot,
            ), patch(
                "scripts.run_signal_quality.write_signal_quality_outputs",
                return_value={"signal_quality_summary": str(Path(tmpdir) / "summary.json")},
            ), patch(
                "sys.argv",
                [
                    "run_signal_quality.py",
                    "--date",
                    "2025-01-03",
                    "--signal_dir",
                    tmpdir,
                    "--output_dir",
                    tmpdir,
                ],
            ):
                code = run_signal_quality.main()

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
