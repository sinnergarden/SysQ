from __future__ import annotations

from pathlib import Path
from typing import Any

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
            # value-growth: market_confirmation
            "ret_20d",
            "ret_60d",
            "ret_120d",
            "volume_ratio_20d",
            "volume_ratio_60d",
            "distance_to_120d_high",
            "distance_to_250d_high",
            # v2: continuation_trend_quality
            "up_day_ratio_60d",
            "up_day_ratio_120d",
            "trend_smoothness_60d",
            "trend_smoothness_120d",
            "max_pullback_120d",
            "volatility_adjusted_return_60d",
            "volatility_adjusted_return_120d",
            "rps_60d",
            "rps_120d",
            "rps_20d",
            "rps_20d_minus_rps_60d",
            "rps_industry_60d",
            "rps_industry_120d",
            "price_percentile_252d",
            "distance_to_252d_low",
            # v2: volume_participation_quality
            "volume_up_down_ratio_60d",
            "above_avg_volume_ratio_60d",
            "amount_ratio_20d",
            "amount_ratio_60d",
            "volume_spike_20d",
            "volume_stability_60d",
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
            # value-growth: growth_quality
            "roe_delta_252d",
            "grossprofit_margin_delta_252d",
            "debt_to_assets_delta_252d",
            "op_cashflow_delta_252d",
            # value-growth: valuation_repair
            "pe_rank_252d",
            "pb_rank_252d",
            "pe_delta_120d",
            "pb_delta_120d",
            # v2: valuation_repair_setup
            "pe_percentile_756d",
            "pb_percentile_756d",
            "pe_distance_from_756d_low",
            "pb_distance_from_756d_low",
            "pe_repair_room_to_median",
            "pb_repair_room_to_median",
            "earnings_yield_proxy",
            "peg_proxy",
            # v2: fundamental_acceleration
            "revenue_yoy_accel",
            "profit_yoy_accel",
            "roe_delta_756d",
            "net_margin_delta_756d",
            "ocf_margin",
            # v2: path_classifier_scores
            "continuation_candidate_score",
            "repair_candidate_score",
            "overheat_risk_score",
            "value_trap_risk_score",
        ],
    },
    "v3a_margin": {
        "enabled_by": "enable_v3a_margin_features",
        "features": [
            "margin_eligible",
            "margin_balance_to_float_mv",
            "margin_balance_chg_20d",
            "margin_balance_chg_60d",
            "margin_buy_intensity_20d",
            "margin_repay_to_buy_20d",
            "margin_crowding_score",
            "margin_trend_confirm_score",
            "margin_overheat_risk_score",
        ],
    },
    "v3a_shareholder": {
        "enabled_by": "enable_v3a_shareholder_features",
        "features": [
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
        ],
    },
    "v3b_price_volume": {
        "enabled_by": "enable_v3b_price_volume_features",
        "features": [
            "trend_consistency_60d",
            "trend_consistency_120d",
            "low_vol_uptrend_60d",
            "low_vol_uptrend_120d",
            "return_drawdown_ratio_60d",
            "return_drawdown_ratio_120d",
            "pullback_recovery_speed_60d",
            "new_high_persistence_120d",
            "up_volume_down_volume_ratio_60d",
            "up_volume_down_volume_ratio_120d",
            "volume_contraction_after_rise_60d",
            "quiet_accumulation_60d",
            "amount_stability_60d",
            "breakout_volume_quality_120d",
        ],
    },
    "v3b_interaction": {
        "enabled_by": "enable_v3b_interaction_features",
        "features": [
            "holder_concentration_trend_confirm",
            "holder_concentration_low_vol_uptrend",
            "holder_concentration_volume_contract",
            "margin_holder_trend_confirm",
            "margin_pullback_recovery_confirm",
        ],
    },
    "industry_relative": {
        "enabled_by": "enable_industry_relative_features",
        "features": [
            "industry_relative_roe",
            "industry_relative_revenue_yoy",
            "industry_relative_profit_yoy",
            "industry_relative_ocf_margin",
            "industry_relative_pe_cheapness",
            "industry_relative_pb_cheapness",
            "industry_relative_holder_chg",
            "industry_relative_top10_chg",
            "industry_relative_margin_crowding",
            "industry_relative_rps_60d",
            "industry_relative_rps_120d",
        ],
    },
    "neutralized": {
        "enabled_by": "enable_neutralized_features",
        "features": [
            "mktcap_neutral_ret_60d",
            "mktcap_neutral_ret_120d",
            "mktcap_neutral_roe",
            "mktcap_neutral_holder_score",
            "industry_size_neutral_ret_60d",
            "industry_size_neutral_ret_120d",
            "industry_size_neutral_roe",
            "industry_size_neutral_holder_score",
        ],
    },
    "shareholder_freshness_interaction": {
        "enabled_by": "enable_shareholder_freshness_interaction_features",
        "features": [
            "holder_decay_weight",
            "top10_decay_weight",
            "holder_concentration_score_decay",
            "holder_squeeze_score_decay",
            "fresh_holder_signal_40d",
            "fresh_holder_signal_80d",
            "holder_concentration_x_value",
            "holder_concentration_x_growth",
            "holder_concentration_x_industry_rps",
        ],
    },
}


