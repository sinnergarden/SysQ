#!/usr/bin/env python3
"""Generate feature_registry_audit/feature_inventory.csv.

Reads from:
- qsys/feature/registry.py (FEATURE_GROUPS)
- qsys/feature/resolver.py (_FEATURE_FORMULAS, _REQUIRED_FIELDS)
- configs/features/*.yaml (feature lists)

Classifies each feature as raw or derived, assigns temporary feature_id,
and documents dependencies, PIT rules, and cacheability.
"""

import csv
import sys
from pathlib import Path

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from qsys.feature.registry import FEATURE_GROUPS
from qsys.feature.resolver import _FEATURE_FORMULAS, _REQUIRED_FIELDS

# ── Classification helpers ──────────────────────────────────────────────────

QLIB_RAW_PREFIXES = ("$",)
"""Features starting with $ are qlib expression strings — always raw."""

RAW_SOURCE_TABLES: dict[str, str] = {
    # qlib raw fields
    "$open": "qlib_bar", "$high": "qlib_bar", "$low": "qlib_bar",
    "$close": "qlib_bar", "$vwap": "qlib_bar", "$factor": "qlib_bar",
    "$volume": "qlib_bar", "$amount": "qlib_bar",
    # qlib fina_indicator
    "$roe": "fina_indicator", "$roe_ttm2": "fina_indicator",
    "$grossprofit_margin": "fina_indicator", "$debt_to_assets": "fina_indicator",
    "$op_cashflow": "fina_indicator", "$current_ratio": "fina_indicator",
    "$total_mv": "qlib_bar", "$circ_mv": "qlib_bar",
    "$pe": "fina_indicator", "$pb": "fina_indicator",
    "$ps": "fina_indicator", "$ps_ttm": "fina_indicator",
    "$net_income": "fina_indicator", "$revenue": "fina_indicator",
    "$total_assets": "fina_indicator", "$equity": "fina_indicator",
    "$inventory": "fina_indicator", "$accounts_receiv": "fina_indicator",
    "$net_inflow": "qlib_bar", "$big_inflow": "qlib_bar",
    # qlib margin
    "$margin_balance": "qlib_margin", "$margin_buy_amount": "qlib_margin",
    "$margin_repay_amount": "qlib_margin", "$margin_total_balance": "qlib_margin",
    "$lend_volume": "qlib_margin", "$lend_sell_volume": "qlib_margin",
    "$lend_repay_volume": "qlib_margin",
    # Derived but exist in qlib fina_indicator
    "roe": "fina_indicator", "grossprofit_margin": "fina_indicator",
    "debt_to_assets": "fina_indicator", "op_cashflow": "fina_indicator",
    "net_margin": "fina_indicator", "roa": "fina_indicator",
    "revenue_yoy": "fina_indicator", "profit_yoy": "fina_indicator",
    "inventory_yoy": "fina_indicator", "ar_yoy": "fina_indicator",
    # Daily data adapter fields
    "close": "qlib_bar", "open": "qlib_bar", "high": "qlib_bar",
    "low": "qlib_bar", "volume": "qlib_bar", "amount": "qlib_bar",
    "high_limit": "qlib_bar", "low_limit": "qlib_bar",
    "turnover_rate": "qlib_bar", "circ_mv": "qlib_bar",
    "total_mv": "qlib_bar", "pe": "fina_indicator",
    "pb": "fina_indicator", "ps": "fina_indicator",
    # Shareholder / margin
    "margin_balance": "qlib_margin", "margin_buy_amount": "qlib_margin",
    "margin_repay_amount": "qlib_margin",
    "holder_num": "shareholder_parquet",
    "top10_holder_ratio": "shareholder_parquet",
    "avg_shares_per_holder": "shareholder_parquet",
}

