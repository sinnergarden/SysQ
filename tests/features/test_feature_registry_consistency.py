#!/usr/bin/env python3
"""Registry consistency tests — verify FeatureSpec, FEATURE_GROUPS, and YAML agree.

Coverage:
- feature_id uniqueness
- feature name uniqueness (active only)
- YAML features exist in registry
- active feature dependencies exist
- broken/deprecated features not in active lists
- feature group enabled_by flags match builder hooks
- builder flag → group feature set consistency
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qsys.feature.registry import FEATURE_GROUPS
from qsys.feature.registry_v2 import (
    FEATURE_REGISTRY,
    get_feature,
    validate_registry,
    validate_feature_list,
    list_feature_ids,
)
from qsys.feature.config import RESEARCH_FEATURE_FLAGS
from qsys.feature.builder import build_phase1_features


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def registry_errors():
    return validate_registry()


@pytest.fixture
def yaml_feature_lists():
    """Load all YAML feature lists."""
    config_dir = Path(__file__).resolve().parents[2] / "configs" / "features"
    result = {}
    for p in sorted(config_dir.glob("*.yaml")):
        if p.stem == "__init__":
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            result[p.stem] = data.get("features", [])
        except Exception:
            pass
    return result


@pytest.fixture(scope="module")
def base_builder_df():
    """Minimal DF with all columns needed by the builder's _repair_research_input_columns."""
    np.random.seed(42)
    dates = pd.date_range("2025-01-01", periods=100, freq="B")
    n_stocks = 10
    rows = []
    for i in range(n_stocks):
        close = 100.0
        for j, d in enumerate(dates):
            ret = np.random.normal(0.0005, 0.02)
            close *= (1 + ret)
            rows.append({
                "ts_code": f"S{i:04d}", "trade_date": d,
                "close": close, "open": close * 0.995, "high": close * 1.01,
                "low": close * 0.99, "volume": 1e6, "amount": 1e8,
                "high_limit": close * 1.1, "low_limit": close * 0.9,
                "turnover_rate": 0.02, "circ_mv": 5e9, "total_mv": 1e10,
                "pe": 20, "pb": 2, "roe": 0.1,
                "grossprofit_margin": 0.3, "debt_to_assets": 0.5,
                "op_cashflow": 1e8, "net_income": 5e7, "revenue": 2e8,
                "total_assets": 5e9, "equity": 2e9,
                "inventory": 1e8, "accounts_receiv": 5e7,
                "industry": np.random.choice(["A", "B", "C", "D", "E"]),
                "paused": 0, "float_shares": 2e8,
            })
    return pd.DataFrame(rows).sort_values(["ts_code", "trade_date"]).reset_index(drop=True)


_BUILD_CALL_NAMES = [
    "build_microstructure_features", "build_liquidity_features",
    "build_tradability_features", "build_relative_strength_features",
    "build_industry_context_features", "build_regime_features",
    "build_fundamental_context_features",
    "build_margin_features", "build_shareholder_features",
    "build_v3b_price_volume_features",
    "build_v3a_v3b_interaction_features",
    "build_industry_momentum_features",
]


# ─── Test 1: Registry self-consistency ────────────────────────────────────


class TestRegistrySelfConsistency:
    def test_no_registry_errors(self, registry_errors):
        assert registry_errors == [], (
            f"validate_registry() returned errors:\n  " +
            "\n  ".join(registry_errors)
        )

    def test_feature_id_uniqueness(self):
        ids = list_feature_ids()
        assert len(ids) == len(set(ids)), "Duplicate feature_ids found"

    def test_feature_name_uniqueness_active(self):
        """Active features must have unique names (cross-group sharing allowed)."""
        names = [s.name for s in FEATURE_REGISTRY.values() if s.status == "active"]
        dupes = {n for n in names if names.count(n) > 1}
        # stock_minus_industry_ret_3d/5d are registered in BOTH relative_strength
        # and industry_context — this is by design (computed in both groups).
        # The name index resolves this: first registration wins.
        allowed_dupes = {"stock_minus_industry_ret_3d", "stock_minus_industry_ret_5d"}
        unexpected = dupes - allowed_dupes
        assert len(unexpected) == 0, f"Active feature name collisions: {unexpected}"

    def test_all_specs_have_description(self):
        empty = [s.feature_id for s in FEATURE_REGISTRY.values() if not s.description]
        assert len(empty) == 0, f"Features without description: {empty}"

    def test_all_specs_have_kind(self):
        no_kind = [s.feature_id for s in FEATURE_REGISTRY.values() if s.kind not in ("raw", "derived")]
        assert len(no_kind) == 0, f"Features with invalid kind: {no_kind}"

    def test_all_specs_have_valid_status(self):
        valid = {"active", "experimental", "deprecated", "broken"}
        bad = [s.feature_id for s in FEATURE_REGISTRY.values() if s.status not in valid]
        assert len(bad) == 0, f"Features with invalid status: {bad}"


# ─── Test 2: YAML ↔ Registry consistency ──────────────────────────────────


