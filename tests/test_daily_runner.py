"""Tests for qsys/ops/daily_runner.py — DailyRunner skeleton."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qsys.ops.daily_runner import DailyRunner
from qsys.ops.run_context import DailyRunContext


class TestDailyRunner(unittest.TestCase):
    """DailyRunner — validates context, creates directories, logs stage."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.run_root = Path(self.tmpdir.name) / "run"
        self.project_root = Path(self.tmpdir.name) / "proj"
        self.runner = DailyRunner()

        self.ctx_preopen = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        self.ctx_postclose = DailyRunContext(
            trade_date="2026-05-18",
            mode="postclose",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        self.ctx_train = DailyRunContext(
            trade_date="2026-05-18",
            mode="train",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        self.ctx_debug = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
            debug_run=True,
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_run_preopen_creates_run_root(self):
        self.assertFalse(self.run_root.exists())
        self.runner.run_preopen(self.ctx_preopen)
        self.assertTrue(self.run_root.exists())

    def test_run_preopen_rejects_wrong_mode(self):
        with self.assertRaises(ValueError):
            self.runner.run_preopen(self.ctx_postclose)

    def test_run_postclose_creates_run_root(self):
        self.assertFalse(self.run_root.exists())
        self.runner.run_postclose(self.ctx_postclose)
        self.assertTrue(self.run_root.exists())

    def test_run_postclose_rejects_wrong_mode(self):
        with self.assertRaises(ValueError):
            self.runner.run_postclose(self.ctx_preopen)

    def test_run_train_creates_run_root(self):
        self.assertFalse(self.run_root.exists())
        self.runner.run_train(self.ctx_train)
        self.assertTrue(self.run_root.exists())

    def test_run_train_rejects_wrong_mode(self):
        with self.assertRaises(ValueError):
            self.runner.run_train(self.ctx_preopen)

    def test_debug_mode_tag_in_output(self):
        """Debug context should not throw and should create run_root."""
        self.runner.run_preopen(self.ctx_debug)
        self.assertTrue(self.run_root.exists())

    def test_multiple_modes_independent(self):
        """Each mode should work independently."""
        self.runner.run_preopen(self.ctx_preopen)
        self.runner.run_postclose(self.ctx_postclose)
        self.runner.run_train(self.ctx_train)
        self.assertTrue(self.run_root.exists())


if __name__ == "__main__":
    unittest.main()
