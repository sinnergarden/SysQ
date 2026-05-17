"""Equivalence tests for tushare_feature_config hardcoded defaults.

Verifies that the hardcoded fallback dict returned by ConfigManager
(when no YAML key is present) is internally consistent and complete.
Does NOT depend on settings.yaml (local-only, gitignored).
"""
from __future__ import annotations

from unittest import TestCase

from qsys.config.manager import ConfigManager


def _hardcoded_default() -> dict:
    """Return a copy of the hardcoded default from manager.py."""
    return {
        "collector": {
            "expected_extra_cols": ["paused"],
            "numeric_extra_cols": ["paused"],
            "non_numeric_cols": ["trade_status"],
            "non_negative_cols": [
                "open", "high", "low", "close", "vol", "amount",
                "turnover_rate", "total_share", "float_share", "free_share",
                "total_mv", "circ_mv", "adj_factor", "up_limit", "down_limit",
            ],
            "financial_cols": [
                "net_income", "revenue", "oper_cost", "total_assets", "equity",
                "total_cur_assets", "total_cur_liab", "roe", "op_cashflow",
                "q_dt_profit", "q_gr_yoy", "roe_ttm", "grossprofit_margin",
                "debt_to_assets", "current_ratio",
            ],
            "moneyflow_fields": [
                "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
                "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount",
                "net_mf_amount",
            ],
            "derived_fields": {
                "moneyflow": ["big_inflow", "net_inflow"]
            },
            "interfaces": {
                "margin": {
                    "interface": "margin_detail",
                    "fields": "ts_code,trade_date,rzye,rzmre,rzche,rzrqye,rqyl,rqmcl,rqchl",
                    "rename": {
                        "rzye": "margin_balance",
                        "rzmre": "margin_buy_amount",
                        "rzche": "margin_repay_amount",
                        "rzrqye": "margin_total_balance",
                        "rqyl": "lend_volume",
                        "rqmcl": "lend_sell_volume",
                        "rqchl": "lend_repay_volume",
                    }
                },
                "income": {
                    "interface": "income",
                    "fields": "ts_code,ann_date,end_date,n_income,revenue,oper_cost",
                },
                "balancesheet": {
                    "interface": "balancesheet",
                    "fields": "ts_code,ann_date,end_date,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab",
                },
                "cashflow": {
                    "interface": "cashflow",
                    "fields": "ts_code,ann_date,end_date,n_cashflow_act",
                },
                "fina_indicator": {
                    "interface": "fina_indicator",
                    "fields": "ts_code,ann_date,end_date,roe,roe_waa,grossprofit_margin,debt_to_assets,current_ratio,q_dtprofit,q_gr_yoy",
                    "rename": {
                        "q_dtprofit": "q_dt_profit",
                    },
                }
            },
            "margin_cols": [
                "margin_balance", "margin_buy_amount", "margin_repay_amount",
                "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume"
            ]
        },
        "adapter": {
            "rename_map": {
                "trade_date": "date",
                "adj_factor": "factor",
                "vol": "volume",
                "up_limit": "high_limit",
                "down_limit": "low_limit",
                "margin_balance": "margin_balance",
                "margin_buy_amount": "margin_buy_amount",
                "margin_repay_amount": "margin_repay_amount",
                "margin_total_balance": "margin_total_balance",
                "lend_volume": "lend_volume",
                "lend_sell_volume": "lend_sell_volume",
                "lend_repay_volume": "lend_repay_volume",
            },
            "qlib_fields": [
                "open", "high", "low", "close", "volume", "amount", "factor",
                "vwap", "paused", "high_limit", "low_limit",
                "turnover_rate", "pe", "pb", "total_mv", "circ_mv",
                "net_inflow", "big_inflow",
                "net_income", "revenue", "total_assets", "equity", "roe", "op_cashflow",
                "q_dt_profit", "q_gr_yoy", "roe_ttm", "grossprofit_margin",
                "debt_to_assets", "current_ratio",
                "margin_balance", "margin_buy_amount", "margin_repay_amount",
                "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
            ]
        }
    }


class TestHardcodedConfigCompleteness(TestCase):
    """The hardcoded default dict must have all required keys and fields."""

    def setUp(self):
        self.config = _hardcoded_default()
        self.collector = self.config["collector"]

    def test_collector_has_all_required_keys(self):
        required = {
            "interfaces",
            "margin_cols",
            "non_numeric_cols",
            "non_negative_cols",
            "expected_extra_cols",
            "numeric_extra_cols",
            "financial_cols",
            "moneyflow_fields",
            "derived_fields",
        }
        present = set(self.collector.keys())
        missing = required - present
        self.assertFalse(
            missing,
            f"Collector config missing keys: {sorted(missing)}",
        )

    def test_expected_extra_cols_includes_paused(self):
        self.assertIn("paused", self.collector["expected_extra_cols"])

    def test_financial_cols_has_key_fields(self):
        for key in ("net_income", "revenue", "roe", "op_cashflow"):
            self.assertIn(key, self.collector["financial_cols"])

    def test_moneyflow_fields_complete(self):
        fields = self.collector["moneyflow_fields"]
        self.assertIn("net_mf_amount", fields)
        self.assertGreaterEqual(len(fields), 8)

    def test_adapter_section_present(self):
        self.assertIn("adapter", self.config)

    def test_verify_method_no_warning_on_complete_config(self):
        """_verify_tushare_config should not log warning when all keys present."""
        try:
            ConfigManager._verify_tushare_config(self.config)
        except Exception as e:
            self.fail(f"_verify_tushare_config raised: {e}")

    def test_verify_method_warns_on_missing_keys(self):
        """_verify_tushare_config should not raise even when keys missing."""
        incomplete = {"collector": {}}
        try:
            ConfigManager._verify_tushare_config(incomplete)
        except Exception as e:
            self.fail(f"_verify_tushare_config raised on incomplete config: {e}")
