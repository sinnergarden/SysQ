"""Tests for shared notification formatting (notify_format.py).

Pure-function tests — no qlib required.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from qsys.ops.notify_format import (
    fmt_amount,
    format_postclose_message,
    format_preopen_message,
    now_str,
)


class TestHelpers(unittest.TestCase):
    """fmt_amount and now_str."""

    def test_now_str_returns_time_string(self):
        t = now_str()
        self.assertEqual(len(t.split(":")), 3)

    def test_fmt_amount_zero(self):
        self.assertEqual(fmt_amount(0), "¥0.00k")

    def test_fmt_amount_positive(self):
        self.assertEqual(fmt_amount(123456), "¥123.46k")

    def test_fmt_amount_negative(self):
        self.assertEqual(fmt_amount(-5000), "¥-5.00k")

    def test_fmt_amount_small(self):
        self.assertEqual(fmt_amount(99.5), "¥0.10k")


class TestFormatPreopenMessage(unittest.TestCase):
    """format_preopen_message output structure and content."""

    def _make_predictions(self) -> pd.DataFrame:
        return pd.DataFrame({
            "instrument": ["000001.SZ", "000002.SZ", "000003.SZ"],
            "score": [2.0, 1.0, -0.5],
        })

    def _get_stock_name(self, code: str) -> str:
        names = {"000001.SZ": "平安银行", "000002.SZ": "万科A", "000003.SZ": "未知"}
        return names.get(code, code)

    def test_minimum_message(self):
        """Only required params — no plan dir."""
        msg = format_preopen_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
            predictions_df=self._make_predictions(),
            plan_dir=None,
            rebalance_skipped=False,
            universe="csi300",
            prediction_count=3,
            rebalance_freq="weekly",
            get_stock_name=self._get_stock_name,
        )
        self.assertIn("Test Strat", msg)
        self.assertIn("2026-05-25", msg)
        self.assertIn("推荐股票", msg)
        self.assertIn("平安银行", msg)
        self.assertIn("000001.SZ", msg)

    def test_rebalance_skipped_message(self):
        msg = format_preopen_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
            predictions_df=self._make_predictions(),
            plan_dir=None,
            rebalance_skipped=True,
            universe="csi300",
            prediction_count=3,
            rebalance_freq="weekly",
            get_stock_name=self._get_stock_name,
        )
        self.assertIn("跳过", msg)

    def test_with_plan_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_dir = Path(tmp)
            pd.DataFrame({
                "instrument": ["000001.SZ", "000002.SZ"],
                "side": ["buy", "sell"],
                "diff_value": [10000.0, -5000.0],
                "requested_qty": [100, 50],
            }).to_csv(plan_dir / "order_intents.csv", index=False)

            msg = format_preopen_message(
                display_name="Test Strat",
                trade_date="2026-05-25",
                predictions_df=self._make_predictions(),
                plan_dir=plan_dir,
                rebalance_skipped=False,
                universe="csi300",
                prediction_count=3,
                rebalance_freq="weekly",
                get_stock_name=self._get_stock_name,
            )
            self.assertIn("计划买入", msg)
            self.assertIn("计划卖出", msg)
            self.assertIn("注:", msg)

    def test_identical_output_both_strategies(self):
        """V1 and V2 produce same format when given same params."""
        preds = self._make_predictions()
        msg_v1 = format_preopen_message(
            display_name="Alpha V1",
            trade_date="2026-05-25",
            predictions_df=preds,
            plan_dir=None,
            rebalance_skipped=False,
            universe="csi300",
            prediction_count=len(preds),
            rebalance_freq="weekly",
            get_stock_name=self._get_stock_name,
        )
        msg_v2 = format_preopen_message(
            display_name="Alpha V2 Smoke",
            trade_date="2026-05-25",
            predictions_df=preds,
            plan_dir=None,
            rebalance_skipped=False,
            universe="csi300",
            prediction_count=len(preds),
            rebalance_freq="weekly",
            get_stock_name=self._get_stock_name,
        )
        # Both messages should have the same structure sections
        for section in ["推荐股票", "策略:", "Universe:"]:
            self.assertIn(section, msg_v1)
            self.assertIn(section, msg_v2)
        # Display name differs
        self.assertIn("Alpha V1", msg_v1)
        self.assertIn("Alpha V2 Smoke", msg_v2)


class TestFormatPostcloseMessage(unittest.TestCase):
    """format_postclose_message output structure and content."""

    def test_minimum_message(self):
        msg = format_postclose_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
        )
        self.assertIn("Test Strat", msg)
        self.assertIn("Post-close", msg)
        # No execution summary when no artifacts
        self.assertNotIn("执行摘要", msg)

    def test_debug_run_indicator(self):
        msg = format_postclose_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
            debug_run=True,
        )
        self.assertIn("调试模式", msg)

    def test_execution_committed(self):
        msg = format_postclose_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
            execution_committed=True,
        )
        self.assertIn("执行状态: 已完成", msg)

    def test_with_artifacts(self):
        from types import SimpleNamespace

        artifacts = SimpleNamespace(
            turnover=50000.0, order_count=5, filled_count=4,
            rejected_count=1, cash_after=80000.0, total_value_after=180000.0,
        )
        msg = format_postclose_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
            artifacts=artifacts,
        )
        self.assertIn("执行摘要", msg)
        self.assertIn("¥50.00k", msg)  # turnover

    def test_with_mtm(self):
        mtm = {
            "cash": 80000.0, "market_value": 100000.0,
            "total_value": 180000.0, "initial_capital": 100000.0,
            "cumulative_pnl": 80000.0, "cumulative_pnl_pct": 80.0,
            "daily_pnl": 5000.0,
            "priced_count": 3, "total_positions": 3,
            "details": [
                ["000001.SZ", "平安银行", 100, 10.0, 12.0, 200.0],
                ["000002.SZ", "万科A", 200, 8.0, 9.0, 200.0],
                ["000003.SZ", "深发展", 300, 15.0, 14.0, -200.0],
                ["000004.SZ", "格力电器", 50, 40.0, 38.0, -100.0],
            ],
        }
        msg = format_postclose_message(
            display_name="Test Strat",
            trade_date="2026-05-25",
            mtm=mtm,
            get_stock_name=lambda c: {"000001.SZ": "平安银行"}.get(c, c),
        )
        self.assertIn("Mark-to-Market", msg)
        self.assertIn("累计 PnL", msg)
        self.assertIn("当日 PnL", msg)
        self.assertIn("Top 3", msg)
        self.assertIn("Bottom 3", msg)
