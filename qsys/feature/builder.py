from __future__ import annotations

import pandas as pd

from qsys.feature.config import DEFAULT_RESEARCH_FEATURE_FLAGS
from qsys.feature.definitions.common import prepare_research_panel
from qsys.feature.definitions.fundamental_context import build_fundamental_context_features
from qsys.feature.definitions.index_context import attach_index_context
from qsys.feature.definitions.industry_context import build_industry_context_features
from qsys.feature.definitions.liquidity import build_liquidity_capacity_features
from qsys.feature.definitions.market_context import build_market_regime_features
from qsys.feature.definitions.price_state import build_daily_price_state_features
from qsys.feature.definitions.execution_state import build_execution_state_features
from qsys.feature.definitions.relative_strength import build_relative_strength_features
from qsys.feature.registry import list_standardization_candidates, resolve_feature_selection
from qsys.feature.selection import resolve_feature_groups
from qsys.feature.transforms import apply_cross_sectional_standardization


GROUP_BUILDERS = {
    "daily_price_state": build_daily_price_state_features,
    "liquidity_capacity": build_liquidity_capacity_features,
    "execution_state": build_execution_state_features,
    "industry_context": build_industry_context_features,
    "relative_strength": build_relative_strength_features,
    "market_regime": build_market_regime_features,
    "fundamental_context": build_fundamental_context_features,
}


def build_research_features(
    df: pd.DataFrame,
    *,
    groups: list[str] | None = None,
    flags: dict | None = None,
    preset: str = "research_default",
    feature_set: str | None = None,
    feature_ids: list[str] | None = None,
    select_only: bool = False,
    add_standardized_views: bool = True,
    index_name: str = "hs300",
) -> pd.DataFrame:
    normalized_flags = dict(flags or {})
    selection = None
    derived_groups = groups
    if feature_set is not None or feature_ids is not None:
        selection = resolve_feature_selection(feature_set=feature_set, feature_ids=feature_ids)
        if groups is None:
            derived_groups = selection.required_groups

    enabled_groups = resolve_feature_groups(groups=derived_groups, flags=normalized_flags, preset=preset)

    out = prepare_research_panel(df)
    if any(group in enabled_groups for group in {"relative_strength", "market_regime"}):
        out = attach_index_context(out, index_name=index_name)

    for group in enabled_groups:
        out = GROUP_BUILDERS[group](out)

    if add_standardized_views:
        standardize_cols = [name for name in list_standardization_candidates() if name in out.columns]
        out = apply_cross_sectional_standardization(out, standardize_cols, date_col="trade_date")

    if select_only and selection is not None:
        keep_columns = [
            column
            for column in ["trade_date", "ts_code"] + selection.qlib_column_names
            if column in out.columns
        ]
        if keep_columns:
            out = out[keep_columns].copy()
    return out


def build_phase1_features(df: pd.DataFrame, flags: dict | None = None) -> pd.DataFrame:
    normalized_flags = {**DEFAULT_RESEARCH_FEATURE_FLAGS, **(flags or {})}
    return build_research_features(df, flags=normalized_flags, preset="phase1_core")
