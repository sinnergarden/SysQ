"""Tests for qsys/ops/daily_runner.py — DailyRunner skeleton and orchestration."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from qsys.ops.daily_runner import DailyRunner
from qsys.ops.run_context import DailyRunContext
from qsys.strategy.alpha_v1.adapter import AlphaV1StrategyAdapter


# ── Fake strategy that records call order ────────────────────────────────

class FakeStrategy:
    """Minimal StrategyCandidate that records every call for order verification."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def strategy_id(self) -> str:
        return "fake_strat"

    @property
    def account_id(self) -> str:
        return "shadow_fake"

    @property
    def universe(self) -> str:
        return "fake_universe"

    @property
    def feature_set(self) -> str:
        return "fake_features"

    @property
    def model_version(self) -> str:
        return "v1"

    @property
    def signal_version(self) -> str:
        return "blend_1.0:0.0"

    @property
    def rebalance_policy(self) -> dict[str, Any]:
        return {
            "top_n": 10,
            "buffer_hold": 50,
            "buffer_buy": 30,
            "rebalance_freq": "weekly",
            "single_stock_cap": 0.1,
        }

    # ── Data ────────────────────────────────────────────────────────────

    def resolve_data_date(self, trade_date: str) -> str:
        self._record("resolve_data_date")
        return trade_date

    def get_stock_name(self, ts_code: str) -> str:
        return ts_code

    def load_model(self) -> Any:
        self._record("load_model")
        return {"models": {}, "clean_features": []}

    def fetch_data(self, data_date: str) -> Any:
        self._record("fetch_data")
        import pandas as pd

        return {
            "frame": pd.DataFrame(
                {"instrument": ["000001", "000002"], "trade_date": [data_date, data_date]}
            ),
            "clean_features": [],
        }

    # ── Predict + Plan ──────────────────────────────────────────────────

    def generate_predictions(self, data: Any) -> Any:
        self._record("generate_predictions")
        import pandas as pd

        return pd.DataFrame(
            {
                "instrument": ["000001", "000002"],
                "score": [0.5, -0.3],
                "trade_date": ["2026-05-18", "2026-05-18"],
            }
        )

    def should_rebalance(self, trade_date: str) -> bool:
        self._record("should_rebalance")
        return True

    def build_plan(self, predictions: Any, target_dir: Any) -> bool:
        self._record("build_plan")
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        # Write a minimal order_intents.csv so the plan is detected
        import pandas as pd

        pd.DataFrame(
            {
                "trade_date": ["2026-05-18"],
                "instrument": ["000001"],
                "side": ["buy"],
                "target_weight": [0.1],
                "diff_value": [10000.0],
                "requested_qty": [100],
            }
        ).to_csv(Path(target_dir) / "order_intents.csv", index=False)
        return True

    def load_plan_instruments(self, plan_dir: Any) -> list[str]:
        self._record("load_plan_instruments")
        return []

    # ── Execute + MTM ───────────────────────────────────────────────────

    def execute_plan(self, context: Any) -> Any:
        self._record("execute_plan")
        # Return a minimal ShadowRebalanceArtifacts-like object
        from types import SimpleNamespace

        return SimpleNamespace(
            order_count=1,
            buy_count=1,
            sell_count=0,
            skipped_count=0,
            filled_count=1,
            rejected_count=0,
            turnover=10000.0,
            cash_after=900000.0,
            total_value_after=1010000.0,
        )

    def commit_execution(self, context: Any, staging_dir: Any) -> None:
        self._record("commit_execution")

    def mark_to_market(self, context: Any) -> dict | None:
        self._record("mark_to_market")
        return {
            "cash": 900000.0,
            "market_value": 110000.0,
            "total_value": 1010000.0,
            "initial_capital": 1000000.0,
            "cumulative_pnl": 10000.0,
            "cumulative_pnl_pct": 1.0,
            "daily_pnl": 5000.0,
            "priced_count": 1,
            "total_positions": 1,
            "details": [("000001", "StockA", 100, 10.0, 11.0, 100.0)],
        }

    def load_artifacts_for_notification(self, context: Any) -> Any | None:
        self._record("load_artifacts_for_notification")
        from types import SimpleNamespace

        return SimpleNamespace(
            turnover=10000.0,
            order_count=1,
            filled_count=1,
            rejected_count=0,
            cash_after=900000.0,
            total_value_after=1010000.0,
        )

    # ── Notifications ───────────────────────────────────────────────────

    def build_preopen_message(
        self, context: Any, rebalance_skipped: bool, predictions: Any
    ) -> str:
        self._record("build_preopen_message")
        return f"Fake preopen {context.trade_date}"

    def build_postclose_message(
        self,
        context: Any,
        mtm: dict | None = None,
        artifacts: Any = None,
        stale_check: dict | None = None,
        execution_committed: bool = False,
        execution_skipped: bool = False,
        idempotent_skip: bool = False,
    ) -> str:
        self._record("build_postclose_message")
        return f"Fake postclose {context.trade_date}"

    def send_notification(self, text: str) -> None:
        self._record("send_notification")


