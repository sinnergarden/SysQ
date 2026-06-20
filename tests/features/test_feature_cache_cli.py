"""CLI tests for feature cache commands."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]


class TestBackfillCli(unittest.TestCase):
    """backfill_feature_cache.py CLI smoke tests."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_backfill_reads_source_panel(self):
        """backfill CLI reads a temp parquet and produces cache plan."""
        # Create a synthetic source panel
        df = pd.DataFrame({
            "trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
            "ts_code": ["A"] * 5,
            "close": [100.0] * 5,
            "open": [100.0] * 5,
            "high": [101.0] * 5,
            "low": [99.0] * 5,
            "volume": [1e6] * 5,
            "amount": [1e8] * 5,
            "vwap": [100.0] * 5,
            "factor": [1.0] * 5,
            "float_shares": [1e8] * 5,
            "paused": [0.0] * 5,
        })
        panel_path = self.tmpdir / "source_panel.parquet"
        df.to_parquet(panel_path, index=False)

        # Run CLI
        result = subprocess.run(
            [
                "python", str(REPO / "scripts" / "dev" / "backfill_feature_cache.py"),
                "--feature-set", str(REPO / "configs" / "features" / "momentum_price_volume_v1.yaml"),
                "--source-panel", str(panel_path),
                "--source-manifest-hash", "test_cli",
                "--date-start", "2025-01-01",
                "--date-end", "2025-01-10",
                "--universe", "test_cli",
                "--cache-root", str(self.tmpdir / "fc"),
                "--force",
            ],
            capture_output=True, text=True, cwd=str(REPO),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, f"CLI failed: {result.stderr}")

        # Check cache files exist
        cache_dirs = list((self.tmpdir / "fc").rglob("*.parquet"))
        self.assertGreater(len(cache_dirs), 0)

    def test_backfill_missing_feature_fails(self):
        """backfill with wrong source panel minus required derived feature columns fails."""
        df = pd.DataFrame({"trade_date": ["2025-01-01"], "ts_code": ["A"]})
        panel_path = self.tmpdir / "bad_panel.parquet"
        df.to_parquet(panel_path, index=False)

        # Use a YAML with actual derived features (not just qlib expressions)
        # value_growth_multibagger_v1_features has 26 derived features
        result = subprocess.run(
            [
                "python", str(REPO / "scripts" / "dev" / "backfill_feature_cache.py"),
                "--feature-set", str(REPO / "configs" / "features" / "value_growth_multibagger_v1_features.yaml"),
                "--source-panel", str(panel_path),
                "--source-manifest-hash", "test_cli",
                "--cache-root", str(self.tmpdir / "fc2"),
                "--force",
            ],
            capture_output=True, text=True, cwd=str(REPO),
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0, f"Expected failure, got: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
