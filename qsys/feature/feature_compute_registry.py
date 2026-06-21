"""Feature compute registry — maps feature_id → compute spec.

Each ``FeatureComputeSpec`` describes how to compute a single feature.
No DAG, no transform topological sort — just a flat mapping from
feature_id to a compute function that accepts raw_panel and returns
a DataFrame or Series.

Batch adapter (``compute_phase1_feature``) bridges the existing
``build_phase1_features`` bulk builder to per-feature computation.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from qsys.feature.builder import build_phase1_features
from qsys.feature.config import RESEARCH_FEATURE_FLAGS
from qsys.feature.registry import FEATURE_GROUPS


# ── Compute spec ──


@dataclass(frozen=True)
class FeatureComputeSpec:
    """Specification for computing a single feature."""

    feature_id: str
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame | pd.Series]
    input_columns: tuple[str, ...] = field(default_factory=tuple)
    compute_fn_hash: str = ""
    pit_policy: str | None = None
    frequency: str = "daily"
    description: str = ""


# ── Phase1 batch adapter ──


def _infer_flags(feature_ids: list[str]) -> dict:
    """Derive build_phase1_features flags from a set of feature names."""
    flags = {k: False for k in RESEARCH_FEATURE_FLAGS}
    requested = set(feature_ids)
    for group in FEATURE_GROUPS.values():
        if requested.intersection(group.get("features", [])):
            flags[group["enabled_by"]] = True
    if requested.intersection({"stock_minus_industry_ret_3d", "stock_minus_industry_ret_5d"}):
        flags["enable_industry_context_features"] = True
    if any(f.startswith("industry_") or f.startswith("stock_minus_industry_") for f in requested):
        flags["enable_industry_momentum_features"] = True
        flags["enable_industry_context_features"] = True
    return flags


def compute_phase1_batch(
    raw_panel: pd.DataFrame,
    feature_ids: list[str],
    flags: dict | None = None,
) -> pd.DataFrame:
    """Compute multiple features via the phase1 builder, return full result."""
    if flags is None:
        flags = _infer_flags(feature_ids)
    raw = raw_panel.copy()
    # Build adapter-level rename map: $xxx -> xxx
    # This must be an EXACT column rename, not a string strip, to avoid
    # creating duplicate column names that trip up _repair_research_input_columns
    adapter_rename: dict[str, str] = {}
    for c in raw.columns:
        if c.startswith("$"):
            adapter_rename[c] = c[1:]
    # Also map instrument to ts_code if present
    if "instrument" in raw.columns and "ts_code" not in raw.columns:
        adapter_rename["instrument"] = "ts_code"
    if adapter_rename:
        raw = raw.rename(columns=adapter_rename)
    if "trade_date" in raw.columns:
        raw["trade_date"] = pd.to_datetime(raw["trade_date"])
    return build_phase1_features(raw, flags=flags)


def compute_phase1_feature(
    raw_panel: pd.DataFrame,
    feature_id: str,
    flags: dict | None = None,
) -> pd.DataFrame:
    """Compute a single feature using the phase1 builder, return ``[trade_date, ts_code, feature_id]``."""
    full = compute_phase1_batch(raw_panel, [feature_id], flags=flags)
    if feature_id not in full.columns:
        raise ValueError(
            f"Phase1 builder did not produce feature '{feature_id}'. "
            f"Available: {list(full.columns)}"
        )
    return full[["trade_date", "ts_code", feature_id]]


def _phase1_fn_hash() -> str:
    """Source hash for the phase1 batch adapter."""
    try:
        source = inspect.getsource(build_phase1_features)
        return hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
    except (OSError, TypeError):
        return "phase1_default_hash"


# ── Auto-generate specs for all known features from FEATURE_GROUPS ──

_PHASE1_HASH = _phase1_fn_hash()


def _build_specs() -> dict[str, FeatureComputeSpec]:
    specs: dict[str, FeatureComputeSpec] = {}
    for gname, ginfo in FEATURE_GROUPS.items():
        for feat in ginfo.get("features", []):
            if feat not in specs:
                _fid = feat  # closure-safe copy
                specs[_fid] = FeatureComputeSpec(
                    feature_id=_fid,
                    compute_fn=lambda p, fid=_fid: compute_phase1_feature(p, fid),
                    input_columns=(),
                    compute_fn_hash=_PHASE1_HASH,
                    pit_policy="rolling_past",
                    frequency="daily",
                    description=f"Phase1 builder feature from group '{gname}'",
                )
    return specs


_FEATURE_SPECS: dict[str, FeatureComputeSpec] = _build_specs()


def get_spec(feature_id: str) -> FeatureComputeSpec | None:
    """Look up a FeatureComputeSpec by feature_id."""
    return _FEATURE_SPECS.get(feature_id)


def register_spec(spec: FeatureComputeSpec) -> None:
    """Register a custom FeatureComputeSpec."""
    _FEATURE_SPECS[spec.feature_id] = spec


def has_spec(feature_id: str) -> bool:
    """Check if a feature has a registered compute spec."""
    return feature_id in _FEATURE_SPECS


def list_spec_ids() -> list[str]:
    """Return all registered feature IDs."""
    return sorted(_FEATURE_SPECS.keys())
