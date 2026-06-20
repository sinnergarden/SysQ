"""FeatureSpec registry — per-feature metadata skeleton (Phase 1).

This is an **internal implementation detail**, not a user-facing API.
For users: only ``configs/features/*.yaml`` matters.

Current status (Phase 1 of the migration plan):
- ``FeatureSpec`` dataclass defined ✓
- ``TransformSpec`` dataclass defined ✓
- ``register()/get_by_id()/verify_feature_list()`` API ✓
- Partial raw specs populated (representative sample)
- Derived specs populated (representative sample — not yet full 171)
- NOT yet wired into builder.py or resolver.py

Target architecture (full phases):
::
    User-facing:  FeatureSet YAML  (configs/features/*.yaml)
                      |
                      v (internal)
    Internal:     Resolver → FeatureSpec + TransformSpec → BuildPlan
                      |               |
                      v               v
                   Build Plan     Feature Cache
                      |               |
                      v               v
                   Manifest (audit only) — if final columns ≠ resolved → fail

Rules:
- ``status="broken"`` features MUST NOT enter any active feature list.
- ``status="deprecated"`` features MAY appear but MUST trigger a hard warning.
- feature_id is permanent. name is the DataFrame column (may change).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Type aliases ──

FeatureKind = Literal["raw", "derived"]
"""Internal classification — users never see this."""

PitType = Literal[
    "point_in_time",
    "daily_observed",
    "rolling_past",
    "cross_sectional",
    "static",
]
"""PIT contract for the feature computation.

- ``"point_in_time"`` — value visible at a specific date (financial statements,
  margin data).  Must use ``ann_date`` for merge.
- ``"daily_observed"`` — value is directly observed per trading day (OHLCV,
  volume, amount).  No lookahead concern as long as data is synced.
- ``"rolling_past"`` — computed over a historical lookback window.  Must
  groupby/instrument before rolling.
- ``"cross_sectional"`` — ranked / normalised across instruments on the same
  trade date.  Must groupby/trade_date.
- ``"static"`` — does not change (industry code).  Rare.
"""

CacheScope = Literal["none", "panel"]
"""Transform-level cache scope.

- ``"none"`` — simple computation, no caching needed.
- ``"panel"`` — expensive computation; cache the full (date, instrument) panel
  so rolling research windows can reuse it.

Per-feature cache (``"per_feature"``) and date-level cache (``"per_date"``)
are deferred — see docs/feature_cache_design.md.
"""

FeatureStatus = Literal["active", "experimental", "deprecated", "broken"]
"""Lifecycle status.

