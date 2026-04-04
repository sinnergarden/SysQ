import unittest
from unittest.mock import patch

from qsys.live.manager import LiveManager
from qsys.strategy.engine import DEFAULT_TOP_K, StrategyEngine
from qsys.workflow.preopen import run_preopen_plan


class TestPreopenWorkflowAdapter(unittest.TestCase):
    def test_strategy_defaults_follow_roadmap_top_k(self):
        self.assertEqual(DEFAULT_TOP_K, 5)
        self.assertEqual(StrategyEngine().top_k, 5)

    def test_live_manager_default_strategy_uses_roadmap_top_k(self):
        manager = LiveManager(model_path="demo")
        self.assertEqual(manager.strategy.top_k, 5)

    @patch("scripts.run_daily_trading.run_preopen_workflow")
    def test_adapter_returns_ready_contract(self, mock_workflow):
        mock_workflow.return_value = {
            "signal_date": "2026-04-03",
            "execution_date": "2026-04-06",
            "data_status": {"health_ok": True},
            "model_info": {"model_name": "qlib_lgbm_prod"},
            "shadow_plan_summary": {"status": "ready", "trades": 5, "signal_date": "2026-04-03", "execution_date": "2026-04-06"},
            "real_plan_summary": {"status": "ready", "trades": 4, "signal_date": "2026-04-03", "execution_date": "2026-04-06"},
            "signal_basket_summary": {"status": "ready", "trades": 5},
            "signal_quality_summary": {"status": "success"},
            "artifacts": {"report": "/tmp/report.json"},
            "blockers": [],
            "blocked_symbols": [],
            "cash_utilization": {"shadow": {"planned_ratio": 0.95}},
            "assumptions": {"top_k": 5, "min_trade": 5000},
            "next_action": "Review blocked symbols and convert executable plans into order intents",
        }

        result = run_preopen_plan(date="2026-04-03")
        self.assertEqual(result["task_name"], "preopen-plan")
        self.assertEqual(result["decision"], "ready")
        self.assertEqual(result["summary"]["signal_date"], "2026-04-03")
        self.assertEqual(result["summary"]["executable_portfolio"]["shadow"]["trades"], 5)
        self.assertEqual(result["summary"]["assumptions"]["top_k"], 5)
        self.assertFalse(result["risk_flags"])

    @patch("scripts.run_daily_trading.run_preopen_workflow")
    def test_adapter_marks_blocked_result(self, mock_workflow):
        mock_workflow.return_value = {
            "signal_date": "2026-04-03",
            "execution_date": "2026-04-06",
            "data_status": {"health_ok": False},
            "model_info": {"model_name": "qlib_lgbm_prod"},
            "shadow_plan_summary": {"status": "skipped", "signal_date": "2026-04-03", "execution_date": "2026-04-06"},
            "real_plan_summary": {"status": "skipped", "signal_date": "2026-04-03", "execution_date": "2026-04-06"},
            "signal_quality_summary": {},
            "artifacts": {},
            "blockers": ["Data health check failed"],
            "blocked_symbols": [],
            "cash_utilization": {},
            "assumptions": {"top_k": 5},
            "next_action": "Refresh data until readiness passes before pre-open",
        }

        result = run_preopen_plan(date="2026-04-03")
        self.assertEqual(result["decision"], "blocked")
        self.assertEqual(result["blocker"], "Data health check failed")
        self.assertIn("data_not_ready", result["risk_flags"])

    @patch("scripts.run_daily_trading.run_preopen_workflow")
    def test_cli_default_top_k_is_5(self, mock_workflow):
        import scripts.run_daily_trading as run_daily_trading

        mock_workflow.return_value = {
            "signal_date": "2026-04-03",
            "execution_date": "2026-04-06",
            "data_status": {},
            "model_info": {},
            "shadow_plan_summary": {"status": "skipped", "signal_date": "2026-04-03", "execution_date": "2026-04-06"},
            "real_plan_summary": {"status": "skipped", "signal_date": "2026-04-03", "execution_date": "2026-04-06"},
            "signal_quality_summary": {},
            "signal_basket_summary": {},
            "artifacts": {},
            "blockers": [],
            "blocked_symbols": [],
            "cash_utilization": {},
            "assumptions": {"top_k": 5},
            "next_action": None,
        }

        with patch("sys.argv", ["run_daily_trading.py", "--date", "2026-04-03", "--no_report"]):
            run_daily_trading.main()

        self.assertEqual(mock_workflow.call_args.kwargs["top_k"], 5)


if __name__ == "__main__":
    unittest.main()
