"""Tests for qsys.ops.shadow_execution."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from qsys.ops.shadow_execution import (
    POSITION_COLUMNS,
    ShadowRebalanceArtifacts,
    commit_execution_artifacts,
    execute_shadow_plan,
    positions_frame,
    write_execution_to_ledger,
    write_failed_execution_summary,
)
from qsys.trader.account import Account, Position


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_account() -> Account:
    a = Account(init_cash=1_000_000.0)
    a.positions["600001"] = Position("600001", 1000, 1000, 10.0)
    return a


def _fake_plan_dir(plan_dir: Path, strategy_id: str = "test_strat") -> None:
    plan_dir.mkdir(parents=True)
    import json
    (plan_dir / "plan_meta.json").write_text(json.dumps({
        "trade_date": "2026-05-22",
        "strategy_id": strategy_id,
        "strategy_version": "1.0",
        "top_n": 10, "buffer_hold": 30, "buffer_buy": 20, "single_stock_cap": 0.10,
    }))
    pd.DataFrame([
        {"instrument": "600001", "side": "buy", "target_weight": 1.0,
         "current_weight": 0.0, "target_value": 1_000_000.0, "current_value": 0.0,
         "diff_value": 1_000_000.0, "requested_qty": 1000, "reason": "target",
         "trade_date": "2026-05-22"},
    ]).to_csv(plan_dir / "order_intents.csv", index=False)


# ── Tests: positions_frame ──────────────────────────────────────────────────

class TestPositionsFrame(unittest.TestCase):
    """positions_frame — output contract."""

    def test_output_columns(self):
        account = _make_account()
        prices = {"600001": 10.5}
        df = positions_frame(account, prices)
        self.assertEqual(df.columns.tolist(), POSITION_COLUMNS)
        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["instrument"], "600001")


# ── Tests: write_execution_to_ledger ────────────────────────────────────────

class TestWriteExecutionToLedger(unittest.TestCase):
    """write_execution_to_ledger — run_id handling and idempotency."""

    def _mock_service(self):
        svc = MagicMock()
        svc.get_run.return_value = None
        svc.get_latest_trade_date.return_value = None
        svc.get_initial_cash.return_value = 1_000_000.0
        return svc

    def test_no_run_id_derives_default(self):
        """Without run_id, derive f'{date}.{strategy_id}.shadow'."""
        svc = self._mock_service()
        with patch("qsys.ledger.service.LedgerService", return_value=svc):
            write_execution_to_ledger(
                db_path="/tmp/test.db",
                execution_date="2026-05-22",
                strategy_id="alpha_v2",
                orders=[],
                ledger_rows=[],
                results=[],
                close_prices={},
                cash_after=1_000_000.0,
                market_value_after=0.0,
                total_value_after=1_000_000.0,
                positions_after=pd.DataFrame(columns=POSITION_COLUMNS),
            )
        # Verify the run was started with the derived run_id
        svc.start_run.assert_called_once()
        call_kwargs = svc.start_run.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "2026-05-22.alpha_v2.shadow")

    def test_custom_run_id(self):
        """When run_id is provided, use as-is."""
        svc = self._mock_service()
        with patch("qsys.ledger.service.LedgerService", return_value=svc):
            write_execution_to_ledger(
                db_path="/tmp/test.db",
                execution_date="2026-05-22",
                strategy_id="alpha_v1",
                orders=[],
                ledger_rows=[],
                results=[],
                close_prices={},
                cash_after=1_000_000.0,
                market_value_after=0.0,
                total_value_after=1_000_000.0,
                positions_after=pd.DataFrame(columns=POSITION_COLUMNS),
                run_id="alpha_v1_execute_2026-05-22",
            )
        svc.start_run.assert_called_once()
        call_kwargs = svc.start_run.call_args[1]
        self.assertEqual(call_kwargs["run_id"], "alpha_v1_execute_2026-05-22")

    def test_completed_run_skips(self):
        """If run exists and is completed, skip writing."""
        svc = self._mock_service()
        svc.get_run.return_value = {"run_id": "test", "status": "completed"}
        with patch("qsys.ledger.service.LedgerService", return_value=svc):
            write_execution_to_ledger(
                db_path="/tmp/test.db",
                execution_date="2026-05-22",
                strategy_id="alpha_v2",
                orders=[],
                ledger_rows=[],
                results=[],
                close_prices={},
                cash_after=1_000_000.0,
                market_value_after=0.0,
                total_value_after=1_000_000.0,
                positions_after=pd.DataFrame(columns=POSITION_COLUMNS),
            )
        svc.start_run.assert_not_called()


# ── Tests: execute_shadow_plan ──────────────────────────────────────────────

class TestExecuteShadowPlan(unittest.TestCase):
    """execute_shadow_plan — plan execution."""

    def test_missing_plan_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(Exception):
                execute_shadow_plan(
                    base_dir=tmp,
                    plan_dir=Path(tmp) / "nonexistent",
                    execution_date="2026-05-22",
                    output_dir=Path(tmp) / "out",
                )

    def test_no_run_id_generates_generic(self):
        """Without run_id, derive f'{strategy_id}_execute_{date}'."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / "plan"
            _fake_plan_dir(plan_dir, strategy_id="alpha_v2")
            output_dir = Path(tmp) / "out"

            with patch("qsys.ops.shadow_execution.fetch_market_snapshot") as mock_fetch:
                mock_fetch.side_effect = [
                    ({"600001": 10.0}, pd.DataFrame({"is_suspended": [False], "is_limit_up": [False], "is_limit_down": [False]}, index=["600001"])),
                    ({"600001": 10.5}, pd.DataFrame()),
                ]
                artifacts = execute_shadow_plan(
                    base_dir=tmp,
                    plan_dir=plan_dir,
                    execution_date="2026-05-22",
                    output_dir=output_dir,
                    debug_run=True,
                )

            self.assertEqual(artifacts.run_id, "alpha_v2_execute_2026-05-22")

    def test_with_run_id_uses_provided(self):
        """When run_id is provided, use as-is."""
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp) / "plan"
            _fake_plan_dir(plan_dir, strategy_id="alpha_v1")
            output_dir = Path(tmp) / "out"

            with patch("qsys.ops.shadow_execution.fetch_market_snapshot") as mock_fetch:
                mock_fetch.side_effect = [
                    ({"600001": 10.0}, pd.DataFrame({"is_suspended": [False], "is_limit_up": [False], "is_limit_down": [False]}, index=["600001"])),
                    ({"600001": 10.5}, pd.DataFrame()),
                ]
                artifacts = execute_shadow_plan(
                    base_dir=tmp,
                    plan_dir=plan_dir,
                    execution_date="2026-05-22",
                    output_dir=output_dir,
                    debug_run=True,
                    run_id="custom_run_2026-05-22",
                )

            self.assertEqual(artifacts.run_id, "custom_run_2026-05-22")


