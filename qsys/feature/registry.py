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
    """Load feature lists from ``configs/features/<feature_list_id>.yaml``.

    Usage::

        feats = FeatureListRegistry.load("momentum_price_volume_v1")
    """

    _CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs" / "features"

    @classmethod
    def list_ids(cls) -> list[str]:
        """Return all available feature list IDs."""
        if not cls._CONFIG_DIR.exists():
            return []
        return sorted(p.stem for p in cls._CONFIG_DIR.glob("*.yaml") if p.stem != "__init__")

    @classmethod
    def load(cls, feature_list_id: str) -> list[str]:
        """Load a feature list from ``configs/features/<feature_list_id>.yaml``.

        Returns a list of feature short names (resolved by
        ``get_feature_fields`` if needed).

        Raises ``FileNotFoundError`` if the file does not exist.
        """
        path = cls._CONFIG_DIR / f"{feature_list_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(
                f"Feature list '{feature_list_id}' not found at {path}. "
                f"Available: {cls.list_ids()}"
            )
        import yaml  # noqa: PLC0415
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Feature list YAML must be a dict, got {type(data).__name__}")
        features: list[str] = data.get("features", [])
        if not isinstance(features, list):
            raise ValueError(f"Feature list YAML 'features' must be a list, got {type(features).__name__}")
        # Resolve each entry: if it's a known named set, expand; otherwise keep as-is
        resolved: list[str] = []
        for f in features:
            if isinstance(f, str) and f.startswith("$"):
                # Named reference, e.g. "$semantic_all_features"
                resolved.extend(get_feature_fields(f[1:]))
            else:
                resolved.append(str(f))
        # Deduplicate preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for f in resolved:
            if f not in seen:
                seen.add(f)
                unique.append(f)
        return unique