COMPUTE_FN_MAP: dict[str, str] = {
    # microstructure
    "close_to_open_gap_1d": "groups.microstructure.build_microstructure_features",
    "open_to_close_ret": "groups.microstructure.build_microstructure_features",
    "close_pos_in_range": "groups.microstructure.build_microstructure_features",
    "open_pos_in_range": "groups.microstructure.build_microstructure_features",
    "upper_shadow_ratio": "groups.microstructure.build_microstructure_features",
    "lower_shadow_ratio": "groups.microstructure.build_microstructure_features",
    "intraday_reversal_strength": "groups.microstructure.build_microstructure_features",
    # liquidity
    "amount_log": "groups.liquidity.build_liquidity_features",
    "amount_zscore_20": "groups.liquidity.build_liquidity_features",
    "volume_shock_3": "groups.liquidity.build_liquidity_features",
    "volume_shock_5": "groups.liquidity.build_liquidity_features",
    "turnover_acceleration": "groups.liquidity.build_liquidity_features",
    "illiquidity": "groups.liquidity.build_liquidity_features",
    # tradability
    "is_limit_up": "groups.tradability.build_tradability_features",
    "is_limit_down": "groups.tradability.build_tradability_features",
    "distance_to_limit_up": "groups.tradability.build_tradability_features",
    "distance_to_limit_down": "groups.tradability.build_tradability_features",
    "limit_up_count_5d": "groups.tradability.build_tradability_features",
    "tradability_score": "groups.tradability.build_tradability_features",
    "opened_from_limit_up": "groups.tradability.build_tradability_features",
    # relative_strength
    "ret_1d": "groups.relative_strength.build_relative_strength_features",
    "ret_3d": "groups.relative_strength.build_relative_strength_features",
    "ret_5d": "groups.relative_strength.build_relative_strength_features",
    "vol_mean_3d": "groups.relative_strength.build_relative_strength_features",
    "vol_mean_5d": "groups.relative_strength.build_relative_strength_features",
    "amount_mean_3d": "groups.relative_strength.build_relative_strength_features",
    "amount_mean_5d": "groups.relative_strength.build_relative_strength_features",
    "ret_1d_rank": "groups.relative_strength.build_relative_strength_features",
    "ret_3d_rank": "groups.relative_strength.build_relative_strength_features",
    "ret_5d_rank": "groups.relative_strength.build_relative_strength_features",
    "vol_mean_3d_rank": "groups.relative_strength.build_relative_strength_features",
    "vol_mean_5d_rank": "groups.relative_strength.build_relative_strength_features",
    "amount_mean_3d_rank": "groups.relative_strength.build_relative_strength_features",
    "amount_mean_5d_rank": "groups.relative_strength.build_relative_strength_features",
    "stock_minus_index_ret_3d": "groups.relative_strength.build_relative_strength_features",
    "stock_minus_index_ret_5d": "groups.relative_strength.build_relative_strength_features",
    "stock_minus_industry_ret_3d": "groups.relative_strength.build_relative_strength_features",
    "stock_minus_industry_ret_5d": "groups.relative_strength.build_relative_strength_features",
    "ret_20d": "groups.relative_strength.build_relative_strength_features",
    "ret_60d": "groups.relative_strength.build_relative_strength_features",
    "ret_120d": "groups.relative_strength.build_relative_strength_features",
    "volume_ratio_20d": "groups.relative_strength.build_relative_strength_features",
    "volume_ratio_60d": "groups.relative_strength.build_relative_strength_features",
    "distance_to_120d_high": "groups.relative_strength.build_relative_strength_features",
    "distance_to_250d_high": "groups.relative_strength.build_relative_strength_features",
    "up_day_ratio_60d": "groups.relative_strength.build_relative_strength_features",
    "up_day_ratio_120d": "groups.relative_strength.build_relative_strength_features",
    "trend_smoothness_60d": "groups.relative_strength.build_relative_strength_features",
    "trend_smoothness_120d": "groups.relative_strength.build_relative_strength_features",
    "max_pullback_120d": "groups.relative_strength.build_relative_strength_features",
    "volatility_adjusted_return_60d": "groups.relative_strength.build_relative_strength_features",
    "volatility_adjusted_return_120d": "groups.relative_strength.build_relative_strength_features",
    "rps_60d": "groups.relative_strength.build_relative_strength_features",
    "rps_120d": "groups.relative_strength.build_relative_strength_features",
    "rps_20d": "groups.relative_strength.build_relative_strength_features",
    "rps_20d_minus_rps_60d": "groups.relative_strength.build_relative_strength_features",
    "rps_industry_60d": "groups.relative_strength.build_relative_strength_features",
    "rps_industry_120d": "groups.relative_strength.build_relative_strength_features",
    "price_percentile_252d": "groups.relative_strength.build_relative_strength_features",
    "distance_to_252d_low": "groups.relative_strength.build_relative_strength_features",
    "volume_up_down_ratio_60d": "groups.relative_strength.build_relative_strength_features",
    "above_avg_volume_ratio_60d": "groups.relative_strength.build_relative_strength_features",
    "amount_ratio_20d": "groups.relative_strength.build_relative_strength_features",
    "amount_ratio_60d": "groups.relative_strength.build_relative_strength_features",
    "volume_spike_20d": "groups.relative_strength.build_relative_strength_features",
    "volume_stability_60d": "groups.relative_strength.build_relative_strength_features",
    # regime
    "market_breadth": "groups.regime.build_regime_features",
    "limit_up_breadth": "groups.regime.build_regime_features",
    "index_volatility_5": "groups.regime.build_regime_features",
    "index_volatility_10": "groups.regime.build_regime_features",
    "index_volatility_20": "groups.regime.build_regime_features",
    "small_vs_large_strength": "groups.regime.build_regime_features",
    "growth_vs_value_proxy": "groups.regime.build_regime_features",
    "market_trend_strength": "groups.regime.build_regime_features",
    # industry_context
    "industry_ret_1d": "groups.industry_context.build_industry_context_features",
    "industry_ret_3d": "groups.industry_context.build_industry_context_features",
    "industry_ret_5d": "groups.industry_context.build_industry_context_features",
    "industry_breadth": "groups.industry_context.build_industry_context_features",
    "stock_minus_industry_ret": "groups.industry_context.build_industry_context_features",
    "stock_minus_industry_ret_3d": "groups.industry_context.build_industry_context_features",
    "stock_minus_industry_ret_5d": "groups.industry_context.build_industry_context_features",
    # fundamental_context
    "log_mktcap": "groups.fundamental_context.build_fundamental_context_features",
    "float_mktcap": "groups.fundamental_context.build_fundamental_context_features",
    "pe_ttm": "groups.fundamental_context.build_fundamental_context_features",
    "pb_raw": "groups.fundamental_context.build_fundamental_context_features",
    "ps_ttm": "groups.fundamental_context.build_fundamental_context_features",
    "gross_margin": "groups.fundamental_context.build_fundamental_context_features",
    "net_margin": "groups.fundamental_context.build_fundamental_context_features",
    "operating_cf_to_profit": "groups.fundamental_context.build_fundamental_context_features",
    "debt_to_asset": "groups.fundamental_context.build_fundamental_context_features",
    "roe_delta_252d": "groups.fundamental_context.build_fundamental_context_features",
    "grossprofit_margin_delta_252d": "groups.fundamental_context.build_fundamental_context_features",
    "debt_to_assets_delta_252d": "groups.fundamental_context.build_fundamental_context_features",
    "op_cashflow_delta_252d": "groups.fundamental_context.build_fundamental_context_features",
    "pe_rank_252d": "groups.fundamental_context.build_fundamental_context_features",
    "pb_rank_252d": "groups.fundamental_context.build_fundamental_context_features",
    "pe_delta_120d": "groups.fundamental_context.build_fundamental_context_features",
    "pb_delta_120d": "groups.fundamental_context.build_fundamental_context_features",
    "pe_percentile_756d": "groups.fundamental_context.build_fundamental_context_features",
    "pb_percentile_756d": "groups.fundamental_context.build_fundamental_context_features",
    "pe_distance_from_756d_low": "groups.fundamental_context.build_fundamental_context_features",
    "pb_distance_from_756d_low": "groups.fundamental_context.build_fundamental_context_features",
    "pe_repair_room_to_median": "groups.fundamental_context.build_fundamental_context_features",
    "pb_repair_room_to_median": "groups.fundamental_context.build_fundamental_context_features",
    "earnings_yield_proxy": "groups.fundamental_context.build_fundamental_context_features",
    "peg_proxy": "groups.fundamental_context.build_fundamental_context_features",
    "revenue_yoy": "groups.fundamental_context.build_fundamental_context_features",
    "profit_yoy": "groups.fundamental_context.build_fundamental_context_features",
    "inventory_yoy": "groups.fundamental_context.build_fundamental_context_features",
    "ar_yoy": "groups.fundamental_context.build_fundamental_context_features",
    "revenue_yoy_accel": "groups.fundamental_context.build_fundamental_context_features",
    "profit_yoy_accel": "groups.fundamental_context.build_fundamental_context_features",
    "roe_delta_756d": "groups.fundamental_context.build_fundamental_context_features",
    "net_margin_delta_756d": "groups.fundamental_context.build_fundamental_context_features",
    "ocf_margin": "groups.fundamental_context.build_fundamental_context_features",
    "continuation_candidate_score": "groups.fundamental_context.build_fundamental_context_features",
    "repair_candidate_score": "groups.fundamental_context.build_fundamental_context_features",
    "overheat_risk_score": "groups.fundamental_context.build_fundamental_context_features",
    "value_trap_risk_score": "groups.fundamental_context.build_fundamental_context_features",
    # v3a_margin
    "margin_eligible": "groups.value_growth_v3a.build_margin_features",
    "margin_balance_to_float_mv": "groups.value_growth_v3a.build_margin_features",
    "margin_balance_chg_20d": "groups.value_growth_v3a.build_margin_features",
    "margin_balance_chg_60d": "groups.value_growth_v3a.build_margin_features",
    "margin_buy_intensity_20d": "groups.value_growth_v3a.build_margin_features",
    "margin_repay_to_buy_20d": "groups.value_growth_v3a.build_margin_features",
    "margin_crowding_score": "groups.value_growth_v3a.build_margin_features",
    "margin_trend_confirm_score": "groups.value_growth_v3a.build_margin_features",
    "margin_overheat_risk_score": "groups.value_growth_v3a.build_margin_features",
    # v3a_shareholder
    "holder_num_chg_qoq": "groups.value_growth_v3a.build_shareholder_features",
    "holder_num_chg_2q": "groups.value_growth_v3a.build_shareholder_features",
    "avg_shares_per_holder_chg_qoq": "groups.value_growth_v3a.build_shareholder_features",
    "top10_holder_ratio_chg_qoq": "groups.value_growth_v3a.build_shareholder_features",
    "holder_concentration_score": "groups.value_growth_v3a.build_shareholder_features",
    "holder_squeeze_score": "groups.value_growth_v3a.build_shareholder_features",
    "holder_price_confirm_score": "groups.value_growth_v3a.build_shareholder_features",
    "holder_num_stale_days": "groups.value_growth_v3a.build_shareholder_features",
    "top10_holder_stale_days": "groups.value_growth_v3a.build_shareholder_features",
    "top10_holder_ratio": "groups.value_growth_v3a.build_shareholder_features",
    # v3b_price_volume
    "trend_consistency_60d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "trend_consistency_120d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "low_vol_uptrend_60d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "low_vol_uptrend_120d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "return_drawdown_ratio_60d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "return_drawdown_ratio_120d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "pullback_recovery_speed_60d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "new_high_persistence_120d": "groups.value_growth_v3b_price_volume.build_trend_quality_features",
    "up_volume_down_volume_ratio_60d": "groups.value_growth_v3b_price_volume.build_volume_quality_features",
    "up_volume_down_volume_ratio_120d": "groups.value_growth_v3b_price_volume.build_volume_quality_features",
    "volume_contraction_after_rise_60d": "groups.value_growth_v3b_price_volume.build_volume_quality_features",
    "quiet_accumulation_60d": "groups.value_growth_v3b_price_volume.build_volume_quality_features",
    "amount_stability_60d": "groups.value_growth_v3b_price_volume.build_volume_quality_features",
    "breakout_volume_quality_120d": "groups.value_growth_v3b_price_volume.build_volume_quality_features",
    # v3b_interaction
    "holder_concentration_trend_confirm": "groups.value_growth_v3b_price_volume.build_v3a_v3b_interaction_features",
    "holder_concentration_low_vol_uptrend": "groups.value_growth_v3b_price_volume.build_v3a_v3b_interaction_features",
    "holder_concentration_volume_contract": "groups.value_growth_v3b_price_volume.build_v3a_v3b_interaction_features",
    "margin_holder_trend_confirm": "groups.value_growth_v3b_price_volume.build_v3a_v3b_interaction_features",
    "margin_pullback_recovery_confirm": "groups.value_growth_v3b_price_volume.build_v3a_v3b_interaction_features",
    # industry_momentum
    "industry_ret_20d": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_ret_60d": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_ret_120d": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_breadth_20d": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_breadth_60d": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_new_high_ratio": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_top_stock_momentum": "groups.industry_momentum_features.build_industry_momentum_features",
    "industry_volume_expansion": "groups.industry_momentum_features.build_industry_momentum_features",
    "stock_minus_industry_ret_20d": "groups.industry_momentum_features.build_industry_momentum_features",
    "stock_minus_industry_ret_60d": "groups.industry_momentum_features.build_industry_momentum_features",
    "stock_industry_ret_corr_60d": "groups.industry_momentum_features.build_industry_momentum_features",
}

