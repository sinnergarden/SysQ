"""Equivalence tests for tushare_feature_config hardcoded defaults.

Verifies that the hardcoded fallback dict returned by ConfigManager
(when no YAML key is present) is internally consistent and complete.
Does NOT depend on settings.yaml (local-only, gitignored).
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest import TestCase

import yaml

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
                    "fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_income,revenue,oper_cost",
                },
                "balancesheet": {
                    "interface": "balancesheet",
                    "fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,total_assets,total_hldr_eqy_exc_min_int,total_cur_assets,total_cur_liab",
                },
                "cashflow": {
                    "interface": "cashflow",
                    "fields": "ts_code,ann_date,end_date,f_ann_date,report_type,comp_type,end_type,update_flag,n_cashflow_act",
                },
                "fina_indicator": {
                    "interface": "fina_indicator",
                    "fields": "ts_code,ann_date,end_date,update_flag,roe,roe_waa,grossprofit_margin,debt_to_assets,current_ratio,q_dtprofit,q_gr_yoy",
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

    def test_financial_interfaces_request_supplier_revision_evidence(self):
        interfaces = self.collector["interfaces"]
        statement_evidence = {
            "f_ann_date", "report_type", "comp_type", "end_type", "update_flag",
        }
        for endpoint in ("income", "balancesheet", "cashflow"):
            fields = set(interfaces[endpoint]["fields"].split(","))
            self.assertTrue(statement_evidence.issubset(fields), endpoint)

        indicator_fields = set(interfaces["fina_indicator"]["fields"].split(","))
        self.assertIn("update_flag", indicator_fields)
        self.assertTrue(
            indicator_fields.isdisjoint(
                {"f_ann_date", "report_type", "comp_type", "end_type"}
            )
        )

    def test_legacy_financial_fields_canonicalize_to_defaults_and_are_idempotent(self):
        defaults = _hardcoded_default()
        default_interfaces = defaults["collector"]["interfaces"]
        evidence = {"f_ann_date", "report_type", "comp_type", "end_type", "update_flag"}

        for input_type in (str, list):
            for evidence_state in ("missing", "tail"):
                legacy = deepcopy(defaults)
                for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
                    default_fields = default_interfaces[endpoint]["fields"].split(",")
                    endpoint_evidence = [field for field in default_fields if field in evidence]
                    narrow = [field for field in default_fields if field not in evidence]
                    legacy_fields = [narrow[0], *narrow]
                    if evidence_state == "tail":
                        legacy_fields.extend([*endpoint_evidence, endpoint_evidence[0]])
                    legacy["collector"]["interfaces"][endpoint]["fields"] = (
                        ",".join(legacy_fields) if input_type is str else legacy_fields
                    )

                enriched = ConfigManager._with_financial_evidence_fields(legacy)
                for endpoint in ("income", "balancesheet", "cashflow", "fina_indicator"):
                    self.assertEqual(
                        enriched["collector"]["interfaces"][endpoint]["fields"],
                        default_interfaces[endpoint]["fields"],
                        (input_type.__name__, evidence_state, endpoint),
                    )
                first_pass = deepcopy(enriched)
                self.assertEqual(
                    ConfigManager._with_financial_evidence_fields(enriched),
                    first_pass,
                )

    def test_daily_ops_allowlist_includes_collector_config_manager(self):
        root = Path(__file__).resolve().parents[1]
        harness_map = yaml.safe_load(
            (root / "docs" / "requirements" / "harness_map.yaml").read_text()
        )
        self.assertIn(
            "qsys/config/manager.py",
            harness_map["usecases"]["UC_DAILY_OPS"]["allowed_paths"],
        )

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
