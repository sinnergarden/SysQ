"""Feature cache — key/path/metadata/read/write for transform and matrix cache.

**Internal only.**  Users never configure cache.

Two levels:
1. **Transform-level cache** (primary) — caches full panel output of a transform.
2. **Matrix cache** — caches the complete feature matrix for a feature_set_id.

No per-feature cache (deferred).

Validation rules (all fail fast):
- Missing ``trade_date`` / ``ts_code`` column.
- Missing expected feature column.
- Cache key mismatch (read).
- Missing ``.meta.json`` sidecar (read).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from qsys.utils.logger import log


# ── Cache context ──


@dataclass(frozen=True)
class FeatureCacheContext:
    """Canonical parameters that influence every cache key."""

    feature_set_id: str
    date_start: str | None = None
    date_end: str | None = None
    universe: str | None = None
    source_manifest_hash: str = ""
    builder_hash: str | None = None
    pit_policy_hash: str | None = None


CacheKeyKind = Literal["transform", "matrix"]


@dataclass(frozen=True)
class CacheKey:
    kind: CacheKeyKind
    key: str
    parts: dict[str, str] = field(default_factory=dict)


# ── Helpers ──


def _stable_hash(data: dict) -> str:
    raw = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _required_columns(df: pd.DataFrame, expected: list[str], label: str) -> None:
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(
            f"{label}: missing required columns: {missing}. "
            f"Available: {list(df.columns)}"
        )


def _verify_metadata(meta_path: Path, expected_cache_key: str) -> dict:
    if not meta_path.exists():
        raise ValueError(f"Cache metadata not found: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    actual_key = meta.get("cache_key", "")
    if actual_key != expected_cache_key:
        raise ValueError(
            f"Cache key mismatch. Expected: '{expected_cache_key}', "
            f"got: '{actual_key}'"
        )
    return meta


# ── Cache key computation ──


def compute_transform_cache_key(
    transform_id: str,
    *,
    input_features: list[str],
    output_features: list[str],
    compute_fn_hash: str,
    context: FeatureCacheContext,
) -> CacheKey:
    parts = {
        "kind": "transform",
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
    return CacheKey(kind="transform", key=_stable_hash(parts), parts=parts)


def compute_matrix_cache_key(
    feature_set_id: str,
    *,
    resolved_features: list[str],
    required_transforms: list[str],
    context: FeatureCacheContext,
) -> CacheKey:
    parts = {
        "kind": "matrix",
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
    return CacheKey(kind="matrix", key=_stable_hash(parts), parts=parts)


# ── Cache paths ──


def transform_cache_path(
    transform_id: str,
    cache_key: str,
    root: str | Path = "data/feature_cache",
) -> Path:
    return Path(root) / "transforms" / transform_id / f"{cache_key}.parquet"


def matrix_cache_path(
    feature_set_id: str,
    cache_key: str,
    root: str | Path = "data/feature_cache",
) -> Path:
    return Path(root) / "matrices" / feature_set_id / f"{cache_key}.parquet"


# ── Cache existence ──


def cache_exists(path: Path) -> bool:
    """Check if a cache parquet + meta sidecar both exist."""
    if not path.exists():
        return False
    meta_path = Path(str(path) + ".meta.json")
    return meta_path.exists()


# ── Standalone metadata helpers (for tests and tooling) ──


def write_cache_metadata(path: Path, metadata: dict) -> None:
    """Write a ``.meta.json`` sidecar next to an arbitrary cache file.

    The sidecar path is ``{path}.meta.json``.
    """
    meta_path = Path(str(path) + ".meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_version": 1,
        "cache_key": metadata.get("cache_key", ""),
        "kind": metadata.get("kind", ""),
        "created_at": metadata.get(
            "created_at", datetime.now(timezone.utc).isoformat()
        ),
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


# ── Transform cache read/write ──


def write_transform_cache(
    df: pd.DataFrame,
    *,
    transform_id: str,
    cache_key: CacheKey,
    output_features: list[str],
    path: Path,
    context: FeatureCacheContext,
) -> None:
    """Write a transform-level cache parquet + meta sidecar.

    Validates:
    - ``trade_date`` and ``ts_code`` exist.
    - All ``output_features`` exist in *df*.
    """
    _required_columns(df, ["trade_date", "ts_code"], f"transform_cache:{transform_id}")
    _required_columns(df, output_features, f"transform_cache:{transform_id}")

    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    log.info("Wrote transform cache: %s → %s (%d cols)", transform_id, path, len(output_features))

    # Write meta sidecar
    meta_path = Path(str(path) + ".meta.json")
    metadata = {
        "_version": 1,
        "cache_key": cache_key.key,
        "kind": "transform",
        "transform_id": transform_id,
        "output_features": output_features,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context": {
            "source_manifest_hash": context.source_manifest_hash,
            "date_start": context.date_start,
            "date_end": context.date_end,
            "universe": context.universe,
        },
    }
    meta_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def read_transform_cache(
    *,
    path: Path,
    expected_cache_key: str,
    expected_features: list[str],
) -> pd.DataFrame:
    """Read and validate a transform-level cache.

    Raises ``ValueError`` on:
    - Missing meta sidecar.
    - Cache key mismatch.
    - Missing expected features.
    """
    meta_path = Path(str(path) + ".meta.json")
    _verify_metadata(meta_path, expected_cache_key)

    df = pd.read_parquet(path)
    _required_columns(df, ["trade_date", "ts_code"], "read_transform_cache")
    _required_columns(df, expected_features, "read_transform_cache")
    return df


# ── Matrix cache read/write ──


def write_matrix_cache(
    df: pd.DataFrame,
    *,
    feature_set_id: str,
    cache_key: CacheKey,
    resolved_features: list[str],
    path: Path,
    context: FeatureCacheContext,
) -> None:
    """Write a matrix-level cache parquet + meta sidecar.

    Validates:
    - ``trade_date`` and ``ts_code`` exist.
    - All ``resolved_features`` columns exist.
    - No extra columns beyond index + resolved_features.
    """
    _required_columns(df, ["trade_date", "ts_code"], f"matrix_cache:{feature_set_id}")
    _required_columns(df, resolved_features, f"matrix_cache:{feature_set_id}")

    # Check no unexpected columns
    allowed = {"trade_date", "ts_code"} | set(resolved_features)
    extra = [c for c in df.columns if c not in allowed]
    if extra:
        raise ValueError(
            f"Matrix cache '{feature_set_id}': unexpected columns {extra}. "
            f"Allowed: {sorted(allowed)}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    # Enforce fixed column order: index cols first, then features in order
    ordered_cols = ["trade_date", "ts_code"] + resolved_features
    df[ordered_cols].to_parquet(path, index=False)
    log.info("Wrote matrix cache: %s → %s (%d cols)", feature_set_id, path, len(resolved_features))

    meta_path = Path(str(path) + ".meta.json")
    metadata = {
        "_version": 1,
        "cache_key": cache_key.key,
        "kind": "matrix",
        "feature_set_id": feature_set_id,
        "resolved_features": resolved_features,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context": {
            "source_manifest_hash": context.source_manifest_hash,
            "date_start": context.date_start,
            "date_end": context.date_end,
            "universe": context.universe,
        },
    }
    meta_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def read_matrix_cache(
    *,
    path: Path,
    expected_cache_key: str,
    expected_features: list[str],
) -> pd.DataFrame:
    """Read and validate a matrix-level cache.

    Raises ``ValueError`` on:
    - Missing meta sidecar.
    - Cache key mismatch.
    - Missing expected features.
    """
    meta_path = Path(str(path) + ".meta.json")
    _verify_metadata(meta_path, expected_cache_key)

    df = pd.read_parquet(path)
    _required_columns(df, ["trade_date", "ts_code"], "read_matrix_cache")
    _required_columns(df, expected_features, "read_matrix_cache")
    return df