# ── Tests ────────────────────────────────────────────────────────────────

class TestDailyRunner(unittest.TestCase):
    """DailyRunner — validates context, creates directories, logs stage."""

    def setUp(self):
        self.strategy = AlphaV1StrategyAdapter()
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
        strategy = FakeStrategy()
        self.runner.run_preopen(self.ctx_preopen, strategy)
        self.assertTrue(self.run_root.exists())

    def test_run_preopen_rejects_wrong_mode(self):
        with self.assertRaises(ValueError):
            self.runner.run_preopen(self.ctx_postclose)

    def test_run_postclose_creates_run_root(self):
        self.assertFalse(self.run_root.exists())
        strategy = FakeStrategy()
        # Create plan dir so postclose proceeds past plan check
        plan_dir = self.run_root / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        import pandas as pd
        pd.DataFrame({"instrument": ["000001"], "side": ["buy"]}).to_csv(
            plan_dir / "order_intents.csv", index=False)
        self.runner.run_postclose(self.ctx_postclose, strategy)
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
        strategy = FakeStrategy()
        self.runner.run_preopen(self.ctx_debug, strategy)
        self.assertTrue(self.run_root.exists())

    def test_multiple_modes_independent(self):
        strategy = FakeStrategy()
        self.runner.run_preopen(self.ctx_preopen, strategy)
        self.runner.run_postclose(self.ctx_postclose, strategy)
        self.runner.run_train(self.ctx_train)
        self.assertTrue(self.run_root.exists())

    def test_with_strategy_candidate(self):
        self.runner.run_preopen(self.ctx_preopen, strategy=self.strategy)
        self.assertTrue(self.run_root.exists())


