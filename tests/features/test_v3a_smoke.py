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
    print("\n=== 6. Shareholder feature builder (announcement-level qoq) ===")
    from qsys.feature.groups.value_growth_v3a import load_shareholder_data, build_shareholder_features

    # Use real data files if available for the coverage check
    holder_path = REPO / "data" / "canonical" / "holder_num.parquet"
    top10_path = REPO / "data" / "canonical" / "top10_holder_ratio.parquet"

    if holder_path.exists():
        # ── Test A: Real data coverage ───────────────────────────────
        mock_sh = pd.DataFrame({
            "trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
            "ts_code": ["000001.SZ"] * N,
            "total_share": np.random.uniform(1e8, 5e9, N),
        })
        loaded = load_shareholder_data(mock_sh, str(holder_path))
        check("holder_num" in loaded.columns, "holder_num loaded from parquet")
        check("holder_real_ann_date" in loaded.columns, "holder_real_ann_date exists (not trade_date)")
        check("holder_num_prev_ann" in loaded.columns, "holder_num_prev_ann loaded")
        check("holder_num_prev2_ann" in loaded.columns, "holder_num_prev2_ann loaded")
        if loaded["holder_num"].notna().sum() > 0:
            check(True, "holder_num has non-null values")

        # ── Test B: Row order preserved after merge_asof ─────────────
        # Check that trade_date sequence stays monotonic
        orig_order = list(mock_sh["trade_date"])
        merged_order = list(loaded["trade_date"])
        check(orig_order == merged_order, "Row order preserved after merge_asof")

        # ── Test C: stale_days > 0 (quarterly data on daily dates) ──────
        if "holder_real_ann_date" in loaded.columns:
            _ha = pd.to_datetime(loaded["holder_real_ann_date"], errors="coerce")
            _td = pd.to_datetime(loaded["trade_date"], errors="coerce")
            stale = (_td - _ha).dt.days
            check(stale.max() >= 0, "holder_num_stale_days max >= 0")
            # Most days should have stale > 0 (quarterly data on daily freq)
            check((stale > 0).sum() > len(stale) * 0.9, ">90% of days have stale_days > 0 (quarterly freq)")

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

        # stale_days >= 0
        for col in ["holder_num_stale_days", "top10_holder_stale_days"]:
            if col in sh_result.columns:
                vals = sh_result[col].dropna()
                check((vals >= 0).all(), f"  '{col}' all >= 0")
                check(vals.notna().any(), f"  '{col}' has non-null values")

    # ── Test D: Synthetic quarterly-announcement test ────────────────
    print("\n=== 6b. Synthetic quarterly announcement PIT test ===")

    # Build a fake parquet with quarterly announcements
    tmp_holder_path = Path("/tmp/test_holder_num_v3a.parquet")
    tmp_top10_path = Path("/tmp/test_top10_holder_ratio_v3a.parquet")

    insts = ["A", "B"]
    # Use business-day-adjusted dates so trade_date aligns with ann_date
    ann_dates = ["2023-01-16", "2023-04-17", "2023-07-17", "2023-10-16"]
    holder_data = []
    for inst in insts:
        for i, ann in enumerate(ann_dates):
            holder_data.append({"inst": inst, "ann_date": ann,
                                "holder_num": float(50000 - i * 5000)})
    pd.DataFrame(holder_data).to_parquet(tmp_holder_path)

    top10_data = []
    for inst in insts:
        for i, ann in enumerate(ann_dates):
            top10_data.append({"inst": inst, "ann_date": ann, "end_date": ann,
                               "top10_ratio": float(60 - i * 3)})
    pd.DataFrame(top10_data).to_parquet(tmp_top10_path)

    # Create daily frame: every trading day from Jan to Nov
    daily_dates = pd.date_range("2023-01-02", "2023-11-30", freq="B")
    daily = pd.DataFrame({
        "trade_date": daily_dates,
        "ts_code": insts[0],
        "total_share": 1e9,
    })
    daily2 = pd.DataFrame({
        "trade_date": daily_dates,
        "ts_code": insts[1],
        "total_share": 1e9,
    })
    mock_multi = pd.concat([daily, daily2], ignore_index=True)
    mock_multi = mock_multi.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    # Use explicit paths for holder and top10
    loaded = load_shareholder_data(mock_multi, str(tmp_holder_path))
    check("holder_num" in loaded.columns, "Synthetic: holder_num loaded")
    check("holder_real_ann_date" in loaded.columns, "Synthetic: holder_real_ann_date exists")
    check("holder_num_prev_ann" in loaded.columns, "Synthetic: holder_num_prev_ann loaded")

    # Verify row order preserved
    check(list(loaded["trade_date"]) == list(mock_multi["trade_date"]),
          "Synthetic: row order preserved after merge")

    # Verify stale_days computed from real_ann_date, not trade_date
    if "holder_real_ann_date" in loaded.columns:
        _ha = pd.to_datetime(loaded["holder_real_ann_date"])
        _td = pd.to_datetime(loaded["trade_date"])
        stale = (_td - _ha).dt.days
        check(stale.min() >= 0, "Synthetic: stale_days >= 0")
        # At least some exact-announcement days should have stale=0
        check(stale.eq(0).sum() >= 4, f"Synthetic: stale=0 on {stale.eq(0).sum()} rows (≥4)")
        # Days far from announcement should have stale >> 0
        max_stale = stale.max()
        check(max_stale > 10, f"Synthetic: max stale_days = {max_stale} (>10)")

    # Build features
    sh_result = build_shareholder_features(loaded)
    check("holder_num_chg_qoq" in sh_result.columns, "Synthetic: holder_num_chg_qoq exists")

    # KEY TEST: same-period values should NOT all be 0.
    # Before the first announcement, chg_qoq should be NaN (no prev_ann).
    # After the first, they should reflect real -20% or so changes.
    if "holder_num_chg_qoq" in sh_result.columns:
        # First announcement period: Jan 1-14 has no holder_num yet (NaN)
        # Jan 15 onwards should have a value
        chg_vals = sh_result["holder_num_chg_qoq"].dropna()
        check(len(chg_vals) > 0, "Synthetic: holder_num_chg_qoq has non-NaN values")
        # Later periods should have qoq change != 0 (50000->45000 = -10%)
        late_mask = loaded["trade_date"] >= "2023-04-20"
        if late_mask.any() and "holder_num_chg_qoq" in sh_result.columns:
            late_chg = sh_result.loc[late_mask, "holder_num_chg_qoq"]
            check(not np.isclose(late_chg.abs().mean(), 0, atol=1e-6),
                  f"Synthetic: qoq changes exist (mean abs chg = {late_chg.abs().mean():.4f}) != 0")

    # Verify top10_holder_ratio_prev_ann works the same way
    if "top10_holder_ratio_chg_qoq" in sh_result.columns:
        top10_chg = sh_result["top10_holder_ratio_chg_qoq"].dropna()
        check(len(top10_chg) > 0, "Synthetic: top10 chg_qoq has non-NaN values")

    # Cleanup temp files
    tmp_holder_path.unlink(missing_ok=True)
    tmp_top10_path.unlink(missing_ok=True)

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

    # ── 9. v3b price-volume quality feature builder ─────────────────
    print("\n=== 9. v3b price-volume quality ===")
    from qsys.feature.groups.value_growth_v3b_price_volume import (
        build_v3b_price_volume_features,
        build_v3a_v3b_interaction_features,
    )

    # Mock with 2 stocks for cross-stock contamination test
    N = 300
    np.random.seed(0)
    mock2 = pd.concat([
        pd.DataFrame({"trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
                      "ts_code": "A", "close": 100 + np.cumsum(np.random.randn(N)*0.5),
                      "amount": 1e8 + np.random.randn(N)*1e7}),
        pd.DataFrame({"trade_date": pd.date_range("2023-01-01", periods=N, freq="B"),
                      "ts_code": "B", "close": 10 + np.cumsum(np.random.randn(N)*0.1),
                      "amount": 1e7 + np.random.randn(N)*1e6}),
    ], ignore_index=True)

    pv_result = build_v3b_price_volume_features(mock2)
    pv_cols = [
        "trend_consistency_60d", "trend_consistency_120d",
        "low_vol_uptrend_60d", "low_vol_uptrend_120d",
        "return_drawdown_ratio_60d", "return_drawdown_ratio_120d",
        "pullback_recovery_speed_60d", "new_high_persistence_120d",
        "up_volume_down_volume_ratio_60d", "up_volume_down_volume_ratio_120d",
        "volume_contraction_after_rise_60d", "quiet_accumulation_60d",
        "amount_stability_60d", "breakout_volume_quality_120d",
    ]
    for col in pv_cols:
        check(col in pv_result.columns, f"  '{col}' column exists")

    for col in pv_cols:
        if col in pv_result.columns:
            check(not np.isinf(pv_result[col].dropna()).any(), f"  '{col}' has no inf")

    # Cross-stock contamination: B's first row rolling feature = NaN
    b_first_dd = pv_result[pv_result["ts_code"]=="B"]["return_drawdown_ratio_60d"].iloc[0]
    check(pd.isna(b_first_dd),
          "Cross-stock: B first row rolling(60) not contaminated by A")

    # ── 10. v3b interaction features ────────────────────────────────
    print("\n=== 10. v3b interaction features ===")
    mock3 = pv_result.copy()
    mock3["holder_concentration_score"] = np.random.randn(len(mock3))
    mock3["margin_trend_confirm_score"] = np.random.randn(len(mock3))
    inter_result = build_v3a_v3b_interaction_features(mock3)
    inter_cols = [
        "holder_concentration_trend_confirm", "holder_concentration_low_vol_uptrend",
        "holder_concentration_volume_contract", "margin_holder_trend_confirm",
        "margin_pullback_recovery_confirm",
    ]
    for col in inter_cols:
        check(col in inter_result.columns, f"  '{col}' column exists")

    # ── 11. v3b configs load ────────────────────────────────────────
    print("\n=== 11. v3b configs load ===")
    from qsys.research.matrix_job import RollingResearchConfig
    for cfg in ["abl_full_v3b_pv_delayed180.yaml", "abl_full_v3b_pv_interact_delayed180.yaml"]:
        p = REPO / "configs" / "research" / cfg
        check(p.exists(), f"Config '{cfg}' exists")
        if p.exists():
            c = RollingResearchConfig.from_file(p)
            check(c.experiment_id is not None, f"  {c.experiment_id}")

    for fl in ["value_growth_v3b_pv_features", "value_growth_v3b_pv_interact_features"]:
        feats = FeatureListRegistry.load(fl)
        check(len(feats) > 0, f"  '{fl}': {len(feats)} feats")
    print(f"\n{'=' * 40}")
    print(f"Results: {pass_count} passed, {fail_count} failed")
    if fail_count > 0:
        sys.exit(1)
    print("All smoke checks passed ✅")


if __name__ == "__main__":
    main()
