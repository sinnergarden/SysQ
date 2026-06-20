from __future__ import annotations

import hashlib
from typing import Any

from qsys.feature.registry import FEATURE_GROUPS

# ── Resolvability classification ──

NON_RESOLVABLE: set[str] = {
    "v3a_margin",
    "v3a_shareholder",
}
"""Feature groups that require explicit config flags and external data sources.

These groups cannot be auto-resolved from a group name alone because they
depend on adapter auto-detection or external data loading (e.g. margin
financing data, shareholder register data).
"""

RESOLVABLE_GROUPS: list[str] = [
    name for name in FEATURE_GROUPS if name not in NON_RESOLVABLE
]
"""All feature groups that *can* be auto-resolved from a config listing.

Equal to all registered groups minus ``NON_RESOLVABLE``.
"""


# ── Resolution helper ──

def _expand_group(group_name: str) -> list[str]:
    """Return the feature list for *group_name*, or raise ``KeyError``."""
    entry = FEATURE_GROUPS.get(group_name)
    if entry is None:
        raise KeyError(
            f"Unknown feature group: '{group_name}'. "
            f"Available: {list(FEATURE_GROUPS)}"
        )
    return list(entry.get("features", []))


def resolve_feature_list(config: dict) -> list[str]:
    """Resolve a feature list from a config dictionary.

    The config may carry:

    * ``features`` -- an explicit list of feature names (backward-compat).
    * ``feature_groups`` -- group names to expand via the registry.

    Resolution rules
    ----------------
    * Only ``features`` → returned unchanged.
    * Only ``feature_groups`` → each group expanded, flattened, stable-order
      deduped.
    * Both ``features`` and ``feature_groups`` → union of explicit features
      and expanded groups, stable-order deduped (features first, then
      groups in the order they appear in the config).

    Parameters
    ----------
    config : dict
        Config dict with optional keys ``features`` (list[str]) and/or
        ``feature_groups`` (list[str]).

    Returns
    -------
    list[str]
        Resolved, deduplicated feature names in stable order.

    Raises
    ------
    KeyError
        If any group name in ``feature_groups`` is not registered.
    """
    explicit: list[str] = list(config.get("features", []))
    group_names: list[str] = list(config.get("feature_groups", []))

    if not group_names:
        # Only explicit (or empty) → backward compat fast path
        return explicit

    # Expand all requested groups
    expanded: list[str] = []
    for name in group_names:
        expanded.extend(_expand_group(name))

    if not explicit:
        # Only groups → deduped expanded list
        return _stable_dedupe(expanded)

    # Both explicit and groups → union, explicit first
    return _stable_dedupe(explicit + expanded)


