import unittest

from qsys.utils.logger import format_kv


class TestLoggerUtils(unittest.TestCase):
    def test_format_kv_skips_none_and_normalizes_values(self):
        rendered = format_kv(stage="readiness", aligned=True, missing_ratio=0.125, blockers=None, symbols=["000001.SZ", "000002.SZ"])
        self.assertIn("stage=readiness", rendered)
        self.assertIn("aligned=true", rendered)
        self.assertIn("missing_ratio=0.125", rendered)
        self.assertIn('symbols=["000001.SZ", "000002.SZ"]', rendered)
        self.assertNotIn("blockers=", rendered)


if __name__ == "__main__":
    unittest.main()
