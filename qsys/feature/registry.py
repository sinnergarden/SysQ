from __future__ import annotations

FEATURE_GROUPS = {
    "microstructure": {
        "enabled_by": "enable_microstructure_features",
        "features": [
            "close_to_open_gap_1d",
            "open_to_close_ret",
            "close_pos_in_range",
            "open_pos_in_range",
            "upper_shadow_ratio",
            "lower_shadow_ratio",
            "intraday_reversal_strength",
        ],
    },
    "liquidity": {
        "enabled_by": "enable_liquidity_features",
        "features": [
            "turnover_rate",
            "amount_log",
            "amount_zscore_20",
            "volume_shock_3",
            "volume_shock_5",
            "turnover_acceleration",
            "illiquidity",
        ],
    },
    "tradability": {
        "enabled_by": "enable_tradability_features",
        "features": [
            "is_limit_up",
            "is_limit_down",
            "distance_to_limit_up",
            "distance_to_limit_down",
            "limit_up_count_5d",
            "tradability_score",
            "opened_from_limit_up",
        ],
    },
    "relative_strength": {
        "enabled_by": "enable_relative_strength_features",
        "features": [
            "ret_1d",
            "ret_3d",
            "ret_5d",
            "vol_mean_3d",
            "vol_mean_5d",
            "amount_mean_3d",
            "amount_mean_5d",
            "ret_1d_rank",
            "ret_3d_rank",
            "ret_5d_rank",
            "vol_mean_3d_rank",
            "vol_mean_5d_rank",
            "amount_mean_3d_rank",
            "amount_mean_5d_rank",
            "stock_minus_index_ret_3d",
            "stock_minus_index_ret_5d",
            "stock_minus_industry_ret_3d",
            "stock_minus_industry_ret_5d",
        ],
    },
    "regime": {
        "enabled_by": "enable_regime_features",
        "features": [
            "market_breadth",
            "limit_up_breadth",
            "index_volatility_5",
            "index_volatility_10",
            "index_volatility_20",
            "small_vs_large_strength",
            "growth_vs_value_proxy",
            "market_trend_strength",
        ],
    },
    "industry_context": {
        "enabled_by": "enable_industry_context_features",
        "features": [
            "industry_ret_1d",
            "industry_ret_3d",
            "industry_ret_5d",
            "industry_breadth",
            "stock_minus_industry_ret",
            "stock_minus_industry_ret_3d",
            "stock_minus_industry_ret_5d",
        ],
    },
    "fundamental_context": {
        "enabled_by": "enable_fundamental_context_features",
        "features": [
            "log_mktcap",
            "float_mktcap",
            "pe_ttm",
            "pb_raw",
            "ps_ttm",
            "roe",
            "roa",
            "gross_margin",
            "net_margin",
            "operating_cf_to_profit",
            "debt_to_asset",
            "revenue_yoy",
            "profit_yoy",
            "inventory_yoy",
            "ar_yoy",
        ],
    },
}


def list_feature_groups() -> dict:
    return FEATURE_GROUPS
