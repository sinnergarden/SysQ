"""Regression tests for instrument symbol format matching.

Verifies that Tushare-style codes (600519.SH) match correctly against
all.txt when creating/refreshing universe instrument files.
"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd


def _write_all_txt(dir_path: Path, symbols: list[tuple[str, str, str]]) -> Path:
    """Write a mock all.txt with tab-separated symbol/start/end columns."""
    p = dir_path / "instruments"
    p.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(row) for row in symbols]
    (p / "all.txt").write_text("\n".join(lines) + "\n")
    return p / "all.txt"


FORMAT_TUSHARE = [
    ("600519.SH", "2010-01-04", "2026-05-15"),
    ("000001.SZ", "2010-01-04", "2026-05-15"),
    ("300750.SZ", "2018-06-11", "2026-05-15"),
]

FORMAT_QLIB_NATIVE = [
    ("SH600519", "2010-01-04", "2026-05-15"),
    ("SZ000001", "2010-01-04", "2026-05-15"),
    ("SZ300750", "2018-06-11", "2026-05-15"),
]


def _run_match(all_txt_symbols: list[tuple[str, str, str]], universe_codes: list[str], tmp_dir: Path) -> list[str]:
    """Simulate the matching logic from _refresh_universe_instruments."""
    all_txt_path = _write_all_txt(tmp_dir, all_txt_symbols)
    df_all = pd.read_csv(all_txt_path, sep="\t", names=["symbol", "start_date", "end_date"])
    code_set = set(universe_codes)
    df_universe = df_all[df_all["symbol"].isin(code_set)]
    return df_universe["symbol"].tolist()


class TestInstrumentSymbolMatching(unittest.TestCase):
    """Regression: instrument format matching between Tushare codes and all.txt."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_tushare_format_matches(self):
        """Tushare format (600519.SH) in all.txt matches Tushare codes."""
        codes = ["600519.SH", "000001.SZ", "300750.SZ"]
        matched = _run_match(FORMAT_TUSHARE, codes, self.tmp_dir)
        self.assertEqual(len(matched), 3)
        self.assertIn("600519.SH", matched)
        self.assertIn("000001.SZ", matched)
        self.assertIn("300750.SZ", matched)

    def test_tushare_format_partial_match(self):
        """Only matching subset is returned."""
        codes = ["600519.SH", "999999.SZ"]
        matched = _run_match(FORMAT_TUSHARE, codes, self.tmp_dir)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched, ["600519.SH"])

    def test_tushare_format_empty(self):
        """No matching codes returns empty list."""
        codes = ["999999.SZ", "888888.SH"]
        matched = _run_match(FORMAT_TUSHARE, codes, self.tmp_dir)
        self.assertEqual(len(matched), 0)

    def test_qlib_native_format_does_not_match(self):
        """If all.txt uses SH600519 format, Tushare codes don't match w/o formatter.

        This is a known incompatibility. If Qlib dump generates SH-prefixed symbols,
        _refresh_universe_instruments will produce an empty universe file.
        """
        codes = ["600519.SH", "000001.SZ"]
        matched = _run_match(FORMAT_QLIB_NATIVE, codes, self.tmp_dir)
        self.assertEqual(len(matched), 0,
                         "Tushare codes should NOT match SH-prefixed format")

    def test_create_universe_file_roundtrip(self):
        """Verifies that writing then reading back a universe file works correctly."""
        all_txt_path = _write_all_txt(self.tmp_dir, FORMAT_TUSHARE)
        qlib_dir = all_txt_path.parent

        codes = ["600519.SH", "000001.SZ"]
        df_all = pd.read_csv(all_txt_path, sep="\t", names=["symbol", "start_date", "end_date"])
        df_universe = df_all[df_all["symbol"].isin(set(codes))]

        out_path = qlib_dir / "csi800.txt"
        df_universe.to_csv(out_path, sep="\t", header=False, index=False)

        written = pd.read_csv(out_path, sep="\t", names=["symbol", "start_date", "end_date"])
        self.assertEqual(len(written), 2)
        self.assertIn("600519.SH", written["symbol"].values)
        self.assertIn("000001.SZ", written["symbol"].values)


if __name__ == "__main__":
    unittest.main()
