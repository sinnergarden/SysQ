"""CLI tests for feature cache commands."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]


class TestBackfillCli(unittest.TestCase):
    """backfill_feature_cache.py CLI smoke tests."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_minimal_yaml(self, name: str, features: list[str]) -> Path:
        """Write a minimal legacy YAML for testing."""
        p = self.tmpdir / "yaml" / f"{name}.yaml"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            yaml.dump({"feature_list_id": name, "features": features}, f)
        return p

    def test_backfill_reads_source_panel(self):
        """backfill CLI reads a temp parquet and produces cache files."""
        df = pd.DataFrame({
            "trade_date": pd.date_range("2025-01-01", periods=5, freq="B"),
            "ts_code": ["A"] * 5,
            "close": [100.0] * 5,
            "open": [100.0] * 5,
            "high": [101.0] * 5,
            "low": [99.0] * 5,
            "volume": [1e6] * 5,
            "amount": [1e8] * 5,
            "float_shares": [1e8] * 5,
        })
        panel_path = self.tmpdir / "source_panel.parquet"
        df.to_parquet(panel_path, index=False)

        yaml_path = self._write_minimal_yaml(
            "test_cli_backfill",
            ["close_to_open_gap_1d", "open_to_close_ret"],
        )

        result = subprocess.run(
            [
                "python", str(REPO / "scripts" / "dev" / "backfill_feature_cache.py"),
                "--feature-set", str(yaml_path),
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

        cache_files = list((self.tmpdir / "fc").rglob("*.parquet"))
        self.assertGreater(len(cache_files), 0)

    def test_backfill_missing_feature_fails(self):
        """backfill with source panel missing required columns fails."""
        df = pd.DataFrame({"trade_date": ["2025-01-01"], "ts_code": ["A"]})
        panel_path = self.tmpdir / "bad_panel.parquet"
        df.to_parquet(panel_path, index=False)

        # This YAML has feature names that the binary panel doesn't satisfy
        yaml_path = self._write_minimal_yaml(
            "test_cli_fail",
            ["close_to_open_gap_1d", "nonexistent_feature"],
        )

        result = subprocess.run(
            [
                "python", str(REPO / "scripts" / "dev" / "backfill_feature_cache.py"),
                "--feature-set", str(yaml_path),
                "--source-panel", str(panel_path),
                "--source-manifest-hash", "test_cli",
                "--cache-root", str(self.tmpdir / "fc2"),
                "--force",
            ],
            capture_output=True, text=True, cwd=str(REPO),
            timeout=60,
        )
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
