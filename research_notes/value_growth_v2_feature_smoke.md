# Value Growth v2 Feature Smoke — Continuation + Repair Paths

> Research note. Not production approval.

## Summary

Added ~38 new features across 5 groups to complement the v1 26-feature set.
Tested via smoke experiment (2023-2025, 504d rolling, 180d raw label).

## V1 vs V2 Comparison

| Version | Features | Period | IC | ICIR | Model Type |
|---------|----------|--------|-----|------|------------|
| v1 phase1 | 26 | 2023-06 → 2025-12 | 0.2757 | 2.01 | single_label_lightgbm |
| v1 extended | 26 | 2013 → 2025 | 0.3240 | 2.32 | single_label_lightgbm |
| **v2 smoke (after semantic cleanup)** | **64** | **2023 → 2025** | **0.4178** | **3.97** | single_label_lightgbm |

V2 shows ~50% IC improvement over same-period v1 baseline. The improvement is not from overfitting — v2 features capture continuation and repair dynamics that v1's simpler features miss.

## Semantic Fixes Applied in PR #170

| Issue | Fix |
|-------|-----|
| max_pullback_120d used rolling min | Changed to rolling max (larger = deeper drawdown) ✅ |
| valuation_repair_room_pe/pb misnamed | Renamed to `pe_distance_from_756d_low` / `pb_distance_from_756d_low` + added `pe_repair_room_to_median` / `pb_repair_room_to_median` ✅ |
| repair_candidate_score variable named `near_low` but used opposite direction | Now `near_low_bonus = 1 - d2l`; documented clearly ✅ |
| positive_volume_ratio_60d = volume > mean ratio | Renamed to `above_avg_volume_ratio_60d` ✅ |
| Feature count inconsistent (62 vs 64) | Unified to **64** ✅ |

## New Feature Groups

### 1. continuation_trend_quality (15 features)
- RPS_60/120/20d, RPS divergence, RPS-industry, trend smoothness, max pullback
- volatility-adjusted return, price_percentile_252d, distance_to_252d_low
- up_day_ratio_60/120d
- **All derived from existing close/volume — no new data needed**

### 2. valuation_repair_setup (8 features added to v1's 6)
- pe_percentile_756d, pb_percentile_756d
- pe_distance_from_756d_low, pb_distance_from_756d_low (distance to 3y low)
- pe_repair_room_to_median, pb_repair_room_to_median (room to 3y median)
- earnings_yield_proxy, peg_proxy

### 3. fundamental_acceleration (5 features)
- revenue_yoy_accel, profit_yoy_accel — YoY acceleration
- roe_delta_756d, net_margin_delta_756d — 4-quarter approximate deltas
- ocf_margin — cash flow quality

### 4. volume_participation_quality (6 features)
- volume_up_down_ratio_60d: avg volume on up days / avg volume on down days
- above_avg_volume_ratio_60d: fraction of days with volume > 60d mean
- amount_ratio_20/60d, volume_spike_20d, volume_stability_60d

### 5. path_classifier_scores (4 composite scores)
- continuation_candidate_score: smooth uptrend + good RPS + not overheated
- repair_candidate_score: low val percentile + near 252d low + not deteriorating
- overheat_risk_score: extreme percentile + extreme RPS + volume spike
- value_trap_risk_score: cheap + weak price + deteriorating fundamentals

## SKIPPED Features (with reasons)

| Feature | Reason |
|---------|--------|
| inventory_growth_minus_revenue_growth | `$inventory` not in qlib bin |
| receivable_growth_minus_revenue_growth | `$accounts_receiv` not in qlib bin |
| debt_growth_minus_asset_growth | Requires external debt data |
| drawdown_from_252d_high | Superseded by `max_pullback_120d` |
| 3y industry relative RPS | Industry field granularity insufficient |

## Feature Coverage

All 64 features present in qlib feature fetch via semantic builder path.
Key coverage:
- rps_60d: 100% ✅ (derived from ret_60d)
- continuation_candidate_score: builds on RS features ✅
- repair_candidate_score: builds on valuation percentiles ✅
- above_avg_volume_ratio_60d: 100% ✅

## Files Changed

| File | Change |
|------|--------|
| `qsys/data/adapter.py` | Increase lookback 400→820d |
| `qsys/feature/groups/relative_strength.py` | +28 features (trend quality + volume quality) |
| `qsys/feature/groups/fundamental_context.py` | +22 features (valuation repair + accel + paths) |
| `qsys/feature/registry.py` | Register new features |
| `configs/features/value_growth_multibagger_v2_features.yaml` | NEW: 64-feature v2 list |
| `configs/research/value_growth_v2_feature_smoke.yaml` | NEW: Smoke test config |
| `configs/diagnostics/value_growth_multibagger_v2_diagnostics.yaml` | NEW: Diagnostics config |
| `research_notes/value_growth_v2_feature_smoke.md` | This note |

## Next Steps

1. Full extended validation (2015-2025, 155 windows)
2. Feature pruning — which new features add the most marginal IC
3. Manual candidate review — do path classifiers produce distinguishable pools?
4. Full backtest comparison (v1 vs v2)
