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
from qsys.feature.groups.growth_confirmation_v0 import build_growth_confirmation_features
from qsys.feature.transforms import apply_cross_sectional_standardization


def _coerce_first_numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame.loc[:, frame.columns == column]
    if isinstance(value, pd.DataFrame):
        series = pd.Series(pd.NA, index=frame.index, dtype="object")
        for idx in range(value.shape[1]):
            series = series.combine_first(pd.to_numeric(value.iloc[:, idx], errors="coerce"))
        return pd.to_numeric(series, errors="coerce")
    return pd.to_numeric(value, errors="coerce")


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
        base = _coerce_first_numeric(out, target)
        for src in sources:
            if src in out.columns:
                base = base.combine_first(_coerce_first_numeric(out, src))
        target_positions = [idx for idx, name in enumerate(out.columns) if name == target]
        insert_at = target_positions[0] if target_positions else len(out.columns)
        if target_positions:
            keep_mask = out.columns != target
            out = out.loc[:, keep_mask].copy()
        out.insert(insert_at, target, base)
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
    if flags.get("enable_v3a_margin_features", False):
        from qsys.feature.groups.value_growth_v3a import build_margin_features
        out = build_margin_features(out)
    if flags.get("enable_v3a_shareholder_features", False):
        from qsys.feature.groups.value_growth_v3a import load_shareholder_data, build_shareholder_features
        out = load_shareholder_data(
            out,
            holder_path=flags.get(
                "shareholder_holder_path",
                "data/canonical/holder_num.parquet",
            ),
            top10_path=flags.get("shareholder_top10_path"),
        )
        out = build_shareholder_features(out)
    if flags.get("enable_v3b_price_volume_features", False):
        from qsys.feature.groups.value_growth_v3b_price_volume import build_v3b_price_volume_features
        out = build_v3b_price_volume_features(out)
    if flags.get("enable_v3b_interaction_features", False):
        from qsys.feature.groups.value_growth_v3b_price_volume import build_v3a_v3b_interaction_features
        out = build_v3a_v3b_interaction_features(out)
    if flags.get("enable_industry_momentum_features", False):
        from qsys.feature.groups.industry_momentum_features import build_industry_momentum_features
        out = build_industry_momentum_features(out)


    if flags.get("enable_growth_confirmation_features", False):
        out = build_growth_confirmation_features(
            out,
            income_sidecar_path=flags.get("income_sidecar_path", ""),
            income_sidecar_sha256=flags.get("income_sidecar_sha256", ""),
            income_sidecar_manifest_path=flags.get(
                "income_sidecar_manifest_path", ""
            ),
            income_sidecar_manifest_sha256=flags.get(
                "income_sidecar_manifest_sha256", ""
            ),
            income_source_mode=flags.get(
                "income_source_mode", "legacy_unverified_global_v0"
            ),
            income_sidecar_required_start=flags.get(
                "income_sidecar_required_start"
            ),
            income_sidecar_required_end=flags.get("income_sidecar_required_end"),
            income_sidecar_required_history_start=flags.get(
                "income_sidecar_required_history_start", ""
            ),
        )


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
