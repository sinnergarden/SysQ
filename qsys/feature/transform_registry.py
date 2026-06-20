"""Transform runtime registry — maps transform_id to actual compute functions.

This is a lightweight mapping, NOT a full framework.  Only register
transforms that are deterministic and can be called independently.

To register a new transform:
  1. Write the compute function (must accept and return ``pd.DataFrame``).
  2. Add a ``TransformRuntimeSpec`` entry below.
  3. Declare ``output_features`` — all feature names this transform produces.
  4. ``compute_fn_hash`` is auto-computed from the wrapper function source.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from qsys.feature.registry import FEATURE_GROUPS


@dataclass(frozen=True)
class TransformRuntimeSpec:
    """Runtime spec linking a transform_id to its compute function."""

    transform_id: str
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame]
    compute_fn_hash: str = ""
    input_features: tuple[str, ...] = field(default_factory=tuple)
    output_features: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""


# ── Compute function hash helper ──


def _fn_hash(wrapper_fn: Callable) -> str:
    """Compute a deterministic hash of the wrapper function's source code.

    This captures the actual implementation (the ``from ... import`` +
    single call within the wrapper).  If the underlying group builder
    changes, its source hash changes too.
    """
    try:
        source = inspect.getsource(wrapper_fn)
    except (OSError, TypeError):
        source = wrapper_fn.__name__
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


# ── Feature group name → transform_id mapping ──

_ENABLED_BY_TO_TRANSFORM_ID: dict[str, str] = {
    "enable_microstructure_features": "build_microstructure_features",
    "enable_liquidity_features": "build_liquidity_features",
    "enable_tradability_features": "build_tradability_features",
    "enable_relative_strength_features": "build_relative_strength_features",
    "enable_regime_features": "build_regime_features",
    "enable_industry_context_features": "build_industry_context_features",
    "enable_fundamental_context_features": "build_fundamental_context_features",
    "enable_v3a_margin_features": "build_margin_features",
    "enable_v3a_shareholder_features": "build_shareholder_features",
    "enable_v3b_price_volume_features": "build_v3b_price_volume_features",
    "enable_v3b_interaction_features": "build_v3b_interaction_features",
    "enable_industry_momentum_features": "build_industry_momentum_features",
}


def _outputs_for(enabled_by: str) -> tuple[str, ...]:
    """Look up the feature names for a group from FEATURE_GROUPS."""
    for gname, ginfo in FEATURE_GROUPS.items():
        if ginfo.get("enabled_by") == enabled_by:
            return tuple(ginfo.get("features", []))
    return ()


# ── Computed function wrappers ──


def _build_microstructure(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.microstructure import build_microstructure_features
    return build_microstructure_features(df)


def _build_liquidity(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.liquidity import build_liquidity_features
    return build_liquidity_features(df)


def _build_tradability(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.tradability import build_tradability_features
    return build_tradability_features(df)


def _build_relative_strength(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.relative_strength import build_relative_strength_features
    return build_relative_strength_features(df)


def _build_fundamental_context(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.fundamental_context import build_fundamental_context_features
    return build_fundamental_context_features(df)


def _build_industry_context(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.industry_context import build_industry_context_features
    return build_industry_context_features(df)


def _build_regime(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.regime import build_regime_features
    return build_regime_features(df)


def _build_margin(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3a import build_margin_features
    return build_margin_features(df)


def _build_shareholder(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3a import (
        load_shareholder_data,
        build_shareholder_features,
    )
    df = load_shareholder_data(df)
    return build_shareholder_features(df)


def _build_v3b_price_volume(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3b_price_volume import build_v3b_price_volume_features
    return build_v3b_price_volume_features(df)


def _build_v3b_interaction(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.value_growth_v3b_price_volume import build_v3a_v3b_interaction_features
    return build_v3a_v3b_interaction_features(df)


def _build_industry_momentum(df: pd.DataFrame) -> pd.DataFrame:
    from qsys.feature.groups.industry_momentum_features import build_industry_momentum_features
    return build_industry_momentum_features(df)


# ── Registry ──
# Each entry MUST have:
#   - output_features declared (from FEATURE_GROUPS)
#   - compute_fn_hash from actual wrapper source code

_REGISTRY_WRAPPERS: list[tuple[str, Callable, str]] = [
    ("build_microstructure_features", _build_microstructure, "enable_microstructure_features"),
    ("build_liquidity_features", _build_liquidity, "enable_liquidity_features"),
    ("build_tradability_features", _build_tradability, "enable_tradability_features"),
    ("build_relative_strength_features", _build_relative_strength, "enable_relative_strength_features"),
    ("build_fundamental_context_features", _build_fundamental_context, "enable_fundamental_context_features"),
    ("build_industry_context_features", _build_industry_context, "enable_industry_context_features"),
    ("build_regime_features", _build_regime, "enable_regime_features"),
    ("build_margin_features", _build_margin, "enable_v3a_margin_features"),
    ("build_shareholder_features", _build_shareholder, "enable_v3a_shareholder_features"),
    ("build_v3b_price_volume_features", _build_v3b_price_volume, "enable_v3b_price_volume_features"),
    ("build_v3b_interaction_features", _build_v3b_interaction, "enable_v3b_interaction_features"),
    ("build_industry_momentum_features", _build_industry_momentum, "enable_industry_momentum_features"),
]

_REGISTRY: dict[str, TransformRuntimeSpec] = {}
for _tid, _fn, _flag in _REGISTRY_WRAPPERS:
    _spec = TransformRuntimeSpec(
        transform_id=_tid,
        compute_fn=_fn,
        compute_fn_hash=_fn_hash(_fn),
        output_features=_outputs_for(_flag),
        description=f"Features from FEATURE_GROUPS['{_flag.replace('enable_', '').replace('_features', '')}']",
    )
    _REGISTRY[_tid] = _spec

TRANSFORM_IDS: tuple[str, ...] = tuple(_REGISTRY.keys())


def get_transform(transform_id: str) -> TransformRuntimeSpec | None:
    return _REGISTRY.get(transform_id)


def is_registered(transform_id: str) -> bool:
    return transform_id in _REGISTRY


def list_unresolved(transform_ids: list[str]) -> list[str]:
    return [tid for tid in transform_ids if tid not in _REGISTRY]
