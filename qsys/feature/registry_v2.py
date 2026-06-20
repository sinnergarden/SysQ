"""Feature registry v2 — unified, frozen metadata for all features.

This module coexists with ``registry.py`` (FEATURE_GROUPS dict) and does
NOT modify or replace it.  It adds:

- ``FeatureSpec`` — frozen dataclass for per-feature metadata.
- ``FEATURE_REGISTRY`` — all known features keyed by ``feature_id``.
- ``FEATURE_NAME_INDEX`` — ``name -> feature_id`` lookup.
- Lookup helpers (``get_feature``, ``get_active_features``, …).
- Consistency validation (``validate_registry``).

Design
------
- **feature_id** is permanent — once assigned, never changed or reused.
- **name** is the DataFrame column name — kept unchanged for YAML backward
  compatibility.
- **dependencies** are ``feature_id`` references (except raw fields, which
  use the column name).
- Status ``"broken"`` blocks production; ``"experimental"`` requires opt-in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

FeatureKind = Literal["raw", "derived"]
PitType = Literal["point_in_time", "rolling_past", "cross_sectional", "industry", "static"]
CacheScope = Literal["per_date", "per_instrument", "panel", "none"]
FeatureStatus = Literal["active", "experimental", "deprecated", "broken"]


@dataclass(frozen=True)
class FeatureSpec:
    """Immutable specification for a single feature.

    Parameters
    ----------
    feature_id:
        Permanent unique identifier (``{group}__{name}``).
    name:
        DataFrame column name (must match what the builder produces).
    group:
        Logical feature group (matches ``FEATURE_GROUPS`` key).
    kind:
        ``"raw"`` (from data source) or ``"derived"`` (computed).
    source:
        Data source table (for raw) or ``None``.
    dependencies:
        Tuple of feature_ids (for derived) or raw field names needed.
    compute_fn:
        Python dotted path to the compute function, or ``None`` for raw.
    pit_type:
        PIT classification.
    cache_scope:
        Cache granularity.
    status:
        Lifecycle status.
    description:
        Human-readable description.
    dtype:
        Expected pandas dtype (e.g. ``"float64"``, ``"object"``).
    owner:
        Person or team responsible.
    """

    feature_id: str
    name: str
    group: str
    kind: FeatureKind
    source: str | None
    dependencies: tuple[str, ...]
    compute_fn: str | None
    pit_type: PitType
    cache_scope: CacheScope
    status: FeatureStatus
    description: str
    dtype: str | None = None
    owner: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
# Registry: every active / experimental / deprecated / broken feature
# ═══════════════════════════════════════════════════════════════════════════
# feature_id = "{group}__{name}"
# Name column matches the legacy column name in builder output.

FEATURE_REGISTRY: dict[str, FeatureSpec] = {}

_NAME_INDEX: dict[str, str] = {}  # populated by _register_batch
_ID_INDEX: set[str] = set()


def _r(
    feature_id: str,
    name: str,
    group: str,
    kind: FeatureKind,
    source: str | None = None,
    dependencies: tuple[str, ...] = (),
    compute_fn: str | None = None,
    pit_type: PitType = "rolling_past",
    cache_scope: CacheScope = "none",
    status: FeatureStatus = "active",
    description: str = "",
    dtype: str | None = None,
    owner: str | None = None,
) -> None:
    """Register one FeatureSpec (mutable helper, dataclass is frozen)."""
    spec = FeatureSpec(
        feature_id=feature_id,
        name=name,
        group=group,
        kind=kind,
        source=source,
        dependencies=dependencies,
        compute_fn=compute_fn,
        pit_type=pit_type,
        cache_scope=cache_scope,
        status=status,
        description=description,
        dtype=dtype,
        owner=owner,
    )
    if feature_id in _ID_INDEX:
        raise ValueError(f"Duplicate feature_id: {feature_id}")
    _ID_INDEX.add(feature_id)
    # Name index: first registered name wins (for cross-group sharing).
    # E.g. stock_minus_industry_ret_3d exists in both relative_strength
    # and industry_context — the first registration sets the canonical name.
    if name not in _NAME_INDEX:
        _NAME_INDEX[name] = feature_id
    FEATURE_REGISTRY[feature_id] = spec


# ═══════════════════════════════════════════════════════════════════════════
# 1. Microstructure
# ═══════════════════════════════════════════════════════════════════════════
_MICRO = "microstructure"
_MICRO_FN = "groups.microstructure.build_microstructure_features"
_MICRO_DEPS = ("close", "open", "high", "low")
for _fid, _desc in [
    ("close_to_open_gap_1d", "Close-to-open gap ratio (prev close / today open)"),
    ("open_to_close_ret", "Open-to-close intraday return"),
    ("close_pos_in_range", "Close position within daily high-low range"),
    ("open_pos_in_range", "Open position within daily high-low range"),
    ("upper_shadow_ratio", "Upper shadow length relative to daily range"),
    ("lower_shadow_ratio", "Lower shadow length relative to daily range"),
    ("intraday_reversal_strength", "Intraday reversal magnitude"),
]:
    _r(f"{_MICRO}__{_fid}", _fid, _MICRO, "derived", dependencies=_MICRO_DEPS,
       compute_fn=_MICRO_FN, pit_type="rolling_past", cache_scope="none",
       description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 2. Liquidity
# ═══════════════════════════════════════════════════════════════════════════
_LIQ = "liquidity"
_LIQ_FN = "groups.liquidity.build_liquidity_features"
for _fid, _desc, _deps, _scope in [
    ("turnover_rate", "Share turnover rate (volume / free float)", ("volume", "float_shares"), "none"),
    ("amount_log", "Log of trading amount", ("amount",), "none"),
    ("amount_zscore_20", "Cross-sectional z-score of 20d avg amount", ("amount",), "none"),
    ("volume_shock_3", "Volume ratio vs 3d average", ("volume",), "none"),
    ("volume_shock_5", "Volume ratio vs 5d average", ("volume",), "none"),
    ("turnover_acceleration", "Change in turnover rate vs previous period", ("volume", "amount"), "none"),
    ("illiquidity", "Amihud illiquidity ratio", ("amount", "close"), "none"),
]:
    _r(f"{_LIQ}__{_fid}", _fid, _LIQ, "derived", dependencies=_deps,
       compute_fn=_LIQ_FN, pit_type="rolling_past", cache_scope=_scope,
       description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 3. Tradability
# ═══════════════════════════════════════════════════════════════════════════
_TRADE = "tradability"
_TRADE_FN = "groups.tradability.build_tradability_features"
for _fid, _desc, _deps in [
    ("is_limit_up", "Binary: stock hit limit-up today", ("close", "high_limit")),
    ("is_limit_down", "Binary: stock hit limit-down today", ("close", "low_limit")),
    ("distance_to_limit_up", "Price distance to limit-up threshold", ("close", "high_limit")),
    ("distance_to_limit_down", "Price distance to limit-down threshold", ("close", "low_limit")),
    ("limit_up_count_5d", "Number of limit-up days in past 5 sessions", ("close", "high_limit")),
    ("tradability_score", "Composite tradability score", ("close", "high_limit", "low_limit")),
    ("opened_from_limit_up", "Binary: stock opened from limit-up state", ("open", "high_limit", "close")),
]:
    _r(f"{_TRADE}__{_fid}", _fid, _TRADE, "derived", dependencies=_deps,
       compute_fn=_TRADE_FN, pit_type="rolling_past", cache_scope="none",
       description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 4. Relative Strength
# ═══════════════════════════════════════════════════════════════════════════
_RS = "relative_strength"
_RS_FN = "groups.relative_strength.build_relative_strength_features"
for _fid, _desc, _deps, _pit, _scope in [
    # Short-term returns
    ("ret_1d", "1-day close return", ("close",), "rolling_past", "none"),
    ("ret_3d", "3-day close return", ("close",), "rolling_past", "none"),
    ("ret_5d", "5-day close return", ("close",), "rolling_past", "none"),
    ("vol_mean_3d", "3-day average volume", ("volume",), "rolling_past", "none"),
    ("vol_mean_5d", "5-day average volume", ("volume",), "rolling_past", "none"),
    ("amount_mean_3d", "3-day average trading amount", ("amount",), "rolling_past", "none"),
    ("amount_mean_5d", "5-day average trading amount", ("amount",), "rolling_past", "none"),
    # Cross-sectional ranks
    ("ret_1d_rank", "Cross-sectional rank of 1d return", ("close",), "cross_sectional", "none"),
    ("ret_3d_rank", "Cross-sectional rank of 3d return", ("close",), "cross_sectional", "none"),
    ("ret_5d_rank", "Cross-sectional rank of 5d return", ("close",), "cross_sectional", "none"),
    ("vol_mean_3d_rank", "Cross-sectional rank of 3d avg volume", ("volume",), "cross_sectional", "none"),
    ("vol_mean_5d_rank", "Cross-sectional rank of 5d avg volume", ("volume",), "cross_sectional", "none"),
    ("amount_mean_3d_rank", "Cross-sectional rank of 3d avg amount", ("amount",), "cross_sectional", "none"),
    ("amount_mean_5d_rank", "Cross-sectional rank of 5d avg amount", ("amount",), "cross_sectional", "none"),
    # Index/industry relative returns
    ("stock_minus_index_ret_3d", "Stock 3d return minus index 3d return", ("close", "index_close"), "rolling_past", "none"),
    ("stock_minus_index_ret_5d", "Stock 5d return minus index 5d return", ("close", "index_close"), "rolling_past", "none"),
    ("stock_minus_industry_ret_3d", "Stock 3d return minus industry 3d return", ("close", "industry"), "rolling_past", "none"),
    ("stock_minus_industry_ret_5d", "Stock 5d return minus industry 5d return", ("close", "industry"), "rolling_past", "none"),
]:
    _r(f"{_RS}__{_fid}", _fid, _RS, "derived", dependencies=_deps,
       compute_fn=_RS_FN, pit_type=_pit, cache_scope=_scope,
       description=_desc)

# Medium/long-horizon returns (market_confirmation)
for _fid, _desc, _scope in [
    ("ret_20d", "20-day close return", "none"),
    ("ret_60d", "60-day close return", "panel"),
    ("ret_120d", "120-day close return", "panel"),
]:
    _r(f"{_RS}__{_fid}", _fid, _RS, "derived", dependencies=("close",),
       compute_fn=_RS_FN, pit_type="rolling_past", cache_scope=_scope,
       description=_desc)

for _fid, _desc, _scope in [
    ("volume_ratio_20d", "Volume ratio vs 20d average", "none"),
    ("volume_ratio_60d", "Volume ratio vs 60d average", "none"),
    ("distance_to_120d_high", "Price distance to 120-day high", "panel"),
    ("distance_to_250d_high", "Price distance to 250-day high", "panel"),
]:
    _r(f"{_RS}__{_fid}", _fid, _RS, "derived", dependencies=("close", "volume"),
       compute_fn=_RS_FN, pit_type="rolling_past", cache_scope=_scope,
       description=_desc)

# v2 continuation_trend_quality
for _fid, _desc, _scope in [
    ("up_day_ratio_60d", "Ratio of up days over 60d", "panel"),
    ("up_day_ratio_120d", "Ratio of up days over 120d", "panel"),
    ("trend_smoothness_60d", "Trend smoothness (R2) over 60d", "panel"),
    ("trend_smoothness_120d", "Trend smoothness (R2) over 120d", "panel"),
    ("max_pullback_120d", "Max peak-to-trough pullback over 120d", "panel"),
    ("volatility_adjusted_return_60d", "60d return / 60d volatility", "panel"),
    ("volatility_adjusted_return_120d", "120d return / 120d volatility", "panel"),
    ("rps_60d", "Relative Price Strength rank over 60d", "panel"),
    ("rps_120d", "Relative Price Strength rank over 120d", "panel"),
    ("rps_20d", "Relative Price Strength rank over 20d", "panel"),
    ("rps_20d_minus_rps_60d", "Short-term RPS minus medium-term RPS", "panel"),
    ("rps_industry_60d", "Industry-relative RPS over 60d", "panel"),
    ("rps_industry_120d", "Industry-relative RPS over 120d", "panel"),
    ("price_percentile_252d", "Current price percentile over 252d range", "panel"),
    ("distance_to_252d_low", "Price distance to 252-day low", "panel"),
]:
    _r(f"{_RS}__{_fid}", _fid, _RS, "derived", dependencies=("close", "volume"),
       compute_fn=_RS_FN, pit_type="cross_sectional" if _fid.startswith(("rps_", "price_percentile")) else "rolling_past",
       cache_scope=_scope, description=_desc)

# v2 volume_participation_quality
for _fid, _desc in [
    ("volume_up_down_ratio_60d", "Up-volume to down-volume ratio over 60d"),
    ("above_avg_volume_ratio_60d", "Ratio of days above avg volume over 60d"),
    ("amount_ratio_20d", "Amount ratio vs 20d average"),
    ("amount_ratio_60d", "Amount ratio vs 60d average"),
    ("volume_spike_20d", "Volume spike intensity over 20d"),
    ("volume_stability_60d", "Volume stability (inverse CV) over 60d"),
]:
    _r(f"{_RS}__{_fid}", _fid, _RS, "derived", dependencies=("close", "volume", "amount"),
       compute_fn=_RS_FN, pit_type="rolling_past", cache_scope="panel" if "60d" in _fid else "none",
       description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 5. Regime
# ═══════════════════════════════════════════════════════════════════════════
_REG = "regime"
_REG_FN = "groups.regime.build_regime_features"
for _fid, _desc, _deps, _pit in [
    ("market_breadth", "Fraction of stocks above 20d MA", ("close",), "cross_sectional"),
    ("limit_up_breadth", "Fraction of index stocks hitting limit-up", ("is_limit_up",), "cross_sectional"),
    ("index_volatility_5", "Index 5-day realised volatility", ("index_close",), "rolling_past"),
    ("index_volatility_10", "Index 10-day realised volatility", ("index_close",), "rolling_past"),
    ("index_volatility_20", "Index 20-day realised volatility", ("index_close",), "rolling_past"),
    ("small_vs_large_strength", "Small-cap vs large-cap relative strength", ("close", "circ_mv"), "cross_sectional"),
    ("growth_vs_value_proxy", "Growth vs value style spread proxy", ("close", "pb"), "cross_sectional"),
    ("market_trend_strength", "Index trend strength", ("index_close",), "rolling_past"),
]:
    _r(f"{_REG}__{_fid}", _fid, _REG, "derived", dependencies=_deps,
       compute_fn=_REG_FN, pit_type=_pit, cache_scope="none",
       description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 6. Industry Context
# ═══════════════════════════════════════════════════════════════════════════
_IC = "industry_context"
_IC_FN = "groups.industry_context.build_industry_context_features"
_IC_DEPS = ("close", "industry")
for _fid, _desc, _pit in [
    ("industry_ret_1d", "Industry-benchmark 1d return", "industry"),
    ("industry_ret_3d", "Industry-benchmark 3d return", "industry"),
    ("industry_ret_5d", "Industry-benchmark 5d return", "industry"),
    ("industry_breadth", "Fraction of industry stocks with positive return", "industry"),
    ("stock_minus_industry_ret", "Stock return minus industry return (1d)", "industry"),
    ("stock_minus_industry_ret_3d", "Stock 3d return minus industry 3d return", "industry"),
    ("stock_minus_industry_ret_5d", "Stock 5d return minus industry 5d return", "industry"),
]:
    _r(f"{_IC}__{_fid}", _fid, _IC, "derived", dependencies=_IC_DEPS,
       compute_fn=_IC_FN, pit_type=_pit, cache_scope="none",
       description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 7. Fundamental Context
# ═══════════════════════════════════════════════════════════════════════════
_FC = "fundamental_context"
_FC_FN = "groups.fundamental_context.build_fundamental_context_features"

# Raw-derived aliases
for _fid, _desc, _source, _pit in [
    ("log_mktcap", "Log of total market capitalisation", None, "point_in_time"),
    ("float_mktcap", "Float market capitalisation", None, "point_in_time"),
    ("pe_ttm", "Price-to-earnings ratio (TTM)", None, "point_in_time"),
    ("pb_raw", "Price-to-book ratio", None, "point_in_time"),
    ("ps_ttm", "Price-to-sales ratio (TTM)", None, "point_in_time"),
    ("gross_margin", "Gross profit margin", None, "point_in_time"),
    ("net_margin", "Net profit margin", None, "point_in_time"),
    ("operating_cf_to_profit", "Operating CF to net profit ratio", None, "point_in_time"),
    ("debt_to_asset", "Total debt to total assets ratio", None, "point_in_time"),
    ("roa", "Return on assets (net_income / total_assets)", None, "point_in_time"),
    ("roe", "Return on equity", None, "point_in_time"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=(),
       compute_fn=_FC_FN, pit_type=_pit, cache_scope="none", description=_desc)

# YoY fields (252d shift — approx, note in description)
for _fid, _desc in [
    ("revenue_yoy", "Revenue year-over-year (252d shift approximation)"),
    ("profit_yoy", "Net profit year-over-year (252d shift approximation)"),
    ("inventory_yoy", "Inventory year-over-year (252d shift approximation)"),
    ("ar_yoy", "Accounts receivable year-over-year (252d shift approximation)"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=(),
       compute_fn=_FC_FN, pit_type="point_in_time", cache_scope="none", description=_desc)

# 252d deltas
for _fid, _desc in [
    ("roe_delta_252d", "252-day change in ROE"),
    ("grossprofit_margin_delta_252d", "252-day change in gross profit margin"),
    ("debt_to_assets_delta_252d", "252-day change in debt-to-assets"),
    ("op_cashflow_delta_252d", "252-day change in operating cash flow"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=(),
       compute_fn=_FC_FN, pit_type="rolling_past", cache_scope="none", description=_desc)

# Valuation ranks / percentiles
for _fid, _desc, _scope in [
    ("pe_rank_252d", "252-day percentile rank of PE", "panel"),
    ("pb_rank_252d", "252-day percentile rank of PB", "panel"),
    ("pe_delta_120d", "120-day change in PE", "panel"),
    ("pb_delta_120d", "120-day change in PB", "panel"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=(),
       compute_fn=_FC_FN, pit_type="rolling_past", cache_scope=_scope, description=_desc)

# v2 valuation_repair_setup
for _fid, _desc in [
    ("pe_percentile_756d", "756-day percentile rank of PE"),
    ("pb_percentile_756d", "756-day percentile rank of PB"),
    ("pe_distance_from_756d_low", "PE distance from 756-day low"),
    ("pb_distance_from_756d_low", "PB distance from 756-day low"),
    ("pe_repair_room_to_median", "PE upside room to 756d median"),
    ("pb_repair_room_to_median", "PB upside room to 756d median"),
    ("earnings_yield_proxy", "Earnings yield proxy (1/PE)"),
    ("peg_proxy", "PEG ratio proxy (PE/earnings growth)"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=(),
       compute_fn=_FC_FN, pit_type="rolling_past", cache_scope="panel", description=_desc)

# v2 fundamental_acceleration
for _fid, _desc in [
    ("revenue_yoy_accel", "Revenue YoY acceleration (delta of YoY rate)"),
    ("profit_yoy_accel", "Profit YoY acceleration (delta of YoY rate)"),
    ("roe_delta_756d", "756-day change in ROE"),
    ("net_margin_delta_756d", "756-day change in net margin"),
    ("ocf_margin", "Operating cash flow margin"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=(),
       compute_fn=_FC_FN, pit_type="rolling_past", cache_scope="panel", description=_desc)

# v2 path_classifier_scores
for _fid, _desc in [
    ("continuation_candidate_score", "Continuation trend candidate score"),
    ("repair_candidate_score", "Repair/turnaround candidate score"),
    ("overheat_risk_score", "Overheat/exhaustion risk score"),
    ("value_trap_risk_score", "Value trap risk score"),
]:
    _r(f"{_FC}__{_fid}", _fid, _FC, "derived", dependencies=("close", "volume"),
       compute_fn=_FC_FN, pit_type="cross_sectional", cache_scope="panel", description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 8. v3a Margin
# ═══════════════════════════════════════════════════════════════════════════
_V3A = "v3a_margin"
_V3A_FN = "groups.value_growth_v3a.build_margin_features"
for _fid, _desc, _deps, _pit, _scope in [
    ("margin_eligible", "Binary: stock eligible for margin trading", ("margin_balance",), "point_in_time", "none"),
    ("margin_balance_to_float_mv", "Margin balance to float market value", ("margin_balance", "circ_mv"), "point_in_time", "none"),
    ("margin_balance_chg_20d", "20-day change in margin balance", ("margin_balance",), "rolling_past", "none"),
    ("margin_balance_chg_60d", "60-day change in margin balance", ("margin_balance",), "rolling_past", "none"),
    ("margin_buy_intensity_20d", "20-day margin buy intensity", ("margin_buy_amount", "amount"), "rolling_past", "none"),
    ("margin_repay_to_buy_20d", "20-day repayment-to-purchase ratio", ("margin_repay_amount", "margin_buy_amount"), "rolling_past", "none"),
    ("margin_crowding_score", "Margin crowding score", ("margin_balance", "circ_mv"), "cross_sectional", "panel"),
    ("margin_trend_confirm_score", "Margin trend confirmation score", ("margin_balance", "close"), "cross_sectional", "panel"),
    ("margin_overheat_risk_score", "Margin overheat risk score", ("margin_balance", "close"), "cross_sectional", "panel"),
]:
    _r(f"{_V3A}__{_fid}", _fid, _V3A, "derived", dependencies=_deps,
       compute_fn=_V3A_FN, pit_type=_pit, cache_scope=_scope, description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 9. v3a Shareholder
# ═══════════════════════════════════════════════════════════════════════════
_V3A_SH = "v3a_shareholder"
_V3A_SH_FN = "groups.value_growth_v3a.build_shareholder_features"
for _fid, _desc, _deps, _pit, _scope in [
    ("holder_num_chg_qoq", "QoQ change in number of shareholders", ("holder_num",), "point_in_time", "none"),
    ("holder_num_chg_2q", "Change in shareholder count over 2 quarters", ("holder_num",), "point_in_time", "none"),
    ("avg_shares_per_holder_chg_qoq", "QoQ change in avg shares per holder", ("holder_num", "total_share"), "point_in_time", "none"),
    ("top10_holder_ratio_chg_qoq", "QoQ change in top-10 holder ratio", ("top10_holder_ratio",), "point_in_time", "none"),
    ("holder_concentration_score", "Shareholder concentration score", ("holder_num", "top10_holder_ratio"), "cross_sectional", "panel"),
    ("holder_squeeze_score", "Holder squeeze score", ("holder_num_chg_qoq", "ret_60d"), "cross_sectional", "panel"),
    ("holder_price_confirm_score", "Holder concentration and price trend confirm", ("holder_concentration_score", "ret_120d"), "cross_sectional", "panel"),
    ("holder_num_stale_days", "Days since last shareholder data update", ("holder_num",), "point_in_time", "none"),
    ("top10_holder_stale_days", "Days since last top-10 holder data update", ("top10_holder_ratio",), "point_in_time", "none"),
    ("top10_holder_ratio", "Top-10 holder ownership ratio", ("top10_holder_ratio",), "point_in_time", "none"),
]:
    _r(f"{_V3A_SH}__{_fid}", _fid, _V3A_SH, "derived", dependencies=_deps,
       compute_fn=_V3A_SH_FN, pit_type=_pit, cache_scope=_scope, description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 10. v3b Price Volume
# ═══════════════════════════════════════════════════════════════════════════
_V3B = "v3b_price_volume"
_V3B_FN = "groups.value_growth_v3b_price_volume.build_trend_quality_features"
_V3B_VOL_FN = "groups.value_growth_v3b_price_volume.build_volume_quality_features"
for _fid, _desc, _fn, _scope in [
    ("trend_consistency_60d", "Trend consistency score over 60d", _V3B_FN, "panel"),
    ("trend_consistency_120d", "Trend consistency score over 120d", _V3B_FN, "panel"),
    ("low_vol_uptrend_60d", "Low-volatility uptrend score over 60d", _V3B_FN, "panel"),
    ("low_vol_uptrend_120d", "Low-volatility uptrend score over 120d", _V3B_FN, "panel"),
    ("return_drawdown_ratio_60d", "Return-to-max-drawdown ratio over 60d", _V3B_FN, "panel"),
    ("return_drawdown_ratio_120d", "Return-to-max-drawdown ratio over 120d", _V3B_FN, "panel"),
    ("pullback_recovery_speed_60d", "Pullback recovery speed over 60d", _V3B_FN, "panel"),
    ("new_high_persistence_120d", "New-high persistence over 120d", _V3B_FN, "panel"),
    ("up_volume_down_volume_ratio_60d", "Up-volume to down-volume ratio over 60d", _V3B_VOL_FN, "panel"),
    ("up_volume_down_volume_ratio_120d", "Up-volume to down-volume ratio over 120d", _V3B_VOL_FN, "panel"),
    ("volume_contraction_after_rise_60d", "Volume contraction after rise over 60d", _V3B_VOL_FN, "panel"),
    ("quiet_accumulation_60d", "Quiet accumulation score over 60d", _V3B_VOL_FN, "panel"),
    ("amount_stability_60d", "Amount stability (inverse CV) over 60d", _V3B_VOL_FN, "panel"),
    ("breakout_volume_quality_120d", "Breakout volume quality score over 120d", _V3B_VOL_FN, "panel"),
]:
    _r(f"{_V3B}__{_fid}", _fid, _V3B, "derived", dependencies=("close", "volume", "amount"),
       compute_fn=_fn, pit_type="rolling_past", cache_scope=_scope, description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 11. v3b Interaction
# ═══════════════════════════════════════════════════════════════════════════
_V3B_INT = "v3b_interaction"
_V3B_INT_FN = "groups.value_growth_v3b_price_volume.build_v3a_v3b_interaction_features"
_V3B_INT_DEPS = ("holder_concentration_score", "margin_trend_confirm_score",
                  "trend_consistency_120d", "low_vol_uptrend_120d",
                  "volume_contraction_after_rise_60d", "pullback_recovery_speed_60d")
for _fid, _desc in [
    ("holder_concentration_trend_confirm", "Holder concentration × trend confirmation"),
    ("holder_concentration_low_vol_uptrend", "Holder concentration × low-vol uptrend"),
    ("holder_concentration_volume_contract", "Holder concentration × volume contraction"),
    ("margin_holder_trend_confirm", "Margin × holder × trend confirmation"),
    ("margin_pullback_recovery_confirm", "Margin × pullback recovery confirmation"),
]:
    _r(f"{_V3B_INT}__{_fid}", _fid, _V3B_INT, "derived",
       dependencies=_V3B_INT_DEPS, compute_fn=_V3B_INT_FN,
       pit_type="cross_sectional", cache_scope="panel", description=_desc)

# ═══════════════════════════════════════════════════════════════════════════
# 12. Industry Momentum (experimental)
# ═══════════════════════════════════════════════════════════════════════════
_IM = "industry_momentum"
_IM_FN = "groups.industry_momentum_features.build_industry_momentum_features"
_IM_DEPS = ("close", "amount", "industry")
for _fid, _desc, _pit, _scope in [
    ("industry_ret_20d", "Industry return over 20d (industry panel rolling)", "industry", "panel"),
    ("industry_ret_60d", "Industry return over 60d (industry panel rolling)", "industry", "panel"),
    ("industry_ret_120d", "Industry return over 120d (industry panel rolling)", "industry", "panel"),
    ("industry_breadth_20d", "Fraction of industry stocks up over 20d", "industry", "panel"),
    ("industry_breadth_60d", "Fraction of industry stocks up over 60d", "industry", "panel"),
    ("industry_new_high_ratio", "Fraction of industry stocks near 120d high", "industry", "panel"),
    ("industry_top_stock_momentum", "Top-quintile stock momentum within industry over 60d", "industry", "panel"),
    ("industry_volume_expansion", "Industry volume expansion ratio over 20d", "industry", "panel"),
    ("stock_minus_industry_ret_20d", "Stock 20d return minus industry 20d return", "cross_sectional", "panel"),
    ("stock_minus_industry_ret_60d", "Stock 60d return minus industry 60d return", "cross_sectional", "panel"),
    ("stock_industry_ret_corr_60d", "Stock-industry return rolling correlation over 60d", "rolling_past", "panel"),
]:
    _r(f"{_IM}__{_fid}", _fid, _IM, "derived", dependencies=_IM_DEPS,
       compute_fn=_IM_FN, pit_type=_pit, cache_scope=_scope,
       status="experimental", description=_desc)


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


def get_feature(feature_id_or_name: str) -> FeatureSpec:
    """Look up a FeatureSpec by feature_id or name."""
    if feature_id_or_name in FEATURE_REGISTRY:
        return FEATURE_REGISTRY[feature_id_or_name]
    fid = _NAME_INDEX.get(feature_id_or_name)
    if fid is not None:
        return FEATURE_REGISTRY[fid]
    raise KeyError(
        f"Feature not found: '{feature_id_or_name}'. "
        f"Known feature_ids: {len(FEATURE_REGISTRY)}, "
        f"names: {len(_NAME_INDEX)}"
    )


def get_active_features() -> list[FeatureSpec]:
    """Return all features with status == 'active'."""
    return [s for s in FEATURE_REGISTRY.values() if s.status == "active"]


def get_experimental_features() -> list[FeatureSpec]:
    """Return all features with status == 'experimental'."""
    return [s for s in FEATURE_REGISTRY.values() if s.status == "experimental"]


def get_group_features(group_name: str) -> list[FeatureSpec]:
    """Return all features belonging to *group_name*."""
    return [s for s in FEATURE_REGISTRY.values() if s.group == group_name]


def resolve_dependency_chain(feature_ids: list[str]) -> list[FeatureSpec]:
    """Expand transitive dependencies for the given feature_ids.

    Returns topologically sorted specs (dependencies first).
    Use this before computing derived features.
    """
    from collections import deque

    result: list[FeatureSpec] = []
    seen: set[str] = set()
    queue: deque[str] = deque(feature_ids)

    while queue:
        fid = queue.popleft()
        if fid in seen:
            continue
        try:
            spec = get_feature(fid)
        except KeyError:
            continue
        seen.add(fid)
        # Dependencies first
        for dep in spec.dependencies:
            if dep not in seen:
                queue.appendleft(dep)
        result.append(spec)

    return result


def list_feature_ids() -> list[str]:
    """Return all registered feature_ids, sorted."""
    return sorted(FEATURE_REGISTRY)


def list_feature_names() -> list[str]:
    """Return all registered feature names, sorted."""
    return sorted(_NAME_INDEX)


def get_feature_id_by_name(name: str) -> str | None:
    """Look up feature_id by column name, or return None."""
    return _NAME_INDEX.get(name)


# ── Consistency validation ─────────────────────────────────────────────────


def _compute_fn_hash(compute_fn_path: str | None) -> str:
    """Return a SHA256 hash of the compute function source code.

    Used for cache invalidation — a source code change produces a new hash.
    """
    if compute_fn_path is None:
        return ""
    import importlib
    import inspect
    try:
        mod_path, fn_name = compute_fn_path.rsplit(".", 1)
        module = importlib.import_module(f"qsys.feature.{mod_path}")
        fn = getattr(module, fn_name)
        source = inspect.getsource(fn)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return "unresolvable"


def validate_registry() -> list[str]:
    """Run self-consistency checks.

    Returns a list of error messages (empty = all clear).
    Does NOT raise — callers decide how to report.
    """
    errors: list[str] = []

    # 1. feature_id uniqueness
    # (Guaranteed by _r())

    # 2. Name uniqueness (allow deprecated dupes and known cross-group sharing)
    known_shared_names = {
        "stock_minus_industry_ret_3d",
        "stock_minus_industry_ret_5d",
    }
    name_counts: dict[str, int] = {}
    for spec in FEATURE_REGISTRY.values():
        name_counts[spec.name] = name_counts.get(spec.name, 0) + 1
    for name, count in name_counts.items():
        if count > 1 and name not in known_shared_names:
            specs = [s for s in FEATURE_REGISTRY.values() if s.name == name]
            active = [s for s in specs if s.status == "active"]
            if len(active) > 1:
                errors.append(
                    f"NAME_COLLISION: '{name}' has {len(active)} active specs: "
                    f"{[s.feature_id for s in active]}"
                )

    # 3. Dependency existence
    known_ids = set(FEATURE_REGISTRY)
    # Known raw field names that don't need a feature_id
    raw_known = {
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
        "is_limit_up", "is_limit_down",  # features used as deps by other groups
    }
    for spec in FEATURE_REGISTRY.values():
        for dep in spec.dependencies:
            if dep in known_ids or dep in raw_known:
                continue
            if dep in _NAME_INDEX:
                continue
            errors.append(
                f"MISSING_DEPENDENCY: '{spec.feature_id}' depends on '{dep}' "
                f"which is not in registry or raw_known"
            )

    # 4. Broken features referenced by active features
    for spec in FEATURE_REGISTRY.values():
        if spec.status != "active":
            continue
        for dep in spec.dependencies:
            dep_spec = FEATURE_REGISTRY.get(dep)
            if dep_spec and dep_spec.status == "broken":
                errors.append(
                    f"BROKEN_DEPENDENCY: active '{spec.feature_id}' depends on "
                    f"broken '{dep_spec.feature_id}'"
                )

    # 5. compute_fn paths (qualified only for derived)
    for spec in FEATURE_REGISTRY.values():
        if spec.kind == "derived" and spec.compute_fn:
            # Verify module path is valid
            if not spec.compute_fn.startswith("groups."):
                errors.append(
                    f"COMPUTE_FN_PATH: '{spec.feature_id}' compute_fn "
                    f"'{spec.compute_fn}' does not start with 'groups.'"
                )

    return errors


def validate_feature_list(feature_names: list[str]) -> list[str]:
    """Validate that a feature list (e.g. from YAML) is consistent.

    Returns error messages. Empty = valid.
    """
    errors: list[str] = []
    for name in feature_names:
        # Skip qlib expression strings
        if name.startswith("$") or "(" in name or ")" in name:
            continue
        try:
            spec = get_feature(name)
        except KeyError:
            errors.append(f"UNREGISTERED: '{name}' not found in registry")
            continue
        if spec.status == "broken":
            errors.append(f"BROKEN: '{name}' ({spec.feature_id}) is broken")
    return errors
