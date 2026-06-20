"""Populate registry_v2 with FeatureSpec entries for all active features.

This script reads the existing FEATURE_GROUPS registry and the inventory
CSV to populate the FeatureSpec registry with appropriate metadata.
It is intended to be imported (not run standalone) to prime the registry.
"""

from qsys.feature.registry_v2 import (
    FeatureSpec,
    register_batch,
)


def build_all_specs() -> list[FeatureSpec]:
    """Build FeatureSpec entries for all currently active features."""
    specs: list[FeatureSpec] = []

    # ── Raw features ──
    raw_specs = [
        FeatureSpec(
            feature_id="close", name="close", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Daily close price (adjusted)",
        ),
        FeatureSpec(
            feature_id="open", name="open", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Daily open price",
        ),
        FeatureSpec(
            feature_id="high", name="high", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Daily high price",
        ),
        FeatureSpec(
            feature_id="low", name="low", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Daily low price",
        ),
        FeatureSpec(
            feature_id="volume", name="volume", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Daily trading volume (shares)",
        ),
        FeatureSpec(
            feature_id="amount", name="amount", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Daily trading amount (yuan)",
        ),
        FeatureSpec(
            feature_id="factor", name="factor", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Cumulative adjustment factor",
        ),
        FeatureSpec(
            feature_id="vwap", name="vwap", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Volume-weighted average price",
        ),
        FeatureSpec(
            feature_id="high_limit", name="high_limit", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Limit-up price",
        ),
        FeatureSpec(
            feature_id="low_limit", name="low_limit", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Limit-down price",
        ),
        FeatureSpec(
            feature_id="turnover_rate", name="turnover_rate", group="price_volume", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Share turnover rate",
        ),
        FeatureSpec(
            feature_id="industry", name="industry", group="classification", kind="raw",
            source="daily", pit_type="static", cache_scope="per_instrument",
            description="SW industry classification",
        ),
        FeatureSpec(
            feature_id="float_shares", name="float_shares", group="shares", kind="raw",
            source="daily", pit_type="static", cache_scope="none",
            description="Free float shares",
        ),
        # Fundamentals
        FeatureSpec(
            feature_id="pe", name="pe", group="valuation", kind="raw",
            source="fina_indicator", pit_type="point_in_time", cache_scope="panel",
            description="Price-to-earnings ratio (TTM)",
        ),
        FeatureSpec(
            feature_id="pb", name="pb", group="valuation", kind="raw",
            source="fina_indicator", pit_type="point_in_time", cache_scope="panel",
            description="Price-to-book ratio",
        ),
        FeatureSpec(
            feature_id="total_mv", name="total_mv", group="market_cap", kind="raw",
            source="fina_indicator", pit_type="point_in_time", cache_scope="panel",
            description="Total market value",
        ),
        FeatureSpec(
            feature_id="circ_mv", name="circ_mv", group="market_cap", kind="raw",
            source="fina_indicator", pit_type="point_in_time", cache_scope="panel",
            description="Circulating market value",
        ),
        FeatureSpec(
            feature_id="roe", name="roe", group="profitability", kind="raw",
            source="fina_indicator", pit_type="point_in_time", cache_scope="panel",
            description="Return on equity",
        ),
        FeatureSpec(
            feature_id="grossprofit_margin", name="grossprofit_margin", group="profitability", kind="raw",
            source="fina_indicator", pit_type="point_in_time", cache_scope="panel",
            description="Gross profit margin",
        ),
        FeatureSpec(
            feature_id="debt_to_assets", name="debt_to_assets", group="leverage", kind="raw",
            source="balancesheet", pit_type="point_in_time", cache_scope="panel",
            description="Debt to assets ratio",
        ),
        FeatureSpec(
            feature_id="current_ratio", name="current_ratio", group="liquidity", kind="raw",
            source="balancesheet", pit_type="point_in_time", cache_scope="panel",
            description="Current ratio",
        ),
        FeatureSpec(
            feature_id="net_income", name="net_income", group="financial_stmt", kind="raw",
            source="income", pit_type="point_in_time", cache_scope="panel",
            description="Net income",
        ),
        FeatureSpec(
            feature_id="revenue", name="revenue", group="financial_stmt", kind="raw",
            source="income", pit_type="point_in_time", cache_scope="panel",
            description="Revenue",
        ),
        FeatureSpec(
            feature_id="total_assets", name="total_assets", group="financial_stmt", kind="raw",
            source="balancesheet", pit_type="point_in_time", cache_scope="panel",
            description="Total assets",
        ),
        FeatureSpec(
            feature_id="equity", name="equity", group="financial_stmt", kind="raw",
            source="balancesheet", pit_type="point_in_time", cache_scope="panel",
            description="Total equity",
        ),
        FeatureSpec(
            feature_id="op_cashflow", name="op_cashflow", group="financial_stmt", kind="raw",
            source="cashflow", pit_type="point_in_time", cache_scope="panel",
            description="Operating cash flow",
        ),
        FeatureSpec(
            feature_id="margin_balance", name="margin_balance", group="margin", kind="raw",
            source="margin_detail", pit_type="point_in_time", cache_scope="panel",
            description="Margin financing balance",
        ),
        FeatureSpec(
            feature_id="margin_buy_amount", name="margin_buy_amount", group="margin", kind="raw",
            source="margin_detail", pit_type="point_in_time", cache_scope="panel",
            description="Margin buy amount",
        ),
        FeatureSpec(
            feature_id="margin_repay_amount", name="margin_repay_amount", group="margin", kind="raw",
            source="margin_detail", pit_type="point_in_time", cache_scope="panel",
            description="Margin repay amount",
        ),
        FeatureSpec(
            feature_id="holder_num", name="holder_num", group="shareholder", kind="raw",
            source="shareholder", pit_type="point_in_time", cache_scope="panel",
            description="Number of shareholders (quarterly from Tushare parquet)",
        ),
        FeatureSpec(
            feature_id="top10_holder_ratio", name="top10_holder_ratio", group="shareholder", kind="raw",
            source="shareholder", pit_type="point_in_time", cache_scope="panel",
            description="Top-10 holder ownership ratio (quarterly from Tushare parquet)",
        ),
    ]
    specs.extend(raw_specs)

    # ── Derived specs: one representative sample per group ──
    # (Full population can be generated from the inventory CSV in a follow-up PR)
    derived_specs: list[FeatureSpec] = [
        # microstructure
        FeatureSpec(
            feature_id="close_to_open_gap_1d", name="close_to_open_gap_1d",
            group="microstructure", kind="derived",
            dependencies=("close", "open"),
            compute_fn="build_microstructure_features", pit_type="static",
            cache_scope="none", status="active",
            description="Close-to-open gap ratio (prev close / today open)",
        ),
        FeatureSpec(
            feature_id="upper_shadow_ratio", name="upper_shadow_ratio",
            group="microstructure", kind="derived",
            dependencies=("high", "close", "open", "low"),
            compute_fn="build_microstructure_features", pit_type="static",
            cache_scope="none", status="active",
            description="Upper shadow length relative to daily range",
        ),
        # liquidity
        FeatureSpec(
            feature_id="turnover_rate", name="turnover_rate",
            group="liquidity", kind="derived",
            dependencies=("volume", "amount", "float_shares"),
            compute_fn="build_liquidity_features", pit_type="static",
            cache_scope="none", status="active",
            description="Share turnover rate (volume / free float shares)",
        ),
        FeatureSpec(
            feature_id="amount_zscore_20", name="amount_zscore_20",
            group="liquidity", kind="derived",
            dependencies=("amount",), pit_type="cross_sectional",
            compute_fn="build_liquidity_features",
            cache_scope="none", status="active",
            description="Cross-sectional z-score of 20d avg amount",
        ),
        # relative_strength — rolling type
        FeatureSpec(
            feature_id="ret_60d", name="ret_60d",
            group="relative_strength", kind="derived",
            dependencies=("close",), compute_fn="build_relative_strength_features",
            pit_type="rolling_past", cache_scope="panel", status="active",
            description="60-day close return",
        ),
        FeatureSpec(
            feature_id="rps_60d", name="rps_60d",
            group="relative_strength", kind="derived",
            dependencies=("close",), compute_fn="build_relative_strength_features",
            pit_type="cross_sectional", cache_scope="none", status="active",
            description="Relative Price Strength rank over 60d",
        ),
        # regime
        FeatureSpec(
            feature_id="market_breadth", name="market_breadth",
            group="regime", kind="derived",
            dependencies=("index_close",), compute_fn="build_regime_features",
            pit_type="cross_sectional", cache_scope="per_date", status="active",
            description="Fraction of stocks above 20d MA in index",
        ),
        # fundamental_context — point_in_time
        FeatureSpec(
            feature_id="revenue_yoy", name="revenue_yoy",
            group="fundamental_context", kind="derived",
            dependencies=("revenue",), compute_fn="build_fundamental_context_features",
            pit_type="point_in_time", cache_scope="panel", status="active",
            description="Revenue year-over-year growth",
        ),
        FeatureSpec(
            feature_id="pe_rank_252d", name="pe_rank_252d",
            group="fundamental_context", kind="derived",
            dependencies=("pe",), compute_fn="build_fundamental_context_features",
            pit_type="cross_sectional", cache_scope="none", status="active",
            description="252-day percentile rank of PE",
        ),
        # v3a margin
        FeatureSpec(
            feature_id="margin_crowding_score", name="margin_crowding_score",
            group="v3a_margin", kind="derived",
            dependencies=("margin_balance_to_float_mv", "margin_balance_chg_60d"),
            compute_fn="build_margin_features",
            pit_type="cross_sectional", cache_scope="none", status="active",
            description="Margin crowding score (z-score composite)",
        ),
        # v3a shareholder
        FeatureSpec(
            feature_id="holder_num_chg_qoq", name="holder_num_chg_qoq",
            group="v3a_shareholder", kind="derived",
            dependencies=("holder_num", "holder_num_prev_ann"),
            compute_fn="build_shareholder_features",
            pit_type="point_in_time", cache_scope="panel", status="active",
            description="QoQ change in number of shareholders (announcement-level)",
        ),
        # v3b — rolling_past
        FeatureSpec(
            feature_id="trend_consistency_120d", name="trend_consistency_120d",
            group="v3b_price_volume", kind="derived",
            dependencies=("close",), compute_fn="build_v3b_price_volume_features",
            pit_type="rolling_past", cache_scope="panel", status="active",
            description="Trend consistency score over 120d",
        ),
        # industry_momentum
        FeatureSpec(
            feature_id="industry_ret_20d", name="industry_ret_20d",
            group="industry_momentum", kind="derived",
            dependencies=("close", "industry"), compute_fn="build_industry_momentum_features",
            pit_type="cross_sectional", cache_scope="per_date", status="active",
            description="Industry-mean return over 20d (cross-sectional aggregated, then temporal rolling)",
        ),
    ]
    specs.extend(derived_specs)
    return specs


def populate_registry() -> None:
    """Populate the FeatureSpec registry with all built specs."""
    specs = build_all_specs()
    register_batch(specs)