# Fields also used as raw inputs (direct from data adapter, not really "features")
RAW_ONLY_FIELDS = {
    "close", "open", "high", "low", "volume", "amount", "turnover_rate",
    "high_limit", "low_limit", "circ_mv", "total_mv",
    "pe", "pb", "ps", "roe", "grossprofit_margin", "debt_to_assets",
    "op_cashflow", "net_margin", "roa", "revenue", "net_income",
    "inventory", "accounts_receiv", "total_assets", "equity",
    "index_close", "industry", "industry_code",
    "margin_balance", "margin_buy_amount", "margin_repay_amount",
    "margin_total_balance", "lend_volume", "lend_sell_volume", "lend_repay_volume",
    "holder_num", "top10_holder_ratio", "avg_shares_per_holder", "total_share",
    "paused", "float_shares", "up_limit", "down_limit",
}

# Features that are known to have semantic issues
BROKEN_FEATURES: set[str] = set()
DEPRECATED_FEATURES: set[str] = set()
EXPERIMENTAL_FEATURES: set[str] = {
    "industry_ret_20d", "industry_ret_60d", "industry_ret_120d",
    "industry_breadth_20d", "industry_breadth_60d",
    "industry_new_high_ratio", "industry_top_stock_momentum",
    "industry_volume_expansion",
    "stock_minus_industry_ret_20d", "stock_minus_industry_ret_60d",
    "stock_industry_ret_corr_60d",
}

