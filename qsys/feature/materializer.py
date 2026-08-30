"""Feature cache materializer — compute and cache transforms and matrices.

This module materializes features using the existing builder path
(``build_phase1_features`` with flag dispatch), then writes the results
into transform-level and matrix-level caches.

It is a **standalone utility**, NOT wired into the default training or
inference paths.  Use ``--use-feature-cache`` / ``--materialize-feature-cache``
opt-in flags to enable.
"""

from __future__ import annotations

import multiprocessing
multiprocessing.set_start_method("fork", force=True)

import json
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd

from qsys.feature.cache import (
    FeatureCacheContext,
    compute_transform_cache_key,
    compute_matrix_cache_key,
    transform_cache_path,
    matrix_cache_path,
    cache_exists,
    read_transform_cache,
    write_transform_cache,
    write_matrix_cache,
    read_matrix_cache,
)
from qsys.feature.resolver_v2 import resolve_feature_set, discover_feature_sets
from qsys.feature.build_plan import build_plan_from_resolved
from qsys.feature.transform_registry import get_transform, is_registered, list_unresolved
from qsys.feature.manifest import build_feature_manifest, write_feature_manifest
from qsys.utils.logger import log


def materialize_feature_set_cache(
    raw_panel: pd.DataFrame,
    *,
    feature_set_id: str,
    date_start: str | None = None,
    date_end: str | None = None,
    universe: str | None = None,
    source_manifest_hash: str = "",
    builder_hash: str | None = None,
    cache_root: str | Path = "data/feature_cache",
    force: bool = False,
) -> dict:
    """Materialize a complete feature set into transform + matrix caches.

    This function:
    1. Resolves the FeatureSet YAML.
    2. Computes the matrix cache key.
    3. If cache hit and not ``force``, returns summary with hit=True.
    4. Otherwise, runs each required transform and writes caches.
    5. Assembles the final matrix, writes matrix cache + manifest.

    Parameters
    ----------
    raw_panel:
        Raw input DataFrame with at least ``trade_date``, ``ts_code``,
        and all raw features needed by the transforms.
    feature_set_id:
        Feature set ID to materialize.
    date_start, date_end:
        Date range for cache key (not used to filter *raw_panel*).
    universe:
        Universe for cache key.
    source_manifest_hash:
        Version hash of the source data.
    builder_hash:
        Version hash of builder code (optional).
    cache_root:
        Root directory for cache files.
    force:
        If ``True``, recompute even if cache hits.

    Returns
    -------
    dict
        Summary with keys: ``feature_set_id``, ``hit``, ``transform_count``,
        ``matrix_cache_path``, ``manifest_path``, ``resolved_features``,
        ``builder_mode``, etc.

    Raises
    ------
    ValueError
        On: unresolved features, cache validation failure, missing columns.
    """
    # 1. Resolve
    discover_feature_sets()
    resolved = resolve_feature_set(feature_set_id)
    plan = build_plan_from_resolved(resolved)

    # 2. Check unresolved transforms
    unresolved = list_unresolved(list(resolved.required_transforms))
    if unresolved:
        raise ValueError(
            f"Cannot materialize '{feature_set_id}': "
            f"unregistered transforms: {unresolved}. "
            f"Register them in qsys/feature/transform_registry.py first."
        )

    context = FeatureCacheContext(
        feature_set_id=resolved.feature_set_id,
        date_start=date_start,
        date_end=date_end,
        universe=universe,
        source_manifest_hash=source_manifest_hash,
        builder_hash=builder_hash,
    )

    # 3. Compute matrix cache key
    matrix_ck = compute_matrix_cache_key(
        resolved.feature_set_id,
        resolved_features=list(resolved.resolved_features),
        required_transforms=list(resolved.required_transforms),
        context=context,
    )
    matrix_path = matrix_cache_path(resolved.feature_set_id, matrix_ck.key, root=cache_root)

    # 4. Check cache hit
    if not force and cache_exists(matrix_path):
        return {
            "feature_set_id": resolved.feature_set_id,
            "hit": True,
            "matrix_cache_path": str(matrix_path),
            "resolved_features": list(resolved.resolved_features),
            "builder_mode": "legacy_flag_dispatch",
        }

    # 5. Materialize transforms (with transform-level cache read)
    materialized: dict[str, pd.DataFrame] = {}
    for tid in resolved.required_transforms:
        tspec = get_transform(tid)
        if tspec is None:
            raise ValueError(
                f"Transform '{tid}' not registered in transform_registry.py"
            )

        # Compute transform cache key using real compute_fn_hash from the spec
        transform_ck = compute_transform_cache_key(
            tid,
            input_features=list(tspec.input_features),
            output_features=list(tspec.output_features),
            compute_fn_hash=tspec.compute_fn_hash or source_manifest_hash,
            context=context,
        )
        t_path = transform_cache_path(tid, transform_ck.key, root=cache_root)

        # Try transform cache read first (skipped when force=True)
        if not force and cache_exists(t_path):
            cached = read_transform_cache(
                path=t_path,
                expected_cache_key=transform_ck.key,
                expected_features=list(tspec.output_features),
            )
            materialized[tid] = cached
            continue

        # Cache miss: compute and write
        # Transform expects columns without $ prefix (e.g. "close" not "$close")
        transform_input = raw_panel.copy()
        if any(c.startswith("$") for c in transform_input.columns):
            transform_input.columns = [c.lstrip("$") if c.startswith("$") else c for c in transform_input.columns]

        try:
            result = tspec.compute_fn(transform_input)
        except Exception as e:
            raise ValueError(
                f"Transform '{tid}' failed for '{feature_set_id}': {e}"
            ) from e

        # Filter output_features to only those actually produced
        actual_outputs = [f for f in tspec.output_features if f in result.columns]
        if not actual_outputs:
            raise ValueError(
                f"Transform '{tid}' produced none of its declared output features "
                f"({list(tspec.output_features)}). "
                f"Available columns: {list(result.columns)}"
            )
        if len(actual_outputs) < len(tspec.output_features):
            log.warning(
                "Transform '%s' missing %d/%d declared outputs. "
                "Missing: %s",
                tid,
                len(tspec.output_features) - len(actual_outputs),
                len(tspec.output_features),
                [f for f in tspec.output_features if f not in result.columns],
            )

        write_transform_cache(
            result,
            transform_id=tid,
            cache_key=transform_ck,
            output_features=actual_outputs,
            path=t_path,
            context=context,
        )
        materialized[tid] = result

    # 6. Assemble matrix — overlay transform outputs onto raw panel.
    # ALL resolved features (including qlib expressions) must be present
    # in the final matrix.  Qlib expressions must already be in raw_panel
    # (pre-computed or sourced from qlib) — the materializer does NOT
    # compute them.
    final_df = raw_panel.copy()
    for tid in resolved.required_transforms:
        tspec = get_transform(tid)
        if tspec is None:
            continue
        for col in tspec.output_features:
            if col in materialized[tid].columns:
                final_df[col] = materialized[tid][col].values

    # Validate ALL resolved features exist (including qlib expressions).
    # If raw_panel lacks them, fail fast.
    missing_features = [f for f in resolved.resolved_features if f not in final_df.columns]
    if missing_features:
        raise ValueError(
            f"Materialization of '{feature_set_id}' missing features: "
            f"{missing_features}. "
            f"Available: {list(final_df.columns)}"
        )

    # 7. Write matrix cache — columns order is fixed:
    #    ["trade_date", "ts_code"] + resolved_features
    matrix_cols = ["trade_date", "ts_code"] + list(resolved.resolved_features)
    matrix_df = final_df[matrix_cols].copy()
    write_matrix_cache(
        matrix_df,
        feature_set_id=resolved.feature_set_id,
        cache_key=matrix_ck,
        resolved_features=list(resolved.resolved_features),
        path=matrix_path,
        context=context,
    )

    # 8. Write manifest
    transform_keys = {}
    for tid in resolved.required_transforms:
        tspec = get_transform(tid)
        if tspec:
            tk = compute_transform_cache_key(
                tid,
                input_features=list(tspec.input_features),
                output_features=list(tspec.output_features),
                compute_fn_hash=tspec.compute_fn_hash or source_manifest_hash,
                context=context,
            )
            transform_keys[tid] = tk.key
    cache_info = {
        "enabled": True,
        "matrix_cache_key": matrix_ck.key,
        "transform_cache_keys": transform_keys,
        "cache_root": str(cache_root),
    }
    manifest = build_feature_manifest(resolved, plan, cache_info=cache_info)
    manifest_path = write_feature_manifest(
        manifest,
        output_dir=Path(cache_root).parent / "manifests",
    )

    return {
        "feature_set_id": resolved.feature_set_id,
        "hit": False,
        "transform_count": len(resolved.required_transforms),
        "matrix_cache_path": str(matrix_path),
        "manifest_path": str(manifest_path),
        "resolved_features": list(resolved.resolved_features),
        "builder_mode": "legacy_flag_dispatch",
        "warnings": list(plan.warnings),
    }
