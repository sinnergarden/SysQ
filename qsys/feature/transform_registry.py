"""Transform runtime registry — maps transform_id to actual compute functions.

This is a lightweight mapping, NOT a full framework.  Only register
transforms that are deterministic and can be called independently.

To register a new transform:
  1. Write the compute function (must accept and return ``pd.DataFrame``).
  2. Add a ``TransformRuntimeSpec`` entry to ``_REGISTRY``.
  3. Export the transform_id in the ``TRANSFORM_IDS`` list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class TransformRuntimeSpec:
    """Runtime spec linking a transform_id to its compute function."""

    transform_id: str
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame]
    input_features: tuple[str, ...] = field(default_factory=tuple)
    output_features: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


# ── Computed function wrappers ──
# These wrap existing group builder functions so they match the
# Callable[[pd.DataFrame], pd.DataFrame] signature.
# Each wrapper first checks that its required inputs are present
# (otherwise returns df unchanged — the cache layer filters later).


def _build_microstructure(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.microstructure import build_microstructure_features  # noqa: PLC0415
    return build_microstructure_features(df)


def _build_liquidity(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.liquidity import build_liquidity_features  # noqa: PLC0415
    return build_liquidity_features(df)


def _build_tradability(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.tradability import build_tradability_features  # noqa: PLC0415
    return build_tradability_features(df)


def _build_relative_strength(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.relative_strength import build_relative_strength_features  # noqa: PLC0415
    return build_relative_strength_features(df)


def _build_fundamental_context(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.fundamental_context import build_fundamental_context_features  # noqa: PLC0415
    return build_fundamental_context_features(df)


def _build_industry_context(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.industry_context import build_industry_context_features  # noqa: PLC0415
    return build_industry_context_features(df)


def _build_regime(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.regime import build_regime_features  # noqa: PLC0415
    return build_regime_features(df)


def _build_margin(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3a import build_margin_features  # noqa: PLC0415
    return build_margin_features(df)


def _build_shareholder(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3a import (  # noqa: PLC0415
        load_shareholder_data,
        build_shareholder_features,
    )
    df = load_shareholder_data(df)
    return build_shareholder_features(df)


def _build_v3b_price_volume(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3b_price_volume import build_v3b_price_volume_features  # noqa: PLC0415
    return build_v3b_price_volume_features(df)


def _build_v3b_interaction(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3b_price_volume import build_v3a_v3b_interaction_features  # noqa: PLC0415
    return build_v3a_v3b_interaction_features(df)


def _build_industry_momentum(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.industry_momentum_features import build_industry_momentum_features  # noqa: PLC0415
    return build_industry_momentum_features(df)


# ── Registry ──

_REGISTRY: dict[str, TransformRuntimeSpec] = {
    s.transform_id: s
    for s in [
        TransformRuntimeSpec(
            transform_id="build_microstructure_features",
            compute_fn=_build_microstructure,
            description="Microstructure features (gap, shadow, reversal)",
        ),
        TransformRuntimeSpec(
            transform_id="build_liquidity_features",
            compute_fn=_build_liquidity,
            description="Liquidity features (turnover, amihud, volume shock)",
        ),
        TransformRuntimeSpec(
            transform_id="build_tradability_features",
            compute_fn=_build_tradability,
            description="Tradability features (limit up/down, distance)",
        ),
        TransformRuntimeSpec(
            transform_id="build_relative_strength_features",
            compute_fn=_build_relative_strength,
            description="Relative strength features (returns, RPS, trend quality)",
        ),
        TransformRuntimeSpec(
            transform_id="build_fundamental_context_features",
            compute_fn=_build_fundamental_context,
            description="Fundamental context (PE, PB, ROE, growth, valuation repair)",
        ),
        TransformRuntimeSpec(
            transform_id="build_industry_context_features",
            compute_fn=_build_industry_context,
            description="Industry context features (industry return, breadth)",
        ),
        TransformRuntimeSpec(
            transform_id="build_regime_features",
            compute_fn=_build_regime,
            description="Market regime features (breadth, volatility, trend)",
        ),
        TransformRuntimeSpec(
            transform_id="build_margin_features",
            compute_fn=_build_margin,
            description="Margin financing features (eligible, balance, crowding)",
        ),
        TransformRuntimeSpec(
            transform_id="build_shareholder_features",
            compute_fn=_build_shareholder,
            description="Shareholder concentration features (holder num, top10)",
        ),
        TransformRuntimeSpec(
            transform_id="build_v3b_price_volume_features",
            compute_fn=_build_v3b_price_volume,
            description="V3b price-volume quality features",
        ),
        TransformRuntimeSpec(
            transform_id="build_v3b_interaction_features",
            compute_fn=_build_v3b_interaction,
            description="V3a x V3b interaction features",
        ),
        TransformRuntimeSpec(
            transform_id="build_industry_momentum_features",
            compute_fn=_build_industry_momentum,
            description="Industry momentum proxy features",
        ),
    ]
}

TRANSFORM_IDS: tuple[str, ...] = tuple(_REGISTRY.keys())
"""All registered transform IDs."""


def get_transform(transform_id: str) -> TransformRuntimeSpec | None:
    """Look up a transform by ID."""
    return _REGISTRY.get(transform_id)


def is_registered(transform_id: str) -> bool:
    """Check if a transform is registered."""
    return transform_id in _REGISTRY


def list_unresolved(transform_ids: list[str]) -> list[str]:
    """Return IDs that are NOT in the registry."""
    return [tid for tid in transform_ids if tid not in _REGISTRY]