PIT_RULES: dict[str, str] = {
    "__default__": "rolling: past-only, per-instrument groupby, no lookahead",
    "__cross_sectional__": "cross-sectional: per-trade_date groupby, same-day rank/percentile",
    "__industry__": "industry aggregation: groupby([trade_date, industry]) for cross-section, then temporal rolling per industry",
}

def classify_kind(feature_name: str) -> str:
    """Classify as raw or derived."""
    if feature_name.startswith("$"):
        return "raw"
    if feature_name in RAW_ONLY_FIELDS:
        return "raw"
    if feature_name in COMPUTE_FN_MAP:
        return "derived"
    return "raw"  # default safe assumption

def determine_pit(feature_name: str, group: str | None) -> str:
    """Determine PIT type."""
    if feature_name.startswith("$"):
        return "point_in_time"
    if "_rank" in feature_name or "rps" in feature_name or "percentile" in feature_name:
        return "cross_sectional"
    if group in ("industry_context", "industry_momentum"):
        return "industry"
    if "_zscore" in feature_name or "_score" in feature_name:
        return "cross_sectional"
    if "_yoy" in feature_name or "yoy" in feature_name:
        return "point_in_time"
    if "rolling" in feature_name.lower():
        return "rolling_past"
    if "_delta_" in feature_name:
        return "rolling_past"
    return "rolling_past"