def _stable_dedupe(items: list[str]) -> list[str]:
    """Remove duplicates while preserving first-occurrence order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ── Feature metadata (formulas + required raw fields) ──

# Formulas organised by group for readability.  Every feature in
# FEATURE_GROUPS should appear in this dict or be covered by a group-level
# fallback.
_FEATURE_FORMULAS: dict[str, str] = {
    # microstructure
    "close_to_open_gap_1d": "Close-to-open gap ratio (prev close / today open)",
    "open_to_close_ret": "Open-to-close intraday return",
    "close_pos_in_range": "Close position within daily high-low range",
    "open_pos_in_range": "Open position within daily high-low range",
    "upper_shadow_ratio": "Upper shadow length relative to daily range",
    "lower_shadow_ratio": "Lower shadow length relative to daily range",
    "intraday_reversal_strength": "Intraday reversal magnitude (|close - open| / range)",
    # liquidity
    "turnover_rate": "Share turnover rate (volume / free float shares)",
    "amount_log": "Log of trading amount",
    "amount_zscore_20": "Cross-sectional z-score of 20d avg amount",
    "volume_shock_3": "Volume ratio vs 3d average",
    "volume_shock_5": "Volume ratio vs 5d average",
    "turnover_acceleration": "Change in turnover rate vs previous period",
    "illiquidity": "Amihud illiquidity ratio (|ret| / amount)",
    # tradability
    "is_limit_up": "Binary: stock hit limit-up today",
    "is_limit_down": "Binary: stock hit limit-down today",
    "distance_to_limit_up": "Price distance to limit-up threshold",
    "distance_to_limit_down": "Price distance to limit-down threshold",
    "limit_up_count_5d": "Number of limit-up days in past 5 sessions",
    "tradability_score": "Composite tradability score",
    "opened_from_limit_up": "Binary: stock opened from limit-up state",
    # relative_strength (basic)
    "ret_1d": "1-day close return",
    "ret_3d": "3-day close return",
    "ret_5d": "5-day close return",
    "vol_mean_3d": "3-day average volume",
    "vol_mean_5d": "5-day average volume",
    "amount_mean_3d": "3-day average trading amount",
    "amount_mean_5d": "5-day average trading amount",
    "ret_1d_rank": "Cross-sectional rank of 1d return",
    "ret_3d_rank": "Cross-sectional rank of 3d return",
    "ret_5d_rank": "Cross-sectional rank of 5d return",
    "vol_mean_3d_rank": "Cross-sectional rank of 3d avg volume",
    "vol_mean_5d_rank": "Cross-sectional rank of 5d avg volume",
    "amount_mean_3d_rank": "Cross-sectional rank of 3d avg amount",
    "amount_mean_5d_rank": "Cross-sectional rank of 5d avg amount",
    "stock_minus_index_ret_3d": "Stock 3d return minus index 3d return",
    "stock_minus_index_ret_5d": "Stock 5d return minus index 5d return",
    "stock_minus_industry_ret_3d": "Stock 3d return minus industry 3d return",
    "stock_minus_industry_ret_5d": "Stock 5d return minus industry 5d return",
    # relative_strength (value-growth: market_confirmation)
    "ret_20d": "20-day close return",
    "ret_60d": "60-day close return",
    "ret_120d": "120-day close return",
    "volume_ratio_20d": "Volume ratio vs 20d average",
    "volume_ratio_60d": "Volume ratio vs 60d average",
    "distance_to_120d_high": "Price distance to 120-day high",
    "distance_to_250d_high": "Price distance to 250-day high",
    # relative_strength (v2: continuation_trend_quality)
    "up_day_ratio_60d": "Ratio of up days over 60d",
    "up_day_ratio_120d": "Ratio of up days over 120d",
    "trend_smoothness_60d": "Trend smoothness (regression R2) over 60d",
    "trend_smoothness_120d": "Trend smoothness (regression R2) over 120d",
    "max_pullback_120d": "Max peak-to-trough pullback over 120d",
    "volatility_adjusted_return_60d": "60d return divided by 60d volatility",
    "volatility_adjusted_return_120d": "120d return divided by 120d volatility",
    "rps_60d": "Relative Price Strength rank over 60d",
    "rps_120d": "Relative Price Strength rank over 120d",
    "rps_20d": "Relative Price Strength rank over 20d",
    "rps_20d_minus_rps_60d": "Short-term RPS minus medium-term RPS",
    "rps_industry_60d": "Industry-relative RPS over 60d",
    "rps_industry_120d": "Industry-relative RPS over 120d",
    "price_percentile_252d": "Current price percentile over 252d range",
    "distance_to_252d_low": "Price distance to 252-day low",
    # relative_strength (v2: volume_participation_quality)
    "volume_up_down_ratio_60d": "Up-volume to down-volume ratio over 60d",
    "above_avg_volume_ratio_60d": "Ratio of days above avg volume over 60d",
    "amount_ratio_20d": "Amount ratio vs 20d average",
    "amount_ratio_60d": "Amount ratio vs 60d average",
    "volume_spike_20d": "Volume spike intensity over 20d",
    "volume_stability_60d": "Volume stability (inverse CV) over 60d",
    # regime
    "market_breadth": "Fraction of stocks above 20d MA in index",
    "limit_up_breadth": "Fraction of index stocks hitting limit-up",
    "index_volatility_5": "Index 5-day realised volatility",
    "index_volatility_10": "Index 10-day realised volatility",
    "index_volatility_20": "Index 20-day realised volatility",
    "small_vs_large_strength": "Small-cap vs large-cap relative strength",
    "growth_vs_value_proxy": "Growth vs value style spread proxy",
    "market_trend_strength": "Index trend strength (slope / vol)",
    # industry_context
    "industry_ret_1d": "Industry-benchmark 1d return",
    "industry_ret_3d": "Industry-benchmark 3d return",
    "industry_ret_5d": "Industry-benchmark 5d return",
    "industry_breadth": "Fraction of industry stocks with positive return",
    "stock_minus_industry_ret": "Stock return minus industry return (1d)",
    # fundamental_context (general)
    "log_mktcap": "Log of total market capitalisation",
    "float_mktcap": "Float market capitalisation",
    "pe_ttm": "Price-to-earnings ratio (TTM)",
    "pb_raw": "Price-to-book ratio",
    "ps_ttm": "Price-to-sales ratio (TTM)",
    "roe": "Return on equity",
    "roa": "Return on assets",
    "gross_margin": "Gross profit margin",
    "net_margin": "Net profit margin",
    "operating_cf_to_profit": "Operating cash flow to net profit ratio",
    "debt_to_asset": "Total debt to total assets ratio",
    "revenue_yoy": "Revenue year-over-year growth",
    "profit_yoy": "Net profit year-over-year growth",
    "inventory_yoy": "Inventory year-over-year change",
    "ar_yoy": "Accounts receivable year-over-year change",
    # fundamental_context (value-growth: growth_quality)
    "roe_delta_252d": "252-day change in ROE",
    "grossprofit_margin_delta_252d": "252-day change in gross profit margin",
    "debt_to_assets_delta_252d": "252-day change in debt-to-assets ratio",
    "op_cashflow_delta_252d": "252-day change in operating cash flow",
    # fundamental_context (value-growth: valuation_repair)
    "pe_rank_252d": "252-day percentile rank of PE",
    "pb_rank_252d": "252-day percentile rank of PB",
    "pe_delta_120d": "120-day change in PE",
    "pb_delta_120d": "120-day change in PB",
    # fundamental_context (v2: valuation_repair_setup)
    "pe_percentile_756d": "756-day percentile rank of PE",
    "pb_percentile_756d": "756-day percentile rank of PB",
    "pe_distance_from_756d_low": "PE distance from 756-day low",
    "pb_distance_from_756d_low": "PB distance from 756-day low",
    "pe_repair_room_to_median": "PE upside room to 756d median",
    "pb_repair_room_to_median": "PB upside room to 756d median",
    "earnings_yield_proxy": "Earnings yield proxy (1 / PE)",
    "peg_proxy": "PEG ratio proxy (PE / earnings growth)",
    # fundamental_context (v2: fundamental_acceleration)
    "revenue_yoy_accel": "Revenue YoY acceleration (delta of YoY rate)",
    "profit_yoy_accel": "Profit YoY acceleration (delta of YoY rate)",
    "roe_delta_756d": "756-day change in ROE",
    "net_margin_delta_756d": "756-day change in net margin",
    "ocf_margin": "Operating cash flow margin",
    # fundamental_context (v2: path_classifier_scores)
    "continuation_candidate_score": "Continuation trend candidate score",
    "repair_candidate_score": "Repair / turnaround candidate score",
    "overheat_risk_score": "Overheat / exhaustion risk score",
    "value_trap_risk_score": "Value trap risk score",
    # v3a_margin
    "margin_eligible": "Binary: stock eligible for margin trading",
    "margin_balance_to_float_mv": "Margin balance to float market value ratio",
    "margin_balance_chg_20d": "20-day change in margin balance",
    "margin_balance_chg_60d": "60-day change in margin balance",
    "margin_buy_intensity_20d": "20-day margin buy intensity",
    "margin_repay_to_buy_20d": "20-day repayment-to-purchase ratio",
    "margin_crowding_score": "Margin crowding score",
    "margin_trend_confirm_score": "Margin trend confirmation score",
    "margin_overheat_risk_score": "Margin overheat risk score",
    # v3a_shareholder
    "holder_num_chg_qoq": "QoQ change in number of shareholders",
    "holder_num_chg_2q": "Change in shareholder count over 2 quarters",
    "avg_shares_per_holder_chg_qoq": "QoQ change in avg shares per holder",
    "top10_holder_ratio_chg_qoq": "QoQ change in top-10 holder ratio",
    "holder_concentration_score": "Shareholder concentration score",
    "holder_squeeze_score": "Holder squeeze (tightening) score",
    "holder_price_confirm_score": "Holder concentration and price trend confirmation",
    "holder_num_stale_days": "Days since last shareholder data update",
    "top10_holder_stale_days": "Days since last top-10 holder data update",
    "top10_holder_ratio": "Top-10 holder ownership ratio",
    # v3b_price_volume
    "trend_consistency_60d": "Trend consistency score over 60d",
    "trend_consistency_120d": "Trend consistency score over 120d",
    "low_vol_uptrend_60d": "Low-volatility uptrend score over 60d",
    "low_vol_uptrend_120d": "Low-volatility uptrend score over 120d",
    "return_drawdown_ratio_60d": "Return-to-max-drawdown ratio over 60d",
    "return_drawdown_ratio_120d": "Return-to-max-drawdown ratio over 120d",
    "pullback_recovery_speed_60d": "Pullback recovery speed over 60d",
    "new_high_persistence_120d": "New-high persistence over 120d",
    "up_volume_down_volume_ratio_60d": "Up-volume to down-volume ratio over 60d",
    "up_volume_down_volume_ratio_120d": "Up-volume to down-volume ratio over 120d",
    "volume_contraction_after_rise_60d": "Volume contraction after rise over 60d",
    "quiet_accumulation_60d": "Quiet accumulation score over 60d",
    "amount_stability_60d": "Amount stability (inverse CV) over 60d",
    "breakout_volume_quality_120d": "Breakout volume quality score over 120d",
    # v3b_interaction
    "holder_concentration_trend_confirm": "Holder concentration × trend confirmation",
    "holder_concentration_low_vol_uptrend": "Holder concentration × low-vol uptrend",
    "holder_concentration_volume_contract": "Holder concentration × volume contraction",
    "margin_holder_trend_confirm": "Margin × holder × trend confirmation",
    "margin_pullback_recovery_confirm": "Margin × pullback recovery confirmation",
}

# Required raw/input fields per feature (group-level defaults with
# feature-specific overrides).
_REQUIRED_FIELDS: dict[str, list[str]] = {
    # ── microstructure (overrides) ──
    "close_to_open_gap_1d": ["close", "open"],
    "open_to_close_ret": ["open", "close"],
    "close_pos_in_range": ["close", "high", "low"],
    "open_pos_in_range": ["open", "high", "low"],
    "upper_shadow_ratio": ["high", "close", "open", "low"],
    "lower_shadow_ratio": ["high", "close", "open", "low"],
    "intraday_reversal_strength": ["close", "open", "high", "low"],
    # ── liquidity (overrides) ──
    "turnover_rate": ["volume", "amount", "float_shares"],
    "amount_log": ["amount"],
    "amount_zscore_20": ["amount"],
    "volume_shock_3": ["volume"],
    "volume_shock_5": ["volume"],
    "turnover_acceleration": ["volume", "amount", "float_shares"],
    "illiquidity": ["amount", "close"],
    # ── tradability (overrides) ──
    "is_limit_up": ["close", "high_limit"],
    "is_limit_down": ["close", "low_limit"],
    "distance_to_limit_up": ["close", "high_limit"],
    "distance_to_limit_down": ["close", "low_limit"],
    "limit_up_count_5d": ["close", "high_limit"],
    "tradability_score": ["close", "high_limit", "low_limit", "volume"],
    "opened_from_limit_up": ["open", "high_limit", "close"],
    # ── regime (overrides) ──
    "market_breadth": ["index_close", "index_ma20"],
    "limit_up_breadth": ["index_limit_up_count", "index_constituents"],
    "index_volatility_5": ["index_close"],
    "index_volatility_10": ["index_close"],
    "index_volatility_20": ["index_close"],
    "small_vs_large_strength": ["small_index_close", "large_index_close"],
    "growth_vs_value_proxy": ["growth_index_close", "value_index_close"],
    "market_trend_strength": ["index_close"],
    # ── industry_context (overrides) ──
    "industry_ret_1d": ["close", "industry_code"],
    "industry_ret_3d": ["close", "industry_code"],
    "industry_ret_5d": ["close", "industry_code"],
    "industry_breadth": ["close", "industry_code"],
    "stock_minus_industry_ret": ["close", "industry_code"],
    "stock_minus_industry_ret_3d": ["close", "industry_code"],
    "stock_minus_industry_ret_5d": ["close", "industry_code"],
    # ── fundamental_context (overrides) ──
    "log_mktcap": ["total_mv"],
    "float_mktcap": ["circ_mv"],
    "pe_ttm": ["pe"],
    "pb_raw": ["pb"],
    "ps_ttm": ["ps"],
    "roe": ["roe"],
    "roa": ["roa"],
    "gross_margin": ["grossprofit_margin"],
    "net_margin": ["net_margin"],
    "operating_cf_to_profit": ["op_cashflow", "net_income"],
    "debt_to_asset": ["debt_to_assets"],
    "revenue_yoy": ["revenue"],
    "profit_yoy": ["net_income"],
    "inventory_yoy": ["inventory"],
    "ar_yoy": ["ar"],
    "roe_delta_252d": ["roe"],
    "grossprofit_margin_delta_252d": ["grossprofit_margin"],
    "debt_to_assets_delta_252d": ["debt_to_assets"],
    "op_cashflow_delta_252d": ["op_cashflow"],
    "pe_rank_252d": ["pe"],
    "pb_rank_252d": ["pb"],
    "pe_delta_120d": ["pe"],
    "pb_delta_120d": ["pb"],
    "pe_percentile_756d": ["pe"],
    "pb_percentile_756d": ["pb"],
    "pe_distance_from_756d_low": ["pe"],
    "pb_distance_from_756d_low": ["pb"],
    "pe_repair_room_to_median": ["pe"],
    "pb_repair_room_to_median": ["pb"],
    "earnings_yield_proxy": ["pe"],
    "peg_proxy": ["pe", "profit_yoy"],
    "revenue_yoy_accel": ["revenue"],
    "profit_yoy_accel": ["net_income"],
    "roe_delta_756d": ["roe"],
    "net_margin_delta_756d": ["net_margin"],
    "ocf_margin": ["op_cashflow", "revenue"],
    "continuation_candidate_score": ["close", "volume", "roe", "pe"],
    "repair_candidate_score": ["close", "volume", "roe", "pb"],
    "overheat_risk_score": ["close", "volume", "margin_balance"],
    "value_trap_risk_score": ["close", "volume", "pe", "pb", "debt_to_assets"],
    # ── v3a_margin (overrides) ──
    "margin_eligible": ["margin_eligible_flag"],
    "margin_balance_to_float_mv": ["margin_balance", "circ_mv"],
    "margin_balance_chg_20d": ["margin_balance"],
    "margin_balance_chg_60d": ["margin_balance"],
    "margin_buy_intensity_20d": ["margin_buy_amount"],
    "margin_repay_to_buy_20d": ["margin_repay_amount", "margin_buy_amount"],
    "margin_crowding_score": ["margin_balance", "circ_mv", "margin_buy_amount"],
    "margin_trend_confirm_score": ["margin_balance", "close"],
    "margin_overheat_risk_score": ["margin_balance", "margin_buy_amount", "close"],
    # ── v3a_shareholder (overrides) ──
    "holder_num_chg_qoq": ["holder_num"],
    "holder_num_chg_2q": ["holder_num"],
    "avg_shares_per_holder_chg_qoq": ["avg_shares_per_holder"],
    "top10_holder_ratio_chg_qoq": ["top10_holder_ratio"],
    "holder_concentration_score": ["top10_holder_ratio", "holder_num"],
    "holder_squeeze_score": ["holder_num", "avg_shares_per_holder"],
    "holder_price_confirm_score": ["holder_num", "close"],
    "holder_num_stale_days": ["holder_num"],
    "top10_holder_stale_days": ["top10_holder_ratio"],
    "top10_holder_ratio": ["top10_holder_ratio"],
    # ── v3b_price_volume (overrides) ──
    "trend_consistency_60d": ["close"],
    "trend_consistency_120d": ["close"],
    "low_vol_uptrend_60d": ["close"],
    "low_vol_uptrend_120d": ["close"],
    "return_drawdown_ratio_60d": ["close"],
    "return_drawdown_ratio_120d": ["close"],
    "pullback_recovery_speed_60d": ["close"],
    "new_high_persistence_120d": ["close"],
    "up_volume_down_volume_ratio_60d": ["close", "volume"],
    "up_volume_down_volume_ratio_120d": ["close", "volume"],
    "volume_contraction_after_rise_60d": ["close", "volume"],
    "quiet_accumulation_60d": ["close", "volume"],
    "amount_stability_60d": ["amount"],
    "breakout_volume_quality_120d": ["close", "volume"],
    # ── v3b_interaction (overrides) ──
    "holder_concentration_trend_confirm": ["top10_holder_ratio", "close"],
    "holder_concentration_low_vol_uptrend": ["top10_holder_ratio", "close", "volume"],
    "holder_concentration_volume_contract": ["top10_holder_ratio", "volume"],
    "margin_holder_trend_confirm": ["margin_balance", "top10_holder_ratio", "close"],
    "margin_pullback_recovery_confirm": ["margin_balance", "close"],
}

# Group-level default required fields (used as fallback when no
# feature-specific override exists).
_GROUP_DEFAULT_FIELDS: dict[str, list[str]] = {
    "microstructure": ["open", "close", "high", "low"],
    "liquidity": ["volume", "amount"],
    "tradability": ["close", "high_limit", "low_limit"],
    "relative_strength": ["close", "volume", "amount"],
    "regime": ["index_close"],
    "industry_context": ["close", "industry_code"],
    "fundamental_context": [
        "total_mv", "circ_mv", "pe", "pb", "roe", "revenue", "net_income",
    ],
    "v3a_margin": [
        "margin_balance", "margin_buy_amount", "margin_repay_amount",
    ],
    "v3a_shareholder": [
        "holder_num", "avg_shares_per_holder", "top10_holder_ratio",
    ],
    "v3b_price_volume": ["close", "volume", "amount"],
    "v3b_interaction": [
        "margin_balance", "top10_holder_ratio", "close", "volume",
    ],
}


def _formula(feature_name: str, group_name: str | None = None) -> str:
    """Return a brief formula/description for *feature_name*."""
    desc = _FEATURE_FORMULAS.get(feature_name)
    if desc is not None:
        return desc
    if group_name:
        return f"{group_name.replace('_', ' ').title()} feature: {feature_name}"
    return feature_name


def _required_fields(feature_name: str, group_name: str | None = None) -> list[str]:
    """Return the list of required raw data fields for *feature_name*."""
    fields = _REQUIRED_FIELDS.get(feature_name)
    if fields is not None:
        return list(fields)
    if group_name:
        default = _GROUP_DEFAULT_FIELDS.get(group_name, [])
        return list(default)
    return []


def _feature_schema_hash(features: list[str]) -> str:
    """Return a deterministic hash of the sorted feature names list."""
    raw = ",".join(sorted(features))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_feature_manifest(
    features: list[str],
    expansions: dict[str, list[str]],
) -> list[dict[str, Any]]:
    """Build a per-feature manifest from a resolved feature list.

    Parameters
    ----------
    features : list[str]
        The resolved, ordered list of feature names (output of
        ``resolve_feature_list``).
    expansions : dict[str, list[str]]
        Mapping from group name to the list of feature names that group
        contributed.  Groups not present in this dict are treated as
        ``status="skipped"`` (unless all their features are absent, in which
        case they are simply not reported).

    Returns
    -------
    list[dict[str, Any]]
        One entry per feature with keys:

        * ``feature_name`` — the feature name.
        * ``group`` — the originating group name, or ``"explicit"``.
        * ``formula`` — brief human-readable description.
        * ``required_fields`` — list of raw data dependencies.
        * ``status`` — ``"existing"`` (in original explicit list),
          ``"added"`` (added by group expansion), or ``"skipped"`` (not
          resolved).
        * ``skip_reason`` — reason string if ``status == "skipped"``, else
          ``None``.
        * ``feature_schema_hash`` — deterministic hash of the *entire*
          sorted feature list (same for all entries in one manifest).
    """
    schema_hash = _feature_schema_hash(features)
    features_set: set[str] = set(features)

    # Build a reverse lookup for features present in BOTH the expansions
    # mapping AND the final resolved list.  When a feature belongs to
    # multiple groups the first group wins, matching stable-order dedup.
    feature_to_group: dict[str, str] = {}
    for group_name, group_features in expansions.items():
        for feat in group_features:
            if feat in features_set and feat not in feature_to_group:
                feature_to_group[feat] = group_name

    manifest: list[dict[str, Any]] = []

    for feat in features:
        group = feature_to_group.get(feat)
        if group is not None:
            status = "added"
            skip_reason: str | None = None
        else:
            status = "existing"
            skip_reason = None

        manifest.append(
            {
                "feature_name": feat,
                "group": group or "explicit",
                "formula": _formula(feat, group),
                "required_fields": _required_fields(feat, group),
                "status": status,
                "skip_reason": skip_reason,
                "feature_schema_hash": schema_hash,
            }
        )

    # Append "skipped" entries for features that were listed in an
    # expansion group but did NOT make it into the final resolved list
    # (e.g. the group is in NON_RESOLVABLE or could not be expanded).
    for group_name, group_features in expansions.items():
        for feat in group_features:
            if feat not in features_set:
                manifest.append(
                    {
                        "feature_name": feat,
                        "group": group_name,
                        "formula": _formula(feat, group_name),
                        "required_fields": _required_fields(feat, group_name),
                        "status": "skipped",
                        "skip_reason": f"Group '{group_name}' could not be resolved",
                        "feature_schema_hash": schema_hash,
                    }
                )

    return manifest
