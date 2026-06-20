#!/usr/bin/env python3
"""Generate feature_inventory.csv — machine-readable inventory of all features."""
import csv, sys, yaml
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

output_dir = REPO / "artifacts" / "feature_registry_audit"
output_dir.mkdir(parents=True, exist_ok=True)

from qsys.feature.registry import FEATURE_GROUPS
from qsys.feature.resolver import _FEATURE_FORMULAS

# ── known compute function mapping ──
FEATURE_TO_FN = {
    "close_to_open_gap_1d": "build_microstructure_features", "open_to_close_ret": "build_microstructure_features",
    "close_pos_in_range": "build_microstructure_features", "open_pos_in_range": "build_microstructure_features",
    "upper_shadow_ratio": "build_microstructure_features", "lower_shadow_ratio": "build_microstructure_features",
    "intraday_reversal_strength": "build_microstructure_features",
    "turnover_rate": "build_liquidity_features", "amount_log": "build_liquidity_features",
    "amount_zscore_20": "build_liquidity_features", "volume_shock_3": "build_liquidity_features",
    "volume_shock_5": "build_liquidity_features", "turnover_acceleration": "build_liquidity_features",
    "illiquidity": "build_liquidity_features",
    "is_limit_up": "build_tradability_features", "is_limit_down": "build_tradability_features",
    "distance_to_limit_up": "build_tradability_features", "distance_to_limit_down": "build_tradability_features",
    "limit_up_count_5d": "build_tradability_features", "tradability_score": "build_tradability_features",
    "opened_from_limit_up": "build_tradability_features",
    "ret_1d": "build_relative_strength_features", "ret_3d": "build_relative_strength_features",
    "ret_5d": "build_relative_strength_features", "vol_mean_3d": "build_relative_strength_features",
    "vol_mean_5d": "build_relative_strength_features",
    "ret_20d": "build_relative_strength_features", "ret_60d": "build_relative_strength_features",
    "ret_120d": "build_relative_strength_features",
    "rps_60d": "build_relative_strength_features", "rps_120d": "build_relative_strength_features",
    "rps_20d": "build_relative_strength_features",
    "rps_industry_60d": "build_relative_strength_features",
    "rps_industry_120d": "build_relative_strength_features",
    "market_breadth": "build_regime_features", "limit_up_breadth": "build_regime_features",
    "index_volatility_5": "build_regime_features", "index_volatility_10": "build_regime_features",
    "index_volatility_20": "build_regime_features",
    "small_vs_large_strength": "build_regime_features",
    "growth_vs_value_proxy": "build_regime_features", "market_trend_strength": "build_regime_features",
    "industry_ret_1d": "build_industry_context_features", "industry_ret_3d": "build_industry_context_features",
    "industry_ret_5d": "build_industry_context_features", "industry_breadth": "build_industry_context_features",
    "log_mktcap": "build_fundamental_context_features", "float_mktcap": "build_fundamental_context_features",
    "pe_ttm": "build_fundamental_context_features", "pb_raw": "build_fundamental_context_features",
    "ps_ttm": "build_fundamental_context_features", "roe": "build_fundamental_context_features",
    "roa": "build_fundamental_context_features", "gross_margin": "build_fundamental_context_features",
    "net_margin": "build_fundamental_context_features",
    "operating_cf_to_profit": "build_fundamental_context_features",
    "debt_to_asset": "build_fundamental_context_features",
    "revenue_yoy": "build_fundamental_context_features", "profit_yoy": "build_fundamental_context_features",
    "inventory_yoy": "build_fundamental_context_features", "ar_yoy": "build_fundamental_context_features",
    "margin_eligible": "build_margin_features", "margin_balance_to_float_mv": "build_margin_features",
    "margin_balance_chg_20d": "build_margin_features", "margin_balance_chg_60d": "build_margin_features",
    "margin_buy_intensity_20d": "build_margin_features",
    "margin_repay_to_buy_20d": "build_margin_features",
    "margin_crowding_score": "build_margin_features",
    "margin_trend_confirm_score": "build_margin_features",
    "margin_overheat_risk_score": "build_margin_features",
    "holder_num_chg_qoq": "build_shareholder_features", "holder_num_chg_2q": "build_shareholder_features",
    "avg_shares_per_holder_chg_qoq": "build_shareholder_features",
    "top10_holder_ratio_chg_qoq": "build_shareholder_features",
    "holder_concentration_score": "build_shareholder_features",
    "holder_squeeze_score": "build_shareholder_features",
    "holder_price_confirm_score": "build_shareholder_features",
    "holder_num_stale_days": "build_shareholder_features",
    "top10_holder_stale_days": "build_shareholder_features", "top10_holder_ratio": "build_shareholder_features",
    "trend_consistency_60d": "build_v3b_price_volume_features",
    "trend_consistency_120d": "build_v3b_price_volume_features",
    "holder_concentration_trend_confirm": "build_v3a_v3b_interaction_features",
    "industry_ret_20d": "build_industry_momentum_features",
    "industry_ret_60d": "build_industry_momentum_features",
    "industry_ret_120d": "build_industry_momentum_features",
    "industry_breadth_20d": "build_industry_momentum_features",
    "industry_breadth_60d": "build_industry_momentum_features",
    "industry_new_high_ratio": "build_industry_momentum_features",
    "industry_top_stock_momentum": "build_industry_momentum_features",
    "industry_volume_expansion": "build_industry_momentum_features",
    "stock_minus_industry_ret_20d": "build_industry_momentum_features",
    "stock_minus_industry_ret_60d": "build_industry_momentum_features",
    "stock_industry_ret_corr_60d": "build_industry_momentum_features",
}