def determine_cacheable(feature_name: str, kind: str) -> bool:
    """Determine if feature is expensive enough to cache."""
    if kind == "raw":
        return False
    # Expensive derived features: rolling windows ≥ 60, cross-sectional ranks
    expensive = {
        # Cross-sectional ranks / percentiles
        "ret_1d_rank", "ret_3d_rank", "ret_5d_rank",
        "vol_mean_3d_rank", "vol_mean_5d_rank",
        "amount_mean_3d_rank", "amount_mean_5d_rank",
        "rps_60d", "rps_120d", "rps_20d", "rps_20d_minus_rps_60d",
        "rps_industry_60d", "rps_industry_120d",
        "price_percentile_252d",
        "pe_rank_252d", "pb_rank_252d",
        "pe_percentile_756d", "pb_percentile_756d",
        # Long rolling windows
        "ret_60d", "ret_120d",
        "distance_to_120d_high", "distance_to_250d_high",
        "distance_to_252d_low",
        "up_day_ratio_60d", "up_day_ratio_120d",
        "trend_smoothness_60d", "trend_smoothness_120d",
        "max_pullback_120d",
        "volatility_adjusted_return_60d", "volatility_adjusted_return_120d",
        "volume_up_down_ratio_60d",
        "above_avg_volume_ratio_60d",
        "volume_stability_60d",
        # 756d windows
        "pe_distance_from_756d_low", "pb_distance_from_756d_low",
        "pe_repair_room_to_median", "pb_repair_room_to_median",
        "roe_delta_756d", "net_margin_delta_756d",
        # Composite scores
        "continuation_candidate_score", "repair_candidate_score",
        "overheat_risk_score", "value_trap_risk_score",
        # Industry momentum
        "industry_ret_60d", "industry_ret_120d",
        "industry_breadth_20d", "industry_breadth_60d",
        "industry_new_high_ratio", "industry_top_stock_momentum",
        "industry_volume_expansion",
        "stock_minus_industry_ret_60d",
        "stock_industry_ret_corr_60d",
        # V3b trend quality
        "trend_consistency_60d", "trend_consistency_120d",
        "low_vol_uptrend_60d", "low_vol_uptrend_120d",
        "return_drawdown_ratio_60d", "return_drawdown_ratio_120d",
        "pullback_recovery_speed_60d",
        "new_high_persistence_120d",
        "quiet_accumulation_60d",
        "breakout_volume_quality_120d",
        # Margin
        "margin_crowding_score", "margin_trend_confirm_score",
        "margin_overheat_risk_score",
        # Shareholder
        "holder_concentration_score", "holder_squeeze_score",
        "holder_price_confirm_score",
        # Interaction
        "holder_concentration_trend_confirm",
        "holder_concentration_low_vol_uptrend",
        "holder_concentration_volume_contract",
        "margin_holder_trend_confirm",
        "margin_pullback_recovery_confirm",
    }
    return feature_name in expensive

