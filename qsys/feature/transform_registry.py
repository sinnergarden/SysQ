"""Transform runtime registry — maps transform_id to actual compute functions.

This is a lightweight mapping, NOT a full framework.  Only register
transforms that are deterministic and can be called independently.

To register a new transform:
  1. Write the compute function (must accept and return ``pd.DataFrame``).
  2. Add a ``TransformRuntimeSpec`` entry below.
  3. Declare ``output_features`` — all feature names this transform produces.
  4. ``compute_fn_hash`` is auto-computed from the underlying builder source.
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


# ── Compute function hash helper ──


def _fn_hash(inner_fn: Callable) -> str:
    """Compute a deterministic SHA-256 hash of *inner_fn* source code.

    *inner_fn* is the actual builder function (e.g.
    ``qsys.feature.groups.microstructure.build_microstructure_features``),
    NOT the wrapper ``_build_microstructure``.  This ensures that any
    change to the real feature computation invalidates the cache key.
    """
    try:
        source = inspect.getsource(inner_fn)
    except (OSError, TypeError):
        source = inner_fn.__name__
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]


# ── Computed function wrappers ──
# Each wrapper:
#   1. Imports its underlying builder.
#   2. Calls it.
# The registry entry below explicitly references the inner function so that
# _fn_hash hashes the real builder source, not the thin wrapper.


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
# Each entry: (transform_id, wrapper_fn, enabled_by_flag)

# fmt: off
_REGISTRY_ENTRIES: list[tuple[str, Callable, str]] = [
    ("build_microstructure_features",   _build_microstructure,   "enable_microstructure_features"),
    ("build_liquidity_features",        _build_liquidity,        "enable_liquidity_features"),
    ("build_tradability_features",      _build_tradability,      "enable_tradability_features"),
    ("build_relative_strength_features", _build_relative_strength, "enable_relative_strength_features"),
    ("build_fundamental_context_features", _build_fundamental_context, "enable_fundamental_context_features"),
    ("build_industry_context_features", _build_industry_context,  "enable_industry_context_features"),
    ("build_regime_features",           _build_regime,           "enable_regime_features"),
    ("build_margin_features",           _build_margin,           "enable_v3a_margin_features"),
    ("build_shareholder_features",      _build_shareholder,      "enable_v3a_shareholder_features"),
    ("build_v3b_price_volume_features",  _build_v3b_price_volume,  "enable_v3b_price_volume_features"),
    ("build_v3b_interaction_features",  _build_v3b_interaction,  "enable_v3b_interaction_features"),
    ("build_industry_momentum_features", _build_industry_momentum, "enable_industry_momentum_features"),
]
# fmt: on

# Resolve inner_fn references after module-level imports are available
_INNER_FN_MAP: dict[str, str] = {
    "build_microstructure_features": "qsys.feature.groups.microstructure:build_microstructure_features",
    "build_liquidity_features": "qsys.feature.groups.liquidity:build_liquidity_features",
    "build_tradability_features": "qsys.feature.groups.tradability:build_tradability_features",
    "build_relative_strength_features": "qsys.feature.groups.relative_strength:build_relative_strength_features",
    "build_fundamental_context_features": "qsys.feature.groups.fundamental_context:build_fundamental_context_features",
    "build_industry_context_features": "qsys.feature.groups.industry_context:build_industry_context_features",
    "build_regime_features": "qsys.feature.groups.regime:build_regime_features",
    "build_margin_features": "qsys.feature.groups.value_growth_v3a:build_margin_features",
    "build_shareholder_features": "qsys.feature.groups.value_growth_v3a:build_shareholder_features",
    "build_v3b_price_volume_features": "qsys.feature.groups.value_growth_v3b_price_volume:build_v3b_price_volume_features",
    "build_v3b_interaction_features": "qsys.feature.groups.value_growth_v3b_price_volume:build_v3a_v3b_interaction_features",
    "build_industry_momentum_features": "qsys.feature.groups.industry_momentum_features:build_industry_momentum_features",
}


def _resolve_inner_fn(module_path: str) -> Callable:
    """Import and return the inner builder function by module:name path."""
    mod_name, fn_name = module_path.split(":")
    import importlib
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


_REGISTRY: dict[str, TransformRuntimeSpec] = {}
for _tid, _wrapper, _flag in _REGISTRY_ENTRIES:
    _inner_fn = _resolve_inner_fn(_INNER_FN_MAP[_tid])
    _spec = TransformRuntimeSpec(
        transform_id=_tid,
        compute_fn=_wrapper,
        compute_fn_hash=_fn_hash(_inner_fn),
        output_features=_outputs_for(_flag),
    )
    _REGISTRY[_tid] = _spec

TRANSFORM_IDS: tuple[str, ...] = tuple(_REGISTRY.keys())


def get_transform(transform_id: str) -> TransformRuntimeSpec | None:
    return _REGISTRY.get(transform_id)


def is_registered(transform_id: str) -> bool:
    return transform_id in _REGISTRY


def list_unresolved(transform_ids: list[str]) -> list[str]:
    return [tid for tid in transform_ids if tid not in _REGISTRY]
