"""Tests for MTM account isolation — no cross-strategy fallback.

When the ledger has an account with cash but zero positions, MTM should return
a cash-only snapshot rather than falling back to another strategy's shadow
files.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

# Module-level import so the module-level path logic in mtm.py doesn't break
os.environ.setdefault("PROJECT_ROOT", str(Path.cwd()))


class TestMtmEmptyAccount(unittest.TestCase):
    """try_mark_to_market with empty positions in ledger."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.account_id = "test_strat"
        self.db_path = self.tmpdir / "test.db"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _init_empty_ledger(self):
        """Create a ledger with cash-only account, no positions."""
        from qsys.ledger.service import LedgerService

        svc = LedgerService(str(self.db_path))
        svc.create_account(self.account_id, "shadow", initial_cash=100_000.0)
        svc.close()

    def test_cash_only_returns_snapshot_without_fallback(self):
        """Account with cash, no positions returns cash-only snapshot."""
        self._init_empty_ledger()

        from qsys.ops.mtm import try_mark_to_market

        result = try_mark_to_market(
            trade_date="2026-05-22",
            output_dir=self.tmpdir,
            db_path=str(self.db_path),
            project_root=self.tmpdir,
            shadow_account_id=self.account_id,
            get_stock_name_fn=lambda c: c,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["cash"], 100_000.0)
        self.assertEqual(result["market_value"], 0.0)
        self.assertEqual(result["total_value"], 100_000.0)
        self.assertEqual(result["priced_count"], 0)
        self.assertEqual(result["total_positions"], 0)
        self.assertIn("cumulative_pnl", result)
        self.assertIn("daily_pnl", result)

        # Verify MTM snapshot was written
        snapshot_path = self.tmpdir / "mtm" / "mtm_snapshot.json"
        self.assertTrue(snapshot_path.exists())

    def test_empty_account_no_cross_strategy_fallback(self):
        """Empty account does NOT read another strategy's shadow files."""
        self._init_empty_ledger()

        from qsys.ops.mtm import try_mark_to_market

        # Ensure no shadow files exist in the tmpdir
        shadow_acct = self.tmpdir / "shadow" / "account.json"
        shadow_pos = self.tmpdir / "shadow" / "positions.csv"
        self.assertFalse(shadow_acct.exists())

        result = try_mark_to_market(
            trade_date="2026-05-22",
            output_dir=self.tmpdir,
            db_path=str(self.db_path),
            project_root=self.tmpdir,
            shadow_account_id=self.account_id,
            get_stock_name_fn=lambda c: c,
        )
        # Must not crash, must return a valid cash-only snapshot
        self.assertIsNotNone(result)
        self.assertEqual(result["cash"], 100_000.0)
        self.assertFalse(shadow_acct.exists(),  # should not have created shadow files
                         "Cross-strategy fallback would create shadow/account.json")


if __name__ == "__main__":
    unittest.main()
