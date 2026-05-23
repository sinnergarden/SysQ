"""Tests for qsys/ops/run_context.py — DailyRunContext and resolve_run_root."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qsys.ops.run_context import DailyRunContext, resolve_run_root


class TestDailyRunContext(unittest.TestCase):
    """DailyRunContext dataclass — field access and output_dir_resolved."""

    def test_basic_fields(self):
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=Path("/tmp/run"),
            project_root=Path("/tmp/proj"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        self.assertEqual(ctx.trade_date, "2026-05-18")
        self.assertEqual(ctx.mode, "preopen")
        self.assertEqual(ctx.strategy_id, "alpha_v1")
        self.assertEqual(ctx.account_id, "shadow_alpha_v1")

    def test_output_dir_resolved_with_override(self):
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=Path("/custom/output"),
            project_root=Path("/tmp/proj"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
            output_dir=Path("/custom/output"),
        )
        self.assertEqual(ctx.output_dir_resolved, Path("/custom/output"))

    def test_output_dir_resolved_default(self):
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=Path("/tmp/run"),
            project_root=Path("/tmp/proj"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        self.assertEqual(ctx.output_dir_resolved, Path("/tmp/run"))

    def test_flags_default_to_false(self):
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=Path("/tmp/run"),
            project_root=Path("/tmp/proj"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
        )
        self.assertFalse(ctx.debug_run)
        self.assertFalse(ctx.force_rerun)
        self.assertFalse(ctx.notify_only)
        self.assertFalse(ctx.no_notify)
        self.assertIsNone(ctx.reason)
        self.assertIsNone(ctx.data_date)
        self.assertIsNone(ctx.ledger_db_path)

    def test_optional_fields(self):
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="postclose",
            run_root=Path("/tmp/run"),
            project_root=Path("/tmp/proj"),
            strategy_id="alpha_v1",
            account_id="shadow_alpha_v1",
            data_date="2026-05-17",
            ledger_db_path="/tmp/trade.db",
            debug_run=True,
            force_rerun=True,
            reason="test rerun",
        )
        self.assertEqual(ctx.data_date, "2026-05-17")
        self.assertEqual(ctx.ledger_db_path, "/tmp/trade.db")
        self.assertTrue(ctx.debug_run)
        self.assertTrue(ctx.force_rerun)
        self.assertEqual(ctx.reason, "test rerun")


class TestResolveRunRoot(unittest.TestCase):
    """resolve_run_root — production vs debug vs output_dir resolution."""

    def test_production_path(self):
        result = resolve_run_root(
            Path("/proj"), "alpha_v1", "2026-05-18",
            debug_run=False, output_dir=None,
        )
        expected = Path("/proj") / "experiments" / "alpha_v1_daily" / "2026-05-18"
        self.assertEqual(result, expected)

    def test_debug_with_output_dir(self):
        result = resolve_run_root(
            Path("/proj"), "alpha_v1", "2026-05-18",
            debug_run=True, output_dir=Path("/custom"),
        )
        self.assertEqual(result, Path("/custom"))

    def test_debug_without_output_dir(self):
        result = resolve_run_root(
            Path("/proj"), "alpha_v1", "2026-05-18",
            debug_run=True, output_dir=None,
        )
        expected = Path("/proj") / "experiments" / "debug" / "alpha_v1" / "2026-05-18_"
        self.assertTrue(str(result).startswith(str(expected)))

    def test_different_strategy_id(self):
        result = resolve_run_root(
            Path("/proj"), "beta_v2", "2026-05-18",
            debug_run=False, output_dir=None,
        )
        expected = Path("/proj") / "experiments" / "beta_v2_daily" / "2026-05-18"
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
