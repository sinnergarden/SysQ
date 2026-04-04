from __future__ import annotations

import pandas as pd

from qsys.feature.config import RESEARCH_FEATURE_FLAGS
from qsys.feature.groups.microstructure import build_microstructure_features
from qsys.feature.groups.liquidity import build_liquidity_features
from qsys.feature.groups.tradability import build_tradability_features
from qsys.feature.groups.relative_strength import build_relative_strength_features
from qsys.feature.groups.regime import build_regime_features
from qsys.feature.groups.industry_context import build_industry_context_features
from qsys.feature.groups.index_context import attach_index_context
from qsys.feature.groups.fundamental_context import build_fundamental_context_features
from qsys.feature.transforms import apply_cross_sectional_standardization


def _repair_research_input_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    coalesce_pairs = {
        'close': ['close_x', 'close_y'],
        'high_limit': ['up_limit'],
        'low_limit': ['down_limit'],
        'volume': ['vol'],
    }
    for target, sources in coalesce_pairs.items():
        if target not in out.columns:
            out[target] = pd.NA
        base = pd.to_numeric(out[target], errors='coerce')
        for src in sources:
            if src in out.columns:
                base = base.combine_first(pd.to_numeric(out[src], errors='coerce'))
        out[target] = base
    return out


def build_phase1_features(df: pd.DataFrame, flags: dict | None = None) -> pd.DataFrame:
    flags = {**RESEARCH_FEATURE_FLAGS, **(flags or {})}
    out = _repair_research_input_columns(df)

    if flags.get("enable_microstructure_features", False):
        out = build_microstructure_features(out)
    if flags.get("enable_liquidity_features", False):
        out = build_liquidity_features(out)
    if flags.get("enable_tradability_features", False):
        out = build_tradability_features(out)
    if flags.get("enable_regime_features", False) or flags.get("enable_relative_strength_features", False):
        out = attach_index_context(out, index_name='hs300')
    if flags.get("enable_relative_strength_features", False):
        out = build_relative_strength_features(out)
    if flags.get("enable_industry_context_features", False):
        out = build_industry_context_features(out)
    if flags.get("enable_regime_features", False):
        out = build_regime_features(out)
    if flags.get("enable_fundamental_context_features", False):
        out = build_fundamental_context_features(out)

    standardize_cols = [
        c for c in [
            "close_to_open_gap_1d",
            "open_to_close_ret",
            "close_pos_in_range",
            "open_pos_in_range",
            "upper_shadow_ratio",
            "lower_shadow_ratio",
            "intraday_reversal_strength",
            "amount_log",
            "amount_zscore_20",
            "volume_shock_3",
            "volume_shock_5",
            "turnover_acceleration",
            "illiquidity",
            "distance_to_limit_up",
            "distance_to_limit_down",
            "tradability_score",
        ] if c in out.columns
    ]
    if standardize_cols:
        out = apply_cross_sectional_standardization(out, standardize_cols, date_col="trade_date")
    return out
