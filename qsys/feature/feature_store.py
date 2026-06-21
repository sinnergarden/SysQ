"""FeatureStore — per-feature cache, the atomic unit of feature caching.

Each ``feature_id`` is the smallest cache unit.  A FeatureSet YAML is just
a list of feature_ids; the training matrix is assembled from multiple
per-feature cache entries.

Storage layout::

    data/feature_cache/features/{feature_id}/{cache_key}.parquet
    data/feature_cache/features/{feature_id}/{cache_key}.meta.json

Parquet schema (per-feature, narrow)::

    trade_date  |  ts_code  |  value

Or (per-feature, wide)::

    trade_date  |  ts_code  |  {feature_id}

FeatureStore reads return ``trade_date, ts_code, {feature_id}`` regardless
of which internal schema was used.

Rules (all fail fast):
1. meta.json must exist.
2. ``cache_key`` must match.
3. ``feature_id`` must match.
4. ``source_manifest_hash`` must match (optional strict mode).
5. ``trade_date`` and ``ts_code`` must be present.
6. No silent fallback.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class FeatureCacheKey:
    """Deterministic key components for a single-feature cache entry.

    All fields are included in the SHA-256 hash via ``compute_feature_cache_key()``.
    """

    feature_id: str
    universe: str | None = None
    date_start: str | None = None
    date_end: str | None = None
    source_manifest_hash: str = ""
    compute_fn_hash: str = ""
    pit_policy: str = ""
    frequency: str = "daily"


def compute_feature_cache_key(k: FeatureCacheKey) -> str:
    """Compute a deterministic SHA-256 cache key from ``FeatureCacheKey``.

    Two identical keys always produce the same hash.
    Changing any field produces a different hash.
    """
    raw = {
        "feature_id": k.feature_id,
        "universe": k.universe or "",
        "date_start": k.date_start or "",
        "date_end": k.date_end or "",
        "source_manifest_hash": k.source_manifest_hash,
        "compute_fn_hash": k.compute_fn_hash,
        "pit_policy": k.pit_policy or "",
        "frequency": k.frequency,
    }
    serialized = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:20]


class FeatureStore:
    """Per-feature cache store.

    Usage::

        store = FeatureStore(root="data/feature_cache/features")

        # Write
        store.write_feature("ret_60d", df, cache_key="abc", metadata={...})

        # Read
        df = store.read_feature("ret_60d", expected_cache_key="abc")

        # Check
        exists = store.exists("ret_60d", "abc")
    """

    def __init__(self, root: str | Path = "data/feature_cache/features") -> None:
        self._root = Path(root)

    # ── Path helpers ──

    def feature_path(self, feature_id: str, cache_key: str) -> Path:
        """Return the parquet path for *feature_id* + *cache_key*."""
        return self._root / feature_id / f"{cache_key}.parquet"

    def _meta_path(self, path: Path) -> Path:
        return Path(str(path) + ".meta.json")

    # ── Existence ──

    def exists(self, feature_id: str, cache_key: str) -> bool:
        """Check if both parquet and meta exist for this feature + key."""
        path = self.feature_path(feature_id, cache_key)
        return path.exists() and self._meta_path(path).exists()

    # ── Read ──

    def read_feature(
        self,
        feature_id: str,
        *,
        expected_cache_key: str,
        strict_source_hash: str | None = None,
    ) -> pd.DataFrame:
        """Read a single feature from cache.

        Returns a DataFrame with columns ``[trade_date, ts_code, {feature_id}]``.

        Raises ``ValueError`` on validation failure.
        """
        path = self.feature_path(feature_id, expected_cache_key)
        meta_path = self._meta_path(path)

        # 1. Meta must exist
        if not meta_path.exists():
            raise ValueError(
                f"Feature '{feature_id}': meta.json not found at {meta_path}"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        # 2. Cache key match
        if meta.get("cache_key") != expected_cache_key:
            raise ValueError(
                f"Feature '{feature_id}': cache_key mismatch "
                f"(expected={expected_cache_key}, meta={meta.get('cache_key')})"
            )

        # 3. Feature ID match
        if meta.get("feature_id") != feature_id:
            raise ValueError(
                f"Cache meta feature_id mismatch: "
                f"expected={feature_id}, meta={meta.get('feature_id')}"
            )

        # 4. Optional strict source hash
        if strict_source_hash is not None:
            meta_hash = meta.get("source_manifest_hash", "")
            if meta_hash != strict_source_hash:
                raise ValueError(
                    f"Feature '{feature_id}': source_manifest_hash mismatch "
                    f"(expected={strict_source_hash}, meta={meta_hash})"
                )

        # 5. Parquet must exist
        if not path.exists():
            raise ValueError(
                f"Feature '{feature_id}': parquet not found at {path}"
            )

        df = pd.read_parquet(path)

        # 6. Required columns
        if "trade_date" not in df.columns:
            raise ValueError(f"Feature '{feature_id}': missing trade_date column")
        if "ts_code" not in df.columns:
            raise ValueError(f"Feature '{feature_id}': missing ts_code column")

        # 7. Ensure feature column exists
        if feature_id not in df.columns:
            # Narrow schema (value column): rename "value" → feature_id
            if "value" in df.columns:
                df = df.rename(columns={"value": feature_id})
            else:
                raise ValueError(
                    f"Feature '{feature_id}': neither '{feature_id}' nor 'value' "
                    f"column in cache. Columns: {list(df.columns)}"
                )

        return df

    # ── Write ──

    def write_feature(
        self,
        feature_id: str,
        df: pd.DataFrame,
        *,
        cache_key: str,
        metadata: dict,
        overwrite: bool = False,
    ) -> Path:
        """Write a single feature to cache.

        ``df`` must have ``trade_date`` and ``ts_code`` columns, plus either
        a ``value`` column or a column named ``feature_id``.

        Returns the path to the written parquet.
        """
        if "trade_date" not in df.columns:
            raise ValueError(f"Feature '{feature_id}': df missing trade_date")
        if "ts_code" not in df.columns:
            raise ValueError(f"Feature '{feature_id}': df missing ts_code")

        # Ensure feature column exists
        if feature_id not in df.columns and "value" not in df.columns:
            raise ValueError(
                f"Feature '{feature_id}': df has neither '{feature_id}' "
                f"nor 'value' column. Columns: {list(df.columns)}"
            )

        path = self.feature_path(feature_id, cache_key)
        if path.exists() and not overwrite:
            # Validate existing cache — orphan parquet or mismatched meta must fail
            try:
                self.read_feature(
                    feature_id,
                    expected_cache_key=cache_key,
                    strict_source_hash=metadata.get("source_manifest_hash"),
                )
                return path
            except (ValueError, FileNotFoundError) as e:
                raise ValueError(
                    f"Feature '{feature_id}': existing cache at {path.name} "
                    f"is invalid (read_feature failed: {e}). "
                    f"Set overwrite=True to replace it."
                ) from e

        path.parent.mkdir(parents=True, exist_ok=True)

        # Write parquet — ensure output has trade_date, ts_code, feature_id
        out_cols = ["trade_date", "ts_code"]
        if feature_id in df.columns:
            out_cols.append(feature_id)
        elif "value" in df.columns:
            out_cols.append("value")
        df[out_cols].to_parquet(path, index=False)

        # Write meta sidecar
        meta_path = self._meta_path(path)
        payload = {
            "feature_id": feature_id,
            "cache_key": cache_key,
            "source_manifest_hash": metadata.get("source_manifest_hash", ""),
            "compute_fn_hash": metadata.get("compute_fn_hash", ""),
            "universe": metadata.get("universe"),
            "date_start": metadata.get("date_start"),
            "date_end": metadata.get("date_end"),
            "row_count": len(df),
            "columns": out_cols,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "pit_policy": metadata.get("pit_policy", ""),
            "frequency": metadata.get("frequency", "daily"),
        }
        meta_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

        return path

    # ── Listing ──

    def list_feature_ids(self) -> list[str]:
        """Return all feature_ids that have at least one cache entry."""
        if not self._root.exists():
            return []
        return sorted(
            d.name for d in self._root.iterdir() if d.is_dir()
        )
