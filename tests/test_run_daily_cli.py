"""Tests for scripts/run_daily.py — argument parsing and dispatch."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.run_daily import build_parser, parse_args, run_daily_main


class TestArgParsing(unittest.TestCase):
    """CLI argument parsing semantics."""

    def test_basic_preopen(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--mode", "preopen",
            "--trade-date", "2026-05-22",
        ])
        self.assertEqual(args.strategy, "alpha_v1")
        self.assertEqual(args.mode, "preopen")
        self.assertEqual(args.trade_date, "2026-05-22")
        self.assertFalse(args.debug_run)
        self.assertFalse(args.no_notify)
        self.assertFalse(args.notify_only)

    def test_basic_postclose(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--mode", "postclose",
            "--trade-date", "2026-05-22",
        ])
        self.assertEqual(args.mode, "postclose")

    def test_basic_infer(self):
        args = parse_args([
            "--strategy", "financial_rc", "--mode", "infer",
            "--signal-date", "2026-08-07", "--top-k", "200",
        ])
        self.assertEqual(args.mode, "infer")
        self.assertEqual(args.signal_date, "2026-08-07")
        self.assertEqual(args.top_k, 200)
        self.assertIsNone(args.trade_date)

    def test_basic_top10(self):
        args = parse_args([
            "--strategy", "s180_top10", "--mode", "top10",
            "--signal-date", "auto",
        ])
        self.assertEqual(args.mode, "top10")
        self.assertEqual(args.signal_date, "auto")
        self.assertEqual(args.top_k, 10)
        self.assertIsNone(args.trade_date)

    def test_top10_force_retrain_requires_reason(self):
        with self.assertRaises(SystemExit):
            parse_args([
                "--strategy", "s180_top10", "--mode", "top10",
                "--force-retrain",
            ])

    def test_force_retrain_rejected_outside_top10(self):
        with self.assertRaises(SystemExit):
            parse_args([
                "--strategy", "financial_rc", "--mode", "infer",
                "--force-retrain", "--reason", "test",
            ])

    def test_infer_trade_date_is_compatibility_alias(self):
        args = parse_args([
            "--strategy", "financial_rc", "--mode", "infer",
            "--trade-date", "2026-08-07",
        ])
        self.assertEqual(args.signal_date, "2026-08-07")

    def test_infer_conflicting_dates_fail(self):
        with self.assertRaises(SystemExit):
            parse_args([
                "--strategy", "financial_rc", "--mode", "infer",
                "--trade-date", "2026-08-06",
                "--signal-date", "2026-08-07",
            ])

    def test_force_rerun_requires_reason(self):
        with self.assertRaises(SystemExit):
            parse_args([
                "--strategy", "alpha_v1", "--force-rerun",
                "--trade-date", "2026-05-22",
            ])

    def test_force_rerun_with_reason_ok(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--force-rerun",
            "--reason", "验证测试", "--trade-date", "2026-05-22",
        ])
        self.assertTrue(args.force_rerun)
        self.assertEqual(args.reason, "验证测试")

    def test_trade_date_defaults_for_preopen(self):
        args = parse_args(["--strategy", "alpha_v1", "--mode", "preopen"])
        self.assertIsNotNone(args.trade_date)

    def test_trade_date_defaults_for_postclose(self):
        args = parse_args(["--strategy", "alpha_v1", "--mode", "postclose"])
        self.assertIsNotNone(args.trade_date)

    def test_notify_only(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--notify-only",
            "--trade-date", "2026-05-22",
        ])
        self.assertTrue(args.notify_only)

    def test_notify_only_without_trade_date_defaults(self):
        args = parse_args(["--strategy", "alpha_v1", "--notify-only"])
        self.assertIsNotNone(args.trade_date)

    def test_mode_default_preopen(self):
        args = parse_args(["--strategy", "alpha_v1", "--trade-date", "2026-05-22"])
        self.assertEqual(args.mode, "preopen")

    def test_debug_run(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--mode", "preopen",
            "--trade-date", "2026-05-22", "--debug-run",
        ])
        self.assertTrue(args.debug_run)

    def test_no_notify(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--mode", "preopen",
            "--trade-date", "2026-05-22", "--no-notify",
        ])
        self.assertTrue(args.no_notify)

    def test_output_dir(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--mode", "preopen",
            "--trade-date", "2026-05-22",
            "--debug-run", "--output-dir", "/tmp/test_out",
        ])
        self.assertEqual(args.output_dir, "/tmp/test_out")

    def test_output_dir_without_debug_is_rejected(self):
        with self.assertRaises(SystemExit):
            parse_args([
                "--strategy", "alpha_v1", "--mode", "preopen",
                "--trade-date", "2026-05-22", "--output-dir", "/tmp/test_out",
            ])

    def test_strategy_is_required(self):
        with self.assertRaises(SystemExit):
            parse_args(["--mode", "preopen", "--trade-date", "2026-05-22"])


class TestDispatch(unittest.TestCase):
    """Verify run_daily_main dispatches to the correct runner method."""

    def setUp(self):
        self._patches = [
            patch(
                "scripts.run_daily.resolve_shadow_promotion",
                return_value={"strategy_id": "alpha_v1", "candidate_id": "cand_test"},
            ),
            patch("scripts.run_daily.validate_daily_stage_manifest"),
            patch("scripts.run_daily.snapshot_promotion_pointer"),
            patch("scripts.run_daily.read_active_attempt", return_value=None),
            patch("scripts.run_daily.resolve_promotion_snapshot", return_value={
                "candidate_id": "cand_test",
                "promotion_pointer_path": "data/research/promotions/shadow.yaml",
            }),
            patch("scripts.run_daily.write_active_attempt"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.run_candidate_inference")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_infer_is_artifact_only(
        self, mock_load_cfg, mock_infer, mock_create, mock_runner_cls,
    ):
        mock_infer.return_value.artifact_path = "/tmp/candidate_run.json"
        mock_infer.return_value.payload = {
            "signal_date": "2026-08-07",
            "decision_date": "2026-08-10",
            "execution_date": "2026-08-11",
            "candidate_count": 20,
            "candidate_hash": "a" * 64,
        }

        run_daily_main([
            "--strategy", "financial_rc", "--mode", "infer",
            "--signal-date", "2026-08-07", "--top-k", "20",
        ])

        mock_infer.assert_called_once()
        mock_create.assert_not_called()
        mock_runner_cls.assert_not_called()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.run_top10_signal")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_top10_is_artifact_only(
        self, mock_load_cfg, mock_top10, mock_create, mock_runner_cls,
    ):
        mock_top10.return_value.artifact_path = "/tmp/top10_run.json"
        mock_top10.return_value.reused = False
        mock_top10.return_value.payload = {
            "data_date": "2026-08-20",
            "decision_date": "2026-08-21",
            "execution_date": "2026-08-24",
            "model": {"bundle_hash": "b" * 64},
            "quality_gate": {"status": "pass"},
        }

        run_daily_main([
            "--strategy", "s180_top10", "--mode", "top10",
            "--signal-date", "auto",
        ])

        mock_top10.assert_called_once()
        mock_create.assert_not_called()
        mock_runner_cls.assert_not_called()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    @patch("scripts.run_daily.resolve_run_root")
    def test_dispatch_preopen(
        self, mock_resolve_root, mock_load_cfg,
        mock_create, mock_runner_cls,
    ):
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v1", "--mode", "preopen",
            "--trade-date", "2026-05-22", "--no-notify",
        ])

        mock_runner.run_preopen.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    @patch("scripts.run_daily.resolve_run_root")
    @patch("scripts.run_daily.read_active_attempt", return_value={
        "attempt_id": "attempt-1", "attempt_seq": 1,
    })
    def test_dispatch_postclose(
        self, mock_active, mock_resolve_root, mock_load_cfg,
        mock_create, mock_runner_cls,
    ):
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v1", "--mode", "postclose",
            "--trade-date", "2026-05-22", "--no-notify",
        ])

        mock_runner.run_postclose.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    @patch("scripts.run_daily.resolve_run_root")
    def test_dispatch_notify_only(
        self, mock_resolve_root, mock_load_cfg,
        mock_create, mock_runner_cls,
    ):
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v1", "--notify-only",
            "--trade-date", "2026-05-22",
        ])

        mock_runner.run_notify_only.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_train(
        self, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Train mode dispatches to DailyRunner.run_train."""
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        # Train mode does not need trade-date
        run_daily_main(["--strategy", "alpha_v1", "--mode", "train"])

        mock_runner.run_train.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_train_with_trade_date(
        self, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Train mode accepts optional --trade-date."""
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v1", "--mode", "train",
            "--trade-date", "2026-05-22",
        ])

        mock_runner.run_train.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_train_with_debug(
        self, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Train mode with --debug-run creates a debug run_root."""
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v1", "--mode", "train",
            "--debug-run", "--no-notify",
        ])

        mock_runner.run_train.assert_called_once()
        # Verify the ctx has debug_run=True
        ctx = mock_runner.run_train.call_args[0][0]
        self.assertTrue(ctx.debug_run)

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_train_output_dir(
        self, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Train mode with --output-dir uses given path."""
        mock_strategy = mock_create.return_value
        mock_strategy.strategy_id = "alpha_v1"
        mock_strategy.account_id = "shadow_alpha_v1"
        mock_strategy.display_name = "Alpha V1"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v1", "--mode", "train",
            "--debug-run", "--output-dir", "/tmp/my_train_run",
        ])

        ctx = mock_runner.run_train.call_args[0][0]
        self.assertEqual(str(ctx.run_root), "/tmp/my_train_run")

    @patch("scripts.run_daily.load_strategy_config")
    def test_train_end_date_injected_into_config(self, mock_load_cfg):
        """--train-end-date is injected into config before strategy creation."""
        mock_load_cfg.return_value = {}
        with patch("scripts.run_daily.create_strategy") as mock_create:
            mock_strat = mock_create.return_value
            mock_strat.strategy_id = "alpha_v1"
            mock_strat.account_id = "shadow_alpha_v1"
            mock_strat.display_name = "Alpha V1"

            with patch("scripts.run_daily.DailyRunner") as mock_runner_cls:
                run_daily_main([
                    "--strategy", "alpha_v1", "--mode", "train",
                    "--train-end-date", "2026-05-15",
                ])

        # Config should have training.end_date injected
        passed_config = mock_create.call_args[0][1]
        assert passed_config["training"]["end_date"] == "2026-05-15"

    def test_financial_rc_train_uses_dedicated_trainer_without_promotion(self):
        """Research bundle training must not require a shadow promotion pointer."""
        with patch("scripts.run_daily.load_strategy_config", return_value={}), \
             patch("scripts.run_daily.has_model_trainer", return_value=True), \
             patch("scripts.run_daily.create_model_trainer") as create_trainer, \
             patch("scripts.run_daily.create_strategy") as create_strategy, \
             patch("scripts.run_daily.resolve_shadow_promotion") as resolve_promotion, \
             patch("scripts.run_daily.DailyRunner") as runner_cls:
            trainer = create_trainer.return_value
            trainer.strategy_id = "financial_rc"
            trainer.account_id = "research_financial_rc"
            trainer.display_name = "Financial RC"

            run_daily_main([
                "--strategy", "financial_rc", "--mode", "train",
                "--trade-date", "2026-08-07", "--no-notify",
            ])

        create_strategy.assert_not_called()
        resolve_promotion.assert_not_called()
        runner_cls.return_value.run_train.assert_called_once()


class TestAlphaV2Dispatch(unittest.TestCase):
    """Verify alpha_v2 dispatches through run_daily_main."""

    def setUp(self):
        self._patches = [
            patch(
                "scripts.run_daily.resolve_shadow_promotion",
                return_value={"strategy_id": "alpha_v2", "candidate_id": "cand_test"},
            ),
            patch("scripts.run_daily.validate_daily_stage_manifest"),
            patch("scripts.run_daily.snapshot_promotion_pointer"),
            patch("scripts.run_daily.read_active_attempt", return_value=None),
            patch("scripts.run_daily.resolve_promotion_snapshot", return_value={
                "candidate_id": "cand_test",
                "promotion_pointer_path": "data/research/promotions/shadow.yaml",
            }),
            patch("scripts.run_daily.write_active_attempt"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    def test_dispatch_alpha_v2_train(
        self, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Alpha v2 train mode dispatches to DailyRunner.run_train."""
        mock_strat = mock_create.return_value
        mock_strat.strategy_id = "alpha_v2"
        mock_strat.account_id = "shadow_alpha_v2"
        mock_strat.display_name = "Alpha V2 Smoke"
        mock_runner = mock_runner_cls.return_value

        run_daily_main(["--strategy", "alpha_v2", "--mode", "train"])

        mock_runner.run_train.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    @patch("scripts.run_daily.resolve_run_root")
    def test_dispatch_alpha_v2_preopen(
        self, mock_resolve_root, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Alpha v2 preopen mode dispatches to DailyRunner.run_preopen."""
        mock_strat = mock_create.return_value
        mock_strat.strategy_id = "alpha_v2"
        mock_strat.account_id = "shadow_alpha_v2"
        mock_strat.display_name = "Alpha V2 Smoke"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v2", "--mode", "preopen",
            "--trade-date", "2026-05-22", "--no-notify",
        ])

        mock_runner.run_preopen.assert_called_once()

    @patch("scripts.run_daily.DailyRunner")
    @patch("scripts.run_daily.create_strategy")
    @patch("scripts.run_daily.load_strategy_config")
    @patch("scripts.run_daily.resolve_run_root")
    @patch("scripts.run_daily.read_active_attempt", return_value={
        "attempt_id": "attempt-1", "attempt_seq": 1,
    })
    def test_dispatch_alpha_v2_postclose(
        self, mock_active, mock_resolve_root, mock_load_cfg, mock_create, mock_runner_cls,
    ):
        """Alpha v2 postclose mode dispatches to DailyRunner.run_postclose."""
        mock_strat = mock_create.return_value
        mock_strat.strategy_id = "alpha_v2"
        mock_strat.account_id = "shadow_alpha_v2"
        mock_strat.display_name = "Alpha V2 Smoke"
        mock_runner = mock_runner_cls.return_value

        run_daily_main([
            "--strategy", "alpha_v2", "--mode", "postclose",
            "--trade-date", "2026-05-22", "--no-notify",
        ])

        mock_runner.run_postclose.assert_called_once()


if __name__ == "__main__":
    unittest.main()