def list_feature_groups() -> dict:
    return FEATURE_GROUPS


# ── Named feature set resolution ──

_FEATURE_SET_METHODS: dict[str, str] = {
    "semantic_all_features": "get_semantic_all_features_config",
    "semantic_all_features_absnorm": "get_semantic_all_features_absnorm_config",
    "semantic_no_regime_clean_v1": "get_semantic_no_regime_config",
}


def get_feature_fields(name: str) -> list[str]:
    """Return the flattened feature list for a named feature set.

    Known sets:
        "semantic_all_features"          — alpha158 + semantic groups
        "semantic_all_features_absnorm"  — same with absolute-value normalisation
        "semantic_no_regime_clean_v1"    — alpha v1 regime-free variant

    Delegates to ``FeatureLibrary`` class methods; falls back to
    ``FEATURE_GROUPS`` if *name* is a group key.
    """
    # Check registry groups first (e.g. "microstructure", "liquidity")
    if name in FEATURE_GROUPS:
        return list(FEATURE_GROUPS[name].get("features", []))

    # Resolve named sets via FeatureLibrary (lazy import to avoid cycles)
    method_name = _FEATURE_SET_METHODS.get(name)
    if method_name is not None:
        from qsys.feature.library import FeatureLibrary  # noqa: PLC0415

        method = getattr(FeatureLibrary, method_name, None)
        if method is None:
            raise KeyError(
                f"FeatureLibrary has no method '{method_name}' "
                f"(resolved from feature set '{name}')"
            )
        result = method()
        if not isinstance(result, list):
            raise TypeError(
                f"FeatureLibrary.{method_name}() returned {type(result).__name__}, "
                f"expected list[str]"
            )
        return result

    raise KeyError(
        f"Unknown feature set: '{name}'. "
        f"Known: {list(_FEATURE_SET_METHODS)} + {list(FEATURE_GROUPS)}"
    )


class FeatureListRegistry:
    """Load feature lists from ``configs/features/<feature_list_id>.yaml``."""

    _CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "features"

    @classmethod
    def list_ids(cls) -> list[str]:
        if not cls._CONFIG_DIR.exists():
            return []
        return sorted(p.stem for p in cls._CONFIG_DIR.glob("*.yaml") if p.stem != "__init__")

    @classmethod
    def load(cls, feature_list_id: str) -> list[str]:
        """Load feature list, return qlib field expressions."""
        path = cls._CONFIG_DIR / f"{feature_list_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Feature list '{feature_list_id}' not found. Available: {cls.list_ids()}")
        import yaml
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return list(data.get("features", []))
