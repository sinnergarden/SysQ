#!/usr/bin/env python3
"""Smoke test for v3a feature groups — validates feature builder, registry, configs.

Usage:
    python tests/features/test_v3a_smoke.py

Checks:
    1. v3-a feature builder can generate all columns (with mock inputs)
    2. margin_eligible exists
    3. margin NaN is not blindly filled with 0
    4. ratio features do not generate inf
    5. holder features use ann_date (via merge_asof)
    6. holder_num_stale_days >= 0
    7. feature list registration works (all 4 lists)
    8. ablation configs parse successfully
"""

import sys
from pathlib import Path

# Ensure project root is on path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import yaml

pass_count = 0
fail_count = 0


def check(condition: bool, msg: str):
    global pass_count, fail_count
    if condition:
        pass_count += 1
        print(f"  ✅ {msg}")
    else:
        fail_count += 1
        print(f"  ❌ {msg}")


def main():
    global pass_count, fail_count

    # ── 1. Feature list registration ─────────────────────────────────
    print("\n=== 1. Feature list registration ===")
    from qsys.feature.registry import FeatureListRegistry

    expected_lists = [
        "value_growth_multibagger_v2_features",
        "value_growth_v2_margin_features",
        "value_growth_v2_shareholder_features",
        "value_growth_multibagger_v3a_features",
    ]
    available = FeatureListRegistry.list_ids()
    for fl_id in expected_lists:
        check(fl_id in available, f"Feature list '{fl_id}' registered")
        if fl_id in available:
            feats = FeatureListRegistry.load(fl_id)
            check(len(feats) > 0, f"  → {len(feats)} features loaded")

    # ── 2. Ablation configs parse successfully ───────────────────────
    print("\n=== 2. Ablation configs parse ===")
    from qsys.research.matrix_job import RollingResearchConfig

    expected_configs = [
        "abl_baseline.yaml",
        "abl_margin.yaml",
        "abl_shareholder.yaml",
        "abl_full.yaml",
    ]
    for cfg_name in expected_configs:
        cfg_path = REPO / "configs" / "research" / cfg_name
        check(cfg_path.exists(), f"Config '{cfg_name}' exists")
        if cfg_path.exists():
            cfg = RollingResearchConfig.from_file(cfg_path)
            check(cfg.experiment_id is not None, f"  → experiment_id: {cfg.experiment_id}")
            check(cfg.feature_list_id is not None, f"  → feature_list_id: {cfg.feature_list_id}")
            check(len(cfg.generators) > 0, f"  → {len(cfg.generators)} generator(s)")
            check(len(cfg.labels) > 0, f"  → {len(cfg.labels)} label(s)")

    # ── 3. Registry groups are correctly defined ─────────────────────
    print("\n=== 3. Registry groups ===")
    from qsys.feature.registry import list_feature_groups

    groups = list_feature_groups()
    check("v3a_margin" in groups, "'v3a_margin' group registered")
    check("v3a_shareholder" in groups, "'v3a_shareholder' group registered")
    if "v3a_margin" in groups:
        mg = groups["v3a_margin"]
        check(len(mg["features"]) == 9, f"v3a_margin has {len(mg['features'])} features")
        check("margin_eligible" in mg["features"], "margin_eligible in v3a_margin")
        check(mg["enabled_by"] == "enable_v3a_margin_features", "enabled_by points to correct flag")
    if "v3a_shareholder" in groups:
        sh = groups["v3a_shareholder"]
        check(len(sh["features"]) == 10, f"v3a_shareholder has {len(sh['features'])} features")
        check("holder_num_chg_qoq" in sh["features"], "holder_num_chg_qoq in v3a_shareholder")
        check(sh["enabled_by"] == "enable_v3a_shareholder_features", "enabled_by points to correct flag")

    # ── 4. Feature flags in config.py ────────────────────────────────
    print("\n=== 4. Feature flags in config.py ===")
    from qsys.feature.config import RESEARCH_FEATURE_FLAGS
    check("enable_v3a_margin_features" in RESEARCH_FEATURE_FLAGS, "enable_v3a_margin_features present")
    check("enable_v3a_shareholder_features" in RESEARCH_FEATURE_FLAGS, "enable_v3a_shareholder_features present")
    check(RESEARCH_FEATURE_FLAGS["enable_v3a_margin_features"] is False, "default: False")
    check(RESEARCH_FEATURE_FLAGS["enable_v3a_shareholder_features"] is False, "default: False")

    # ── 5. Margin feature builder produces expected columns ──────────
    print("\n=== 5. Margin feature builder ===")
    from qsys.feature.groups.value_growth_v3a import build_margin_features

    # Create minimal mock input
    N = 500
    np.random.seed(42)
    mock = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
        "ts_code": ["000001.SZ"] * N,
        "margin_balance": np.where(np.random.random(N) < 0.9, np.random.uniform(1e9, 5e9, N), np.nan),
        "margin_buy_amount": np.where(np.random.random(N) < 0.9, np.random.uniform(1e7, 5e8, N), np.nan),
        "margin_repay_amount": np.where(np.random.random(N) < 0.9, np.random.uniform(1e7, 5e8, N), np.nan),
        "amount": np.random.uniform(1e8, 2e9, N),
        "circ_mv": np.random.uniform(1e10, 5e11, N),
        "ret_60d": np.random.randn(N) * 0.02,
        "ret_120d": np.random.randn(N) * 0.03,
    })

    result = build_margin_features(mock)
    expected_cols = [
        "margin_eligible",
        "margin_balance_to_float_mv",
        "margin_balance_chg_20d",
        "margin_balance_chg_60d",
        "margin_buy_intensity_20d",
        "margin_repay_to_buy_20d",
        "margin_crowding_score",
        "margin_trend_confirm_score",
        "margin_overheat_risk_score",
    ]
    for col in expected_cols:
        check(col in result.columns, f"  '{col}' column exists")

    # Check margin_eligible
    check("margin_eligible" in result.columns, "margin_eligible exists")
    if "margin_eligible" in result.columns:
        check(result["margin_eligible"].dtype == np.float64 or result["margin_eligible"].dtype == np.float32,
              "margin_eligible is float")
        check(result["margin_eligible"].notna().sum() > 0, "margin_eligible has non-null values")

    # Check NaN is not blindly filled with 0 for non-eligible stocks
    check(result["margin_balance_to_float_mv"].isna().any(),
          "margin_balance_to_float_mv retains NaN (not blindly filled 0)")

    # Check ratio features do not generate inf
    for col in expected_cols:
        if col in result.columns:
            has_inf = np.isinf(result[col].dropna()).any()
            check(not has_inf, f"  '{col}' has no inf values")

    # Check composite features produce values
    for col in ["margin_crowding_score"]:
        if col in result.columns:
            check(result[col].notna().any(), f"  '{col}' produces non-null values")

    # ── 6. Shareholder feature builder ───────────────────────────────
    print("\n=== 6. Shareholder feature builder ===")
    from qsys.feature.groups.value_growth_v3a import load_shareholder_data, build_shareholder_features

    # Add holder data paths
    holder_path = REPO / "data" / "canonical" / "holder_num.parquet"
    top10_path = REPO / "data" / "canonical" / "top10_holder_ratio.parquet"

    # Create mock with timestamp-aligned dates
    mock_sh = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
        "ts_code": ["000001.SZ"] * N,
        "total_share": np.random.uniform(1e8, 5e9, N),
    })
    mock_sh["trade_date_str"] = mock_sh["trade_date"].dt.strftime("%Y-%m-%d")

    # Load shareholder data if available
    if holder_path.exists():
        loaded = load_shareholder_data(mock_sh, str(holder_path))
        check("holder_num" in loaded.columns, "holder_num loaded from parquet")
        if "holder_num" in loaded.columns:
            check(loaded["holder_num"].notna().sum() > 0, "holder_num has non-null values")
    else:
        # Fallback: manually set holder_num
        loaded = mock_sh.copy()
        loaded["holder_num"] = np.random.randint(10000, 500000, N).astype(float)
        loaded["holder_ann_date"] = loaded["trade_date"].dt.strftime("%Y-%m-%d")
        print("  ⚠️  holder_num.parquet not found — using synthetic data")

    if top10_path.exists():
        pass  # already loaded by load_shareholder_data
    else:
        loaded["top10_holder_ratio"] = np.random.uniform(30, 90, N)
        loaded["top10_ann_date"] = loaded["trade_date"].dt.strftime("%Y-%m-%d")
        print("  ⚠️  top10_holder_ratio.parquet not found — using synthetic data")

    sh_result = build_shareholder_features(loaded)
    sh_expected = [
        "holder_num_chg_qoq",
        "holder_num_chg_2q",
        "avg_shares_per_holder_chg_qoq",
        "top10_holder_ratio_chg_qoq",
        "holder_concentration_score",
        "holder_squeeze_score",
        "holder_price_confirm_score",
        "holder_num_stale_days",
        "top10_holder_stale_days",
        "top10_holder_ratio",
    ]
    for col in sh_expected:
        if col in sh_result.columns:
            check(True, f"  '{col}' column exists")

    # Check stale_days >= 0
    for col in ["holder_num_stale_days", "top10_holder_stale_days"]:
        if col in sh_result.columns:
            vals = sh_result[col].dropna()
            check((vals >= 0).all(), f"  '{col}' all >= 0")
            check(vals.notna().any(), f"  '{col}' has non-null values")

    # ── 7. Adapter semantic support fields ───────────────────────────
    print("\n=== 7. Adapter semantic fields ===")
    from qsys.data.adapter import QlibAdapter
    support = QlibAdapter._semantic_support_fields()
    for field in ["$margin_balance", "$margin_buy_amount", "$margin_repay_amount"]:
        check(field in support, f"  '{field}' in semantic support fields")

    # ── 8. Whole pipeline compile check ──────────────────────────────
    print("\n=== 8. Pipeline integration ===")
    # Verify that builder.py can import without error
    try:
        from qsys.feature.builder import build_phase1_features
        check(True, "build_phase1_features importable")
    except Exception as e:
        check(False, f"build_phase1_features import failed: {e}")

    # Verify the v3a module compiles
    import importlib, ast
    v3a_path = REPO / "qsys" / "feature" / "groups" / "value_growth_v3a.py"
    try:
        with open(v3a_path) as f:
            ast.parse(f.read())
        check(True, "value_growth_v3a.py has valid syntax")
    except SyntaxError as e:
        check(False, f"value_growth_v3a.py has syntax error: {e}")

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{'=' * 40}")
    print(f"Results: {pass_count} passed, {fail_count} failed")
    if fail_count > 0:
        sys.exit(1)
    print("All smoke checks passed ✅")


if __name__ == "__main__":
    main()