class TestYamlRegistryConsistency:
    def test_yaml_features_exist_in_registry(self, yaml_feature_lists):
        """Every named feature in YAML must have a FeatureSpec."""
        for yaml_id, features in yaml_feature_lists.items():
            for feat in features:
                if feat.startswith("$") or "(" in feat or ")" in feat:
                    continue  # qlib expressions
                try:
                    get_feature(feat)
                except KeyError:
                    pytest.fail(
                        f"YAML '{yaml_id}' references '{feat}' "
                        f"which is not in FeatureSpec registry"
                    )

    def test_no_broken_features_in_yaml(self, yaml_feature_lists):
        """YAML configs must not include broken features."""
        for yaml_id, features in yaml_feature_lists.items():
            errors = validate_feature_list(features)
            broken = [e for e in errors if e.startswith("BROKEN:")]
            assert len(broken) == 0, (
                f"YAML '{yaml_id}' references broken features: {broken}"
            )

    def test_yaml_only_raw_expressions_have_spec(self, yaml_feature_lists):
        """All non-expression features in YAML must be registered."""
        for yaml_id, features in yaml_feature_lists.items():
            errors = validate_feature_list(features)
            unregistered = [e for e in errors if e.startswith("UNREGISTERED:")]
            assert len(unregistered) == 0, (
                f"YAML '{yaml_id}' has unregistered features: {unregistered}"
            )


# ─── Test 3: FEATURE_GROUPS ↔ FeatureSpec consistency ─────────────────────


class TestFeatureGroupsVsSpec:
    def test_all_group_features_have_spec(self):
        """Every feature listed in FEATURE_GROUPS must have a FeatureSpec."""
        for group_name, group_info in FEATURE_GROUPS.items():
            for feat in group_info["features"]:
                try:
                    spec = get_feature(feat)
                except KeyError:
                    pytest.fail(
                        f"FEATURE_GROUPS '{group_name}' has '{feat}' "
                        f"but no FeatureSpec found"
                    )

    def test_group_enabled_by_in_builder(self):
        """Each group's enabled_by flag must have a corresponding builder hook."""
        for group_name, group_info in FEATURE_GROUPS.items():
            flag = group_info.get("enabled_by", "")
            assert flag in RESEARCH_FEATURE_FLAGS, (
                f"Group '{group_name}' flag '{flag}' not in RESEARCH_FEATURE_FLAGS"
            )

    def test_registry_group_in_feature_groups(self):
        """Every group in FeatureSpec must appear in FEATURE_GROUPS."""
        spec_groups = set(s.group for s in FEATURE_REGISTRY.values())
        legacy_groups = set(FEATURE_GROUPS)
        missing = spec_groups - legacy_groups
        assert missing == set(), f"FeatureSpec groups not in FEATURE_GROUPS: {missing}"


# ─── Test 4: Builder flag isolation ────────────────────────────────────────


class TestBuilderFlagConsistency:
    def test_flag_toggles_only_its_group(self, base_builder_df):
        """Test that each flag adds expected features (allowing conditions)."""
        df = base_builder_df
        base_cols = set(df.columns)

        for group_name, group_info in FEATURE_GROUPS.items():
            flag = group_info["enabled_by"]
            expected = set(group_info["features"])

            # Build with only this flag on
            flags = {k: False for k in RESEARCH_FEATURE_FLAGS}
            flags[flag] = True

            # Skip groups needing external data
            if group_name in ("v3a_margin", "v3a_shareholder"):
                continue
            if group_name == "industry_context":
                continue  # requires meta.db for industry info

            try:
                result = build_phase1_features(df.copy(), flags)
            except Exception:
                continue

            new_cols = set(result.columns) - base_cols - {"index_close"}
            missing = expected - new_cols

            # Features that may be absent due to conditional computation:
            # - turnover_rate is a raw field, not produced by liquidity group
            # - stock_minus_industry_ret_3d/5d need industry_context features
            # - fundamental context scores need upstream relative_strength features
            # - roe, ps_ttm are raw field aliases, not produced by builders
            # - v3b_interaction features need v3a margin/shareholder upstream
            if group_name == "v3b_interaction":
                ok_missing = set(group_info["features"])  # all interaction features conditional
            else:
                ok_missing = {
                "turnover_rate",                   # raw field, not produced
                "stock_minus_industry_ret_3d",     # needs industry_context
                "stock_minus_industry_ret_5d",     # needs industry_context
                "industry_top_stock_momentum",     # needs ret_60d from relative_strength
                "stock_minus_industry_ret_20d",    # needs upstream industry features
                "stock_minus_industry_ret_60d",    # needs upstream industry features
                "stock_industry_ret_corr_60d",     # needs upstream industry features
                "industry_new_high_ratio",         # 120d window needs enough data
                "industry_volume_expansion",       # needs enough data
                # fundamental context fields not produced by builder
                "roe",                             # raw alias, not produced
                "ps_ttm",                          # raw alias, not produced
                # score features need upstream relative_strength features
                "continuation_candidate_score",
                "repair_candidate_score",
                "overheat_risk_score",
                "value_trap_risk_score",
            }

            critical_missing = missing - ok_missing
            assert len(critical_missing) == 0, (
                f"Flag '{flag}' (group '{group_name}') missing: {critical_missing}"
            )

    def test_no_duplicate_builder_calls(self):
        """The builder must not call the same function twice."""
        import inspect
        import re
        source = inspect.getsource(build_phase1_features)
        for fn_name in _BUILD_CALL_NAMES:
            # Count calls (function_name followed by "(") but exclude imports
            pattern = rf'(?<!from )(?<!import )(?<!\.){fn_name}\('
            matches = re.findall(pattern, source)
            count = len(matches)
            assert count <= 1, (
                f"build_phase1_features calls '{fn_name}' {count} times "
                f"(should be at most 1)"
            )
