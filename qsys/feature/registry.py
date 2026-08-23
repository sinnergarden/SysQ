from __future__ import annotations

import hashlib
import json
import string
from pathlib import Path
from typing import Any

FEATURE_GROUPS = {
    "growth_confirmation_v0": {
        "enabled_by": "enable_growth_confirmation_features",
        "features": [
            # forecast
            "forecast_type_score",
            "forecast_stale_days",
            "has_forecast",
            # financial (income-based)
            "ttm_revenue_yoy",
            "single_q_revenue_yoy",
            "is_profitable_ttm",
            "gross_margin_delta_yoy",
            # breakout
            "breakout_252d_high",
            "days_since_252d_high",
        ],
    },
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
            "amount_log_ind_zscore",
            "turnover_rate_ind_zscore",
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
    "industry_momentum": {
        "enabled_by": "enable_industry_momentum_features",
        "features": [
            "industry_ret_20d",
            "industry_ret_60d",
            "industry_ret_120d",
            "industry_breadth_20d",
            "industry_breadth_60d",
            "industry_new_high_ratio",
            "industry_top_stock_momentum",
            "industry_volume_expansion",
            "stock_minus_industry_ret_20d",
            "stock_minus_industry_ret_60d",
            "stock_industry_ret_corr_60d",
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
        return list(cls.contract(feature_list_id)["features"])

    @classmethod
    def contract(cls, feature_list_id: str) -> dict[str, Any]:
        """Load and validate a feature list's immutable content contract.

        ``features_sha256`` always binds the ordered expressions.  Configs that
        additionally declare ``feature_count`` or ``source_artifact_sha256``
        are checked fail-closed so a stale/tampered list cannot be resumed
        under the same ``feature_list_id``.
        """
        path = cls._CONFIG_DIR / f"{feature_list_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Feature list '{feature_list_id}' not found. Available: {cls.list_ids()}")
        import yaml
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"Feature list '{feature_list_id}' must be a mapping")

        declared_id = data.get("feature_list_id")
        if declared_id is not None and str(declared_id) != feature_list_id:
            raise ValueError(
                f"Feature list id mismatch: requested '{feature_list_id}', "
                f"file declares '{declared_id}'"
            )

        raw_features = data.get("features", [])
        if not isinstance(raw_features, list) or not all(
            isinstance(value, str) for value in raw_features
        ):
            raise TypeError(
                f"Feature list '{feature_list_id}' features must be list[str]"
            )
        features = list(raw_features)
        # Historical AlphaV1 ``features.json`` artifacts use this exact
        # serialization.  Version it explicitly instead of silently changing
        # the digest scheme in future code.
        canonicalization = "json_indent_2_utf8_ensure_ascii_true_v1"
        canonical = json.dumps(features, indent=2).encode("utf-8")
        features_sha256 = hashlib.sha256(canonical).hexdigest()

        declared_count = data.get("feature_count")
        if declared_count is not None and type(declared_count) is not int:
            raise TypeError(
                f"Feature list '{feature_list_id}' feature_count must be int"
            )
        if declared_count is not None and declared_count != len(features):
            raise ValueError(
                f"Feature list '{feature_list_id}' count mismatch: "
                f"declared={declared_count}, actual={len(features)}"
            )
        declared_sha256 = data.get("source_artifact_sha256")
        if declared_sha256 is not None and (
            not isinstance(declared_sha256, str)
            or len(declared_sha256) != 64
            or any(char not in string.hexdigits for char in declared_sha256)
        ):
            raise TypeError(
                f"Feature list '{feature_list_id}' source_artifact_sha256 "
                "must be a 64-character hexadecimal string"
            )
        if declared_sha256 is not None and str(declared_sha256) != features_sha256:
            raise ValueError(
                f"Feature list '{feature_list_id}' SHA-256 mismatch: "
                f"declared={declared_sha256}, actual={features_sha256}"
            )

        return {
            "schema_version": "feature_list_content_contract_v1",
            "feature_list_id": feature_list_id,
            "feature_count": len(features),
            "features_sha256": features_sha256,
            "features_sha256_canonicalization": canonicalization,
            "feature_list_config_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "contract": data.get("contract"),
            "source_artifact": data.get("source_artifact"),
            "source_artifact_sha256": declared_sha256,
            "source_artifact_sha256_declared": declared_sha256 is not None,
            "features": features,
        }