def get_dependencies(feature_name: str, kind: str, group: str | None) -> list[str]:
    """Get dependencies from resolver or infer."""
    if kind == "raw":
        return []
    deps = _REQUIRED_FIELDS.get(feature_name, [])
    return deps

def get_yaml_refs(feature_name: str, yaml_feature_sets: dict[str, set[str]]) -> list[str]:
    """Find which YAML configs reference this feature."""
    refs = []
    for yaml_id, features in yaml_feature_sets.items():
        if feature_name in features:
            refs.append(yaml_id)
    return refs


# ── Load YAML references ───────────────────────────────────────────────────

def load_yaml_feature_sets() -> dict[str, set[str]]:
    """Load all feature names from configs/features/*.yaml."""
    import yaml
    yaml_dir = PROJECT_ROOT / "configs" / "features"
    result: dict[str, set[str]] = {}
    if not yaml_dir.exists():
        return result
    for p in sorted(yaml_dir.glob("*.yaml")):
        if p.stem == "__init__":
            continue
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            feats = data.get("features", [])
            result[p.stem] = set(str(f) for f in feats)
        except Exception:
            pass
    return result


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    yaml_feature_sets = load_yaml_feature_sets()

    out_dir = PROJECT_ROOT / "artifacts" / "feature_registry_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "feature_inventory.csv"

    # Assign temp feature_id: group_prefix + _ + name
    rows = []
    seen_names: set[str] = set()

    for group_name, group_info in FEATURE_GROUPS.items():
        for feat in group_info["features"]:
            if feat in seen_names:
                continue
            seen_names.add(feat)
            kind = classify_kind(feat)
            pit = determine_pit(feat, group_name)
            cacheable = determine_cacheable(feat, kind)
            deps = get_dependencies(feat, kind, group_name)
            yaml_refs = get_yaml_refs(feat, yaml_feature_sets)

            if feat in BROKEN_FEATURES:
                status = "broken"
            elif feat in DEPRECATED_FEATURES:
                status = "deprecated"
            elif feat in EXPERIMENTAL_FEATURES:
                status = "experimental"
            else:
                status = "active"

            source_table = RAW_SOURCE_TABLES.get(feat, "")
            compute_fn = COMPUTE_FN_MAP.get(feat, kind if kind == "raw" else "")

            rows.append({
                "feature_id": f"{group_name}__{feat}",
                "feature_name": feat,
                "feature_group": group_name,
                "kind": kind,
                "source_table": source_table,
                "dependencies": ";".join(deps) if deps else "",
                "compute_fn": compute_fn,
                "pit_rule": pit,
                "cacheable": str(cacheable),
                "yaml_refs": ";".join(yaml_refs) if yaml_refs else "",
                "registry_refs": group_name,
                "status": status,
                "notes": "",
            })

    # Also add registry-only features not in any group (if any)
    # And add raw fields from YAML that are not in any group
    for yaml_id, feats in yaml_feature_sets.items():
        for feat in sorted(feats):
            if feat in seen_names:
                continue
            seen_names.add(feat)
            kind = classify_kind(feat)
            pit = determine_pit(feat, None)
            cacheable = determine_cacheable(feat, kind)
            source_table = RAW_SOURCE_TABLES.get(feat, "qlib_expression")
            compute_fn = COMPUTE_FN_MAP.get(feat, "")
            rows.append({
                "feature_id": f"yaml_only__{feat}",
                "feature_name": feat,
                "feature_group": "yaml_only",
                "kind": kind,
                "source_table": source_table,
                "dependencies": "",
                "compute_fn": compute_fn,
                "pit_rule": pit,
                "cacheable": str(cacheable),
                "yaml_refs": yaml_id,
                "registry_refs": "",
                "status": "active",
                "notes": "YAML-only feature, not registered in FEATURE_GROUPS",
            })

    # Sort: active first, by group, then by name
    status_order = {"active": 0, "experimental": 1, "deprecated": 2, "broken": 3}
    rows.sort(key=lambda r: (status_order.get(r["status"], 9), r["feature_group"], r["feature_name"]))

    fieldnames = [
        "feature_id", "feature_name", "feature_group", "kind", "source_table",
        "dependencies", "compute_fn", "pit_rule", "cacheable",
        "yaml_refs", "registry_refs", "status", "notes",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "total": len(rows),
        "active": sum(1 for r in rows if r["status"] == "active"),
        "experimental": sum(1 for r in rows if r["status"] == "experimental"),
        "deprecated": sum(1 for r in rows if r["status"] == "deprecated"),
        "broken": sum(1 for r in rows if r["status"] == "broken"),
        "raw": sum(1 for r in rows if r["kind"] == "raw"),
        "derived": sum(1 for r in rows if r["kind"] == "derived"),
        "cacheable": sum(1 for r in rows if r["cacheable"] == "True"),
        "yaml_only": sum(1 for r in rows if r["feature_group"] == "yaml_only"),
    }
    print(f"✅ Feature inventory written to {out_path}")
    for k, v in summary.items():
        print(f"   {k}: {v}")

    # Write summary as companion JSON
    import json
    summary_path = out_dir / "inventory_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"✅ Summary written to {summary_path}")


if __name__ == "__main__":
    main()
