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

    def test_trade_date_required_for_preopen(self):
        with self.assertRaises(SystemExit):
            parse_args(["--strategy", "alpha_v1", "--mode", "preopen"])

    def test_trade_date_required_for_postclose(self):
        with self.assertRaises(SystemExit):
            parse_args(["--strategy", "alpha_v1", "--mode", "postclose"])

    def test_notify_only(self):
        args = parse_args([
            "--strategy", "alpha_v1", "--notify-only",
            "--trade-date", "2026-05-22",
        ])
        self.assertTrue(args.notify_only)

    def test_notify_only_without_trade_date_fails(self):
        with self.assertRaises(SystemExit):
            parse_args(["--strategy", "alpha_v1", "--notify-only"])

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
            "--output-dir", "/tmp/test_out",
        ])
        self.assertEqual(args.output_dir, "/tmp/test_out")

    def test_strategy_is_required(self):
        with self.assertRaises(SystemExit):
            parse_args(["--mode", "preopen", "--trade-date", "2026-05-22"])


class TestDispatch(unittest.TestCase):
    """Verify run_daily_main dispatches to the correct runner method."""

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
    def test_dispatch_postclose(
        self, mock_resolve_root, mock_load_cfg,
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
            "--output-dir", "/tmp/my_train_run",
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


if __name__ == "__main__":
    unittest.main()