# build all features
all_features = []
for group_name, group_info in FEATURE_GROUPS.items():
    flag = group_info["enabled_by"]
    for feat in group_info["features"]:
        compute_fn = FEATURE_TO_FN.get(feat, "")
        formula = _FEATURE_FORMULAS.get(feat, "")
        all_features.append({
            "feature_id": feat,
            "feature_name": feat,
            "feature_group": group_name,
            "kind": "derived",
            "source_table": "",
            "dependencies": "",
            "compute_fn": compute_fn,
            "pit_rule": "",
            "cacheable": "yes" if any(w in feat for w in ["60d","120d","20d","_chg_"]) else "no",
            "formula": formula,
            "yaml_refs": "",
            "registry_refs": group_name,
            "status": "active",
            "notes": "",
        })

# raw features
RAW_FEATURES = [
    ("close","price_volume","daily","OHLCV"),("open","price_volume","daily","OHLCV"),
    ("high","price_volume","daily","OHLCV"),("low","price_volume","daily","OHLCV"),
    ("volume","price_volume","daily","OHLCV"),("amount","price_volume","daily","OHLCV"),
    ("factor","price_volume","daily","adjustment"),("vwap","price_volume","daily","VWAP"),
    ("high_limit","price_volume","daily","limit_up"),("low_limit","price_volume","daily","limit_down"),
    ("turnover_rate","trading_activity","daily","turnover"),
    ("pe","valuation","fina_indicator","PE TTM"),("pb","valuation","fina_indicator","PB"),
    ("ps","valuation","fina_indicator","PS TTM"),
    ("total_mv","market_cap","fina_indicator",""),("circ_mv","market_cap","fina_indicator",""),
    ("net_inflow","money_flow","daily",""),("big_inflow","money_flow","daily",""),
    ("roe","profitability","fina_indicator",""),("roa","profitability","fina_indicator",""),
    ("grossprofit_margin","profitability","fina_indicator",""),("net_margin","profitability","fina_indicator",""),
    ("debt_to_assets","leverage","balancesheet",""),("current_ratio","liquidity","balancesheet",""),
    ("net_income","financial_stmt","income",""),("revenue","financial_stmt","income",""),
    ("total_assets","financial_stmt","balancesheet",""),("equity","financial_stmt","balancesheet",""),
    ("op_cashflow","financial_stmt","cashflow",""),("inventory","financial_stmt","balancesheet",""),
    ("ar","financial_stmt","balancesheet",""),
    ("margin_balance","margin","margin_detail",""),("margin_buy_amount","margin","margin_detail",""),
    ("margin_repay_amount","margin","margin_detail",""),
    ("margin_total_balance","margin","margin_detail",""),
    ("lend_volume","margin","margin_detail",""),("lend_sell_volume","margin","margin_detail",""),
    ("lend_repay_volume","margin","margin_detail",""),
    ("industry","classification","daily",""),("float_shares","shares","daily",""),
    ("holder_num","shareholder","parquet","external"),("top10_holder_ratio","shareholder","parquet","external"),
]
for name, grp, tbl, note in RAW_FEATURES:
    pit = "point_in_time" if tbl in ("fina_indicator","income","balancesheet","cashflow","margin_detail","parquet") else "daily"
    all_features.append({
        "feature_id": name, "feature_name": name, "feature_group": grp, "kind": "raw",
        "source_table": tbl, "dependencies": "", "compute_fn": "",
        "pit_rule": pit, "cacheable": "no", "formula": "",
        "yaml_refs": "", "registry_refs": "qlib_adapter", "status": "active", "notes": note,
    })