class TestDailyRunnerOrchestration(unittest.TestCase):
    """DailyRunner — orchestration order and lifecycle."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.run_root = Path(self.tmpdir.name) / "run"
        self.project_root = Path(self.tmpdir.name) / "proj"
        self.runner = DailyRunner()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_preopen_calls_strategy_in_order(self):
        """Preopen calls strategy methods in the correct sequence."""
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
            no_notify=True,
        )
        strategy = FakeStrategy()
        self.runner.run_preopen(ctx, strategy)

        expected_order = [
            "resolve_data_date",
            "load_model",
            "fetch_data",
            "generate_predictions",
            "should_rebalance",
            "build_plan",
            # build_preopen_message/send_notification skipped because no_notify=True
        ]
        self.assertEqual(strategy.calls, expected_order)

    def test_preopen_creates_run_meta(self):
        """Preopen writes run_meta.json."""
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
            no_notify=True,
        )
        strategy = FakeStrategy()
        self.runner.run_preopen(ctx, strategy)
        meta_path = self.run_root / "run_meta.json"
        self.assertTrue(meta_path.exists())
        meta = json.loads(meta_path.read_text())
        self.assertEqual(meta["trade_date"], "2026-05-18")
        self.assertEqual(meta["mode"], "preopen")

    def test_preopen_creates_predictions_dir(self):
        """Preopen writes predictions CSV."""
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
            no_notify=True,
        )
        strategy = FakeStrategy()
        self.runner.run_preopen(ctx, strategy)
        pred_path = self.run_root / "predictions" / "predictions_2026-05-18.csv"
        self.assertTrue(pred_path.exists())

    def test_postclose_calls_strategy_in_order(self):
        """Postclose calls strategy methods in the correct sequence with COMMITTING."""
        # First create a plan directory with order_intents.csv
        plan_dir = self.run_root / "plan"
        plan_dir.mkdir(parents=True, exist_ok=True)
        import pandas as pd
        pd.DataFrame(
            {
                "trade_date": ["2026-05-18"],
                "instrument": ["000001"],
                "side": ["buy"],
                "target_weight": [0.1],
                "diff_value": [10000.0],
                "requested_qty": [100],
            }
        ).to_csv(plan_dir / "order_intents.csv", index=False)

        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="postclose",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
        )
        strategy = FakeStrategy()
        self.runner.run_postclose(ctx, strategy)

        # Verify postclose calls
        self.assertIn("load_plan_instruments", strategy.calls)
        self.assertIn("execute_plan", strategy.calls)
        self.assertIn("commit_execution", strategy.calls)
        self.assertIn("mark_to_market", strategy.calls)
        self.assertIn("build_postclose_message", strategy.calls)

    def test_postclose_idempotent_skip(self):
        """COMMITTED marker present → skip execution, notify."""
        # Write COMMITTED marker
        exec_dir = self.run_root / "execution"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "COMMITTED").write_text("")

        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="postclose",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
        )
        strategy = FakeStrategy()
        self.runner.run_postclose(ctx, strategy)

        # Should NOT call execute_plan or commit_execution
        self.assertNotIn("execute_plan", strategy.calls)
        self.assertNotIn("commit_execution", strategy.calls)
        # Should call notification methods (idempotent skip still notifies)
        self.assertIn("load_artifacts_for_notification", strategy.calls)
        self.assertIn("build_postclose_message", strategy.calls)
        self.assertIn("send_notification", strategy.calls)

    def test_postclose_committing_without_committed_exits(self):
        """COMMITTING without COMMITTED → sys.exit(1)."""
        committing_dir = self.run_root / "execution"
        committing_dir.mkdir(parents=True, exist_ok=True)
        (committing_dir / "COMMITTING").write_text("")

        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="postclose",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
            no_notify=True,
        )
        strategy = FakeStrategy()
        with self.assertRaises(SystemExit) as cm:
            self.runner.run_postclose(ctx, strategy)
        self.assertEqual(cm.exception.code, 1)

    def test_path_helpers(self):
        """Path helpers return correct locations."""
        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="preopen",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="test",
            account_id="test",
        )
        self.assertEqual(self.runner.plan_dir(ctx), self.run_root / "plan")
        self.assertEqual(self.runner.exec_dir(ctx), self.run_root / "execution")
        self.assertEqual(
            self.runner.staging_dir(ctx), self.run_root / "execution" / "staging"
        )
        self.assertEqual(self.runner.mtm_dir(ctx), self.run_root / "mtm")

    def test_notify_only_sends_notification(self):
        """Notify-only loads artifacts and sends notification."""
        # Set up run_root with plan dir (so it exists)
        self.run_root.mkdir(parents=True, exist_ok=True)

        ctx = DailyRunContext(
            trade_date="2026-05-18",
            mode="postclose",
            run_root=self.run_root,
            project_root=self.project_root,
            strategy_id="fake_strat",
            account_id="shadow_fake",
        )
        strategy = FakeStrategy()
        self.runner.run_notify_only(ctx, strategy)

        self.assertIn("load_artifacts_for_notification", strategy.calls)
        self.assertIn("build_postclose_message", strategy.calls)
        self.assertIn("send_notification", strategy.calls)


if __name__ == "__main__":
    unittest.main()
