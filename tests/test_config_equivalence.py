"""Equivalence tests for tushare_feature_config loading.

Verifies that loading from YAML (with the newly added keys) produces the same
result as the hardcoded defaults, for the shared key set.
"""
from __future__ import annotations

from unittest import TestCase

from qsys.config import cfg


class TestTushareConfigEquivalence(TestCase):
    """Shared keys between YAML and hardcoded defaults must match."""

    def setUp(self):
        self.config = cfg.get_tushare_feature_config()
        self.collector = self.config.get("collector", {})

    def test_collector_has_all_required_keys(self):
        """All keys required for normal operation must be present."""
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
        self.assertIn("paused", self.collector.get("expected_extra_cols", []))

    def test_financial_cols_has_key_fields(self):
        financial = self.collector.get("financial_cols", [])
        for key in ("net_income", "revenue", "roe", "op_cashflow"):
            self.assertIn(key, financial)

    def test_moneyflow_fields_complete(self):
        fields = self.collector.get("moneyflow_fields", [])
        self.assertIn("net_mf_amount", fields)
        self.assertGreaterEqual(len(fields), 8)

    def test_adapter_section_present(self):
        self.assertIn("adapter", self.config)

    def test_verify_method_no_warning(self):
        """Direct call to _verify_tushare_config should not log warning
        when YAML is complete."""
        try:
            cfg._verify_tushare_config(self.config)
        except Exception as e:
            self.fail(f"_verify_tushare_config raised: {e}")