# ── Tests: commit_execution_artifacts ──────────────────────────────────────

class TestCommitExecutionArtifacts(unittest.TestCase):
    """commit_execution_artifacts — marker and artifact flow."""

    def test_missing_committing_exits(self):
        """Missing COMMITTING marker calls sys.exit(1)."""
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as cm:
                commit_execution_artifacts(
                    run_root=Path(tmp),
                    staging_dir=Path(tmp) / "staging",
                    db_path="/tmp/test.db",
                    trade_date="2026-05-22",
                    strategy_id="test",
                )
            self.assertEqual(cm.exception.code, 1)

    def test_debug_run_skips_ledger(self):
        """debug_run=True skips ledger write but commits artifacts."""
        from qsys.ops.commit_guard import committing_marker

        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "staging"
            staging_dir.mkdir(parents=True)
            (staging_dir / "execution_summary.json").write_text(json.dumps({"status": "success"}))
            # Create COMMITTING marker
            p = committing_marker(Path(tmp))
            p.parent.mkdir(parents=True)
            p.touch()

            commit_execution_artifacts(
                run_root=Path(tmp),
                staging_dir=staging_dir,
                db_path="/tmp/test.db",
                trade_date="2026-05-22",
                strategy_id="test",
                debug_run=True,
            )

            # COMMITTING should be renamed to COMMITTED
            from qsys.ops.commit_guard import committed_marker
            self.assertTrue(committed_marker(Path(tmp)).exists())

    def test_copies_staging_artifacts(self):
        """Staging artifacts are copied to execution/."""
        from qsys.ops.commit_guard import committing_marker

        with tempfile.TemporaryDirectory() as tmp:
            staging_dir = Path(tmp) / "staging"
            staging_dir.mkdir(parents=True)
            (staging_dir / "execution_summary.json").write_text(json.dumps({"status": "success"}))
            (staging_dir / "account_after.json").write_text(json.dumps({"cash": 1_000_000.0}))

            p = committing_marker(Path(tmp))
            p.parent.mkdir(parents=True)
            p.touch()

            commit_execution_artifacts(
                run_root=Path(tmp),
                staging_dir=staging_dir,
                db_path="/tmp/test.db",
                trade_date="2026-05-22",
                strategy_id="test",
                debug_run=True,
            )

            exec_dir = Path(tmp) / "execution"
            self.assertTrue((exec_dir / "execution_summary.json").exists())
            self.assertTrue((exec_dir / "account_after.json").exists())


# ── Tests: write_failed_execution_summary ───────────────────────────────────

class TestWriteFailedExecutionSummary(unittest.TestCase):
    """write_failed_execution_summary — output contract."""

    def test_writes_failed_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_failed_execution_summary(
                output_dir=Path(tmp),
                trade_date="2026-05-22",
                run_id="test_run",
                error="something broke",
                strategy_id="alpha_v2",
                strategy_version="1.0",
            )
            data = json.loads(path.read_text())
            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["error"], "something broke")
            self.assertEqual(data["strategy_id"], "alpha_v2")


if __name__ == "__main__":
    unittest.main()
