"""Feature cache — transform-level and matrix cache key/path/metadata.

**Internal only.**  Users never configure cache; they write FeatureSet YAML
and cache is an automatic optimization.

Two levels:
1. **Transform-level cache** (primary) — caches the full output panel of an
   expensive transform (e.g. ``build_relative_strength_features``).
2. **Matrix cache** — caches the complete feature matrix for a resolved
   ``feature_set_id``, useful for repeated rolling research and model training.

No per-feature cache (deferred).

Cache key rules:
- All components that affect output must be in the key.
- Same inputs → same key.  Different inputs → different key.
- No silent fallback on stale data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


# ── Cache context ──


@dataclass(frozen=True)
class FeatureCacheContext:
    """Contextual parameters that feed into cache key computation.

    Every field influences the cache key.  Change any field → cache miss.
    """

    feature_set_id: str
    date_start: str | None = None
    date_end: str | None = None
    universe: str | None = None
    source_manifest_hash: str = ""
    builder_hash: str | None = None
    pit_policy_hash: str | None = None


# ── Cache key ──


CacheKeyKind = Literal["transform", "matrix"]


@dataclass(frozen=True)
class CacheKey:
    """A computed cache key with its constituent parts for auditability."""

    kind: CacheKeyKind
    key: str
    parts: dict[str, str] = field(default_factory=dict)


def _stable_hash(data: dict) -> str:
    """Deterministic SHA-256 hex digest (first 20 chars) for a dict.

    Uses JSON serialisation with ``sort_keys=True`` so the same data
    always produces the same hash regardless of dict key order.
    """
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


# ── Transform cache key ──


def compute_transform_cache_key(
    transform_id: str,
    *,
    input_features: list[str],
    output_features: list[str],
    compute_fn_hash: str,
    context: FeatureCacheContext,
) -> CacheKey:
    """Compute a deterministic cache key for a transform.

    Key components (all order-sensitive):
    - ``transform_id``
    - ``input_features``  — order matters
    - ``output_features`` — order matters
    - ``compute_fn_hash`` — source code version
    - ``source_manifest_hash`` — raw data version
    - ``date_start`` / ``date_end`` — temporal range
    - ``universe`` — stock universe
    - ``pit_policy_hash`` — PIT rules version
    """
    parts = {
        "transform_id": transform_id,
        "input_features": json.dumps(input_features),
        "output_features": json.dumps(output_features),
        "compute_fn_hash": compute_fn_hash,
        "source_manifest_hash": context.source_manifest_hash,
        "date_start": context.date_start or "",
        "date_end": context.date_end or "",
        "universe": context.universe or "",
        "pit_policy_hash": context.pit_policy_hash or "",
        "builder_hash": context.builder_hash or "",
    }
    return CacheKey(
        kind="transform",
        key=_stable_hash(parts),
        parts=parts,
    )


# ── Matrix cache key ──


def compute_matrix_cache_key(
    feature_set_id: str,
    *,
    resolved_features: list[str],
    required_transforms: list[str],
    context: FeatureCacheContext,
) -> CacheKey:
    """Compute a deterministic cache key for a full feature matrix.

    Key components:
    - ``feature_set_id``
    - ``resolved_features`` — order matters (column order of matrix)
    - ``required_transforms`` — order sensitive (sorted in plan)
    - ``source_manifest_hash`` — raw data version
    - ``builder_hash`` — builder code version
    - ``date_start`` / ``date_end`` — temporal range
    - ``universe`` — stock universe
    - ``pit_policy_hash`` — PIT rules version
    """
    parts = {
        "feature_set_id": feature_set_id,
        "resolved_features": json.dumps(resolved_features),
        "required_transforms": json.dumps(required_transforms),
        "source_manifest_hash": context.source_manifest_hash,
        "builder_hash": context.builder_hash or "",
        "date_start": context.date_start or "",
        "date_end": context.date_end or "",
        "universe": context.universe or "",
        "pit_policy_hash": context.pit_policy_hash or "",
    }
    return CacheKey(
        kind="matrix",
        key=_stable_hash(parts),
        parts=parts,
    )


# ── Cache paths ──


def transform_cache_path(
    transform_id: str,
    cache_key: str,
    root: str | Path = "data/feature_cache",
) -> Path:
    """Return the expected parquet path for a transform-level cache entry.

    ``data/feature_cache/transforms/{transform_id}/{cache_key}.parquet``
    """
    return Path(root) / "transforms" / transform_id / f"{cache_key}.parquet"


def matrix_cache_path(
    feature_set_id: str,
    cache_key: str,
    root: str | Path = "data/feature_cache",
) -> Path:
    """Return the expected parquet path for a matrix-level cache entry.

    ``data/feature_cache/matrices/{feature_set_id}/{cache_key}.parquet``
    """
    return Path(root) / "matrices" / feature_set_id / f"{cache_key}.parquet"


# ── Cache metadata I/O ──


_CACHE_METADATA_VERSION = 1


def write_cache_metadata(path: Path, metadata: dict) -> None:
    """Write a ``.meta.json`` sidecar next to the cache parquet file.

    The sidecar path is ``{parquet_path}.meta.json``.
    """
    meta_path = Path(str(path) + ".meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_version": _CACHE_METADATA_VERSION,
        "cache_key": metadata.get("cache_key", ""),
        "kind": metadata.get("kind", ""),
        "created_at": metadata.get("created_at", datetime.now(timezone.utc).isoformat()),
        "context": metadata.get("context", {}),
    }
    meta_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def read_cache_metadata(path: Path) -> dict:
    """Read the ``.meta.json`` sidecar for a cache file.

    Returns an empty dict if the sidecar does not exist.
    """
    meta_path = Path(str(path) + ".meta.json")
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))