# YAML refs
yaml_dir = REPO / "configs" / "features"
yaml_refs = {f["feature_name"]: [] for f in all_features}
for yaml_path in sorted(yaml_dir.glob("*.yaml")):
    fl_id = yaml_path.stem
    try:
        data = yaml.safe_load(yaml_path.read_text())
        for f in (data.get("features") or []):
            if f in yaml_refs:
                yaml_refs[f].append(fl_id)
    except: pass

for feat in all_features:
    feat["yaml_refs"] = "; ".join(sorted(set(yaml_refs.get(feat["feature_name"], []))))

# PIT rules
PIT_RULES = {
    "ret_1d":"rolling_past","ret_3d":"rolling_past","ret_5d":"rolling_past",
    "ret_20d":"rolling_past","ret_60d":"rolling_past","ret_120d":"rolling_past",
    "volume_ratio_20d":"rolling_past","volume_ratio_60d":"rolling_past",
    "distance_to_120d_high":"rolling_past","distance_to_250d_high":"rolling_past",
    "up_day_ratio_60d":"rolling_past","up_day_ratio_120d":"rolling_past",
    "trend_smoothness_60d":"rolling_past","trend_smoothness_120d":"rolling_past",
    "max_pullback_120d":"rolling_past",
    "volatility_adjusted_return_60d":"rolling_past","volatility_adjusted_return_120d":"rolling_past",
    "rps_60d":"cross_sectional","rps_120d":"cross_sectional","rps_20d":"cross_sectional",
    "rps_20d_minus_rps_60d":"cross_sectional",
    "rps_industry_60d":"cross_sectional","rps_industry_120d":"cross_sectional",
    "price_percentile_252d":"rolling_past","distance_to_252d_low":"rolling_past",
    "volume_up_down_ratio_60d":"rolling_past","volume_up_down_ratio_120d":"rolling_past",
    "above_avg_volume_ratio_60d":"rolling_past",
    "amount_ratio_20d":"rolling_past","amount_ratio_60d":"rolling_past",
    "volume_spike_20d":"rolling_past","volume_stability_60d":"rolling_past",
    "roe_delta_252d":"rolling_past","grossprofit_margin_delta_252d":"rolling_past",
    "debt_to_assets_delta_252d":"rolling_past","op_cashflow_delta_252d":"rolling_past",
    "pe_delta_120d":"rolling_past","pb_delta_120d":"rolling_past",
    "pe_rank_252d":"cross_sectional","pb_rank_252d":"cross_sectional",
    "pe_percentile_756d":"cross_sectional","pb_percentile_756d":"cross_sectional",
    "pe_distance_from_756d_low":"rolling_past","pb_distance_from_756d_low":"rolling_past",
    "pe_repair_room_to_median":"cross_sectional","pb_repair_room_to_median":"cross_sectional",
    "earnings_yield_proxy":"point_in_time","peg_proxy":"point_in_time",
    "revenue_yoy":"point_in_time","profit_yoy":"point_in_time",
    "inventory_yoy":"point_in_time","ar_yoy":"point_in_time",
    "revenue_yoy_accel":"point_in_time","profit_yoy_accel":"point_in_time",
    "roe_delta_756d":"rolling_past","net_margin_delta_756d":"rolling_past",
    "ocf_margin":"point_in_time",
    "margin_eligible":"point_in_time","margin_balance_to_float_mv":"point_in_time",
    "margin_balance_chg_20d":"rolling_past","margin_balance_chg_60d":"rolling_past",
    "margin_buy_intensity_20d":"rolling_past","margin_repay_to_buy_20d":"rolling_past",
    "margin_crowding_score":"cross_sectional","margin_trend_confirm_score":"cross_sectional",
    "margin_overheat_risk_score":"cross_sectional",
    "holder_num_chg_qoq":"point_in_time","holder_num_chg_2q":"point_in_time",
    "avg_shares_per_holder_chg_qoq":"point_in_time","top10_holder_ratio_chg_qoq":"point_in_time",
    "holder_concentration_score":"point_in_time","holder_squeeze_score":"point_in_time",
    "holder_price_confirm_score":"point_in_time","holder_num_stale_days":"point_in_time",
    "top10_holder_stale_days":"point_in_time",
    "trend_consistency_60d":"rolling_past","trend_consistency_120d":"rolling_past",
    "low_vol_uptrend_60d":"rolling_past","low_vol_uptrend_120d":"rolling_past",
    "return_drawdown_ratio_60d":"rolling_past","return_drawdown_ratio_120d":"rolling_past",
    "pullback_recovery_speed_60d":"rolling_past","new_high_persistence_120d":"rolling_past",
    "up_volume_down_volume_ratio_60d":"rolling_past","up_volume_down_volume_ratio_120d":"rolling_past",
    "volume_contraction_after_rise_60d":"rolling_past","quiet_accumulation_60d":"rolling_past",
    "amount_stability_60d":"rolling_past","breakout_volume_quality_120d":"rolling_past",
    "holder_concentration_trend_confirm":"cross_sectional","holder_concentration_low_vol_uptrend":"cross_sectional",
    "holder_concentration_volume_contract":"cross_sectional","margin_holder_trend_confirm":"cross_sectional",
    "margin_pullback_recovery_confirm":"cross_sectional",
    "industry_ret_1d":"cross_sectional","industry_ret_3d":"cross_sectional",
    "industry_ret_5d":"cross_sectional","industry_breadth":"cross_sectional",
    "stock_minus_industry_ret":"cross_sectional","stock_minus_industry_ret_3d":"cross_sectional",
    "stock_minus_industry_ret_5d":"cross_sectional",
    "industry_ret_20d":"cross_sectional","industry_ret_60d":"cross_sectional","industry_ret_120d":"cross_sectional",
    "industry_breadth_20d":"cross_sectional","industry_breadth_60d":"cross_sectional",
    "industry_new_high_ratio":"cross_sectional","industry_top_stock_momentum":"cross_sectional",
    "industry_volume_expansion":"cross_sectional",
    "stock_minus_industry_ret_20d":"cross_sectional","stock_minus_industry_ret_60d":"cross_sectional",
    "stock_industry_ret_corr_60d":"rolling_past",
    "continuation_candidate_score":"cross_sectional","repair_candidate_score":"cross_sectional",
    "overheat_risk_score":"cross_sectional","value_trap_risk_score":"cross_sectional",
    "industry_ret_1d":"cross_sectional","industry_ret_3d":"cross_sectional",
    "industry_ret_5d":"cross_sectional","industry_breadth":"cross_sectional",
}

for feat in all_features:
    if feat["kind"] == "derived":
        feat["pit_rule"] = PIT_RULES.get(feat["feature_name"], "rolling_past")
    src = feat.get("source_table", "")
    if feat["kind"] == "raw" and src in ("fina_indicator","income","balancesheet","cashflow","margin_detail","parquet"):
        feat["pit_rule"] = "point_in_time"

# write CSV
fieldnames = ["feature_id","feature_name","feature_group","kind","source_table",
              "dependencies","compute_fn","pit_rule","cacheable","formula",
              "yaml_refs","registry_refs","status","notes"]

with open(output_dir / "feature_inventory.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for feat in all_features:
        w.writerow(feat)

raw_count = sum(1 for f in all_features if f["kind"]=="raw")
derived_count = sum(1 for f in all_features if f["kind"]=="derived")
print(f"Written {len(all_features)} rows ({raw_count} raw, {derived_count} derived, {len(FEATURE_GROUPS)} groups)")
print(f"→ {output_dir / 'feature_inventory.csv'}")