``"broken"`` → blocked from entering any feature list.  Fail fast.
"""


# ── Spec definitions ──


@dataclass(frozen=True)
class FeatureSpec:
    """Per-feature metadata.

    **Internal only.**  Researchers should never touch this directly;
    they write YAML, and FeatureSpec is populated by framework maintainers.

    All fields are frozen — once registered, a FeatureSpec should be
    considered read-only.
    """

    # ── Identity ──
    feature_id: str
    """Permanent stable identifier.  Never changes."""

    name: str
    """DataFrame column name.  Must match downstream YAML/qlib output."""

    group: str
    """Logical group (mirrors ``FEATURE_GROUPS`` keys for now)."""

    # ── Classification ──
    kind: FeatureKind
    """``"raw"`` — directly from data source.  ``"derived"`` — computed."""

    source: str | None = None
    """For raw: data source table.  For derived: implementing module path."""

    dependencies: tuple[str, ...] = field(default_factory=tuple)
    """Direct inputs (feature names).  Empty for raw features."""

    compute_fn: str | None = None
    """Fully qualified function name for derived features; ``None`` for raw."""

    # ── Type and storage hints (for Resolver / Cache) ──
    dtype: str | None = None
    """Expected pandas dtype, or ``None`` if unknown."""

    pit_type: PitType = "rolling_past"
    """PIT contract — consumed by Resolver to validate build order."""

    cache_scope: CacheScope = "none"
    """Cache hint — consumed by Cache layer."""

    # ── Lifecycle ──
    status: FeatureStatus = "active"
    """``"broken"`` features must not enter any active feature list."""

    # ── Documentation ──
    description: str = ""
    """Human-readable description of what this feature measures."""

    owner: str | None = None
    """Who maintains this feature (name or team)."""


@dataclass(frozen=True)
class TransformSpec:
    """Describes a transform (derived feature compute unit).

    **Internal only.**  The Resolver uses TransformSpec to decide:
    - What order to run transforms (dependency DAG)
    - What needs caching (expensive panel-level transforms)
    - What source columns are required

    A single TransformSpec maps to one ``if flag: out = build_something(out)``
    block in the current builder.py.  Over time, builder will be driven by
    TransformSpec resolution rather than hard-coded flag dispatch.
    """

    transform_id: str
    """Unique identifier for this transform (e.g. ``"build_microstructure"``)."""

    inputs: tuple[str, ...] = field(default_factory=tuple)
    """Feature names this transform reads.  May be raw or derived."""

    outputs: tuple[str, ...] = field(default_factory=tuple)
    """Feature names this transform produces."""

    compute_fn: str | None = None
    """Function reference, e.g. ``"build_microstructure_features"``."""

    pit_contract: str = ""
    """Short description of PIT obligations (see docs/feature_development.md)."""

    cache_scope: CacheScope = "none"
    """``"panel"`` if the transform is expensive enough to cache."""

    dependencies: tuple[str, ...] = field(default_factory=tuple)
    """Other transform_ids this transform depends on (build order)."""


# ── Global registries ──

_FEATURE_SPECS: dict[str, FeatureSpec] = {}
"""feature_id → FeatureSpec."""

_NAME_INDEX: dict[str, str] = {}
"""name → feature_id.  Enforces name uniqueness."""

_TRANSFORM_SPECS: dict[str, TransformSpec] = {}
"""transform_id → TransformSpec."""


# ── Registration API ──


def register(spec: FeatureSpec) -> None:
    """Register a single FeatureSpec.

    Raises ``ValueError`` if feature_id or name is already registered.
    """
    if spec.feature_id in _FEATURE_SPECS:
        raise ValueError(
            f"FeatureSpec feature_id '{spec.feature_id}' already registered"
        )
    if spec.name in _NAME_INDEX:
        existing_id = _NAME_INDEX[spec.name]
        if existing_id != spec.feature_id:
            raise ValueError(
                f"Feature name '{spec.name}' already registered "
                f"(feature_id={existing_id})"
            )
    _FEATURE_SPECS[spec.feature_id] = spec
    _NAME_INDEX[spec.name] = spec.feature_id


def register_batch(specs: list[FeatureSpec]) -> None:
    """Register multiple FeatureSpecs."""
    for sp in specs:
        register(sp)


def register_transform(spec: TransformSpec) -> None:
    """Register a TransformSpec."""
    if spec.transform_id in _TRANSFORM_SPECS:
        raise ValueError(
            f"TransformSpec '{spec.transform_id}' already registered"
        )
    _TRANSFORM_SPECS[spec.transform_id] = spec


def get_by_id(feature_id: str) -> FeatureSpec | None:
    """Lookup a FeatureSpec by feature_id."""
    return _FEATURE_SPECS.get(feature_id)


def get_by_name(name: str) -> FeatureSpec | None:
    """Lookup a FeatureSpec by output column name."""
    fid = _NAME_INDEX.get(name)
    if fid is None:
        return None
    return _FEATURE_SPECS.get(fid)


def list_specs(
    status: FeatureStatus | None = None,
    kind: FeatureKind | None = None,
    group: str | None = None,
) -> list[FeatureSpec]:
    """List registered specs, optionally filtered."""
    results = []
    for spec in _FEATURE_SPECS.values():
        if status is not None and spec.status != status:
            continue
        if kind is not None and spec.kind != kind:
            continue
        if group is not None and spec.group != group:
            continue
        results.append(spec)
    return results


# ── Dependency resolution (Phase 2 target, simple version for tests) ──


def resolve_dependencies(
    feature_id: str,
    *,
    _path: set[str] | None = None,
    _resolved: set[str] | None = None,
) -> list[str]:
    """Resolve the full dependency chain for a feature to its raw leaf inputs.

    Uses ``_path`` (recursion stack) for circular detection — two branches
    that share a common ancestor do NOT trigger a false positive.
    Uses ``_resolved`` for visited-set dedup so the same raw leaf is not
    double-listed.

    Returns a stable-ordered, deduplicated list of raw feature names.

    Raises ``ValueError`` on genuine circular dependency (``_path`` hit).
    """
    if _path is None:
        _path = set()
    if _resolved is None:
        _resolved = set()

    spec = get_by_id(feature_id)
    if spec is None:
        return []
    if spec.kind == "raw":
        if spec.name not in _resolved:
            _resolved.add(spec.name)
        return [spec.name]

    # ── Circular detection (recursion-stack path check) ──
    if feature_id in _path:
        raise ValueError(f"Circular dependency detected: {feature_id}")
    _path.add(feature_id)

    deps: list[str] = []
    for dep_name in spec.dependencies:
        dep_spec = get_by_name(dep_name)
        if dep_spec is not None:
            deps.extend(
                resolve_dependencies(
                    dep_spec.feature_id, _path=_path, _resolved=_resolved
                )
            )

    _path.discard(feature_id)

    # ── Stable-order dedup ──
    seen: set[str] = set()
    ordered: list[str] = []
    for d in deps:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


# ── Validation helpers ──


def check_broken_features(names: list[str]) -> list[str]:
    """Return feature names whose status is ``"broken"``."""
    broken: list[str] = []
    for name in names:
        spec = get_by_name(name)
        if spec is not None and spec.status == "broken":
            broken.append(name)
    return broken


def check_deprecated_features(names: list[str]) -> list[str]:
    """Return feature names whose status is ``"deprecated"``."""
    deprecated: list[str] = []
    for name in names:
        spec = get_by_name(name)
        if spec is not None and spec.status == "deprecated":
            deprecated.append(name)
    return deprecated


def check_missing_features(names: list[str]) -> list[str]:
    """Return feature names NOT found in the registry at all."""
    missing: list[str] = []
    for name in names:
        if get_by_name(name) is None:
            missing.append(name)
    return missing


def verify_feature_list(features: list[str]) -> dict[str, list[str]]:
    """Verify a feature list against the registry.

    Returns ``{"broken": [...], "deprecated": [...], "missing": [...]}``.

    Downstream MUST fail if ``broken`` or ``missing`` is non-empty.
    ``deprecated`` SHOULD produce a hard warning.
    """
    return {
        "broken": check_broken_features(features),
        "deprecated": check_deprecated_features(features),
        "missing": check_missing_features(features),
    }
