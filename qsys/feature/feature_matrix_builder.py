"""Feature matrix builder — assemble a training matrix from per-feature caches.

Given a FeatureSet YAML, this module:
1. Resolves the YAML to a list of feature_ids.
2. For each feature_id, attempts to read from FeatureStore (with strict source hash).
3. On cache hit → use cached value.
4. On cache miss + ``compute_missing=True`` → compute in batch and write per-feature cache.
5. On cache miss + ``compute_missing=False`` → fail fast.
6. Joins all features by (trade_date, ts_code) into a wide matrix.
7. Optionally writes the assembled matrix to matrix cache.

**Mixed mode supported**: cached and missing features can coexist.
A single missing feature does NOT invalidate other cached features.
**No raw_panel as base** — matrix is assembled from feature data via inner join.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.feature.feature_store import (
    FeatureStore,
    FeatureCacheKey,
    compute_feature_cache_key,
)
from qsys.feature.feature_compute_registry import get_spec, has_spec, compute_phase1_batch, _PHASE1_HASH
from qsys.feature.resolver_v2 import resolve_feature_set, discover_feature_sets
from qsys.utils.logger import log


def build_matrix_from_feature_store(
    raw_panel: pd.DataFrame,
    *,
    feature_set_id: str,
    universe: str | None = None,
    date_start: str | None = None,
    date_end: str | None = None,
    source_manifest_hash: str = "",
    feature_cache_root: str | Path = "data/feature_cache/features",
    compute_missing: bool = False,
    allow_uncacheable: bool = False,
    join_policy: str = "inner",
    write_matrix_cache: bool = False,
    matrix_cache_root: str | Path = "data/feature_cache/matrices",
) -> pd.DataFrame:
    """Assemble a feature matrix from per-feature caches.

    Parameters
    ----------
    raw_panel:
        Raw input panel.  Only used when computing missing features;
        **not** used as the base for matrix assembly.
    feature_set_id:
        Feature set ID or YAML path.
    universe, date_start, date_end, source_manifest_hash:
        Cache key components.
    feature_cache_root:
        Per-feature cache root.
    compute_missing:
        If ``True``, compute missing features on the fly.
    allow_uncacheable:
        If ``True``, features without a compute spec are computed inline
        but NOT cached.  They still appear in the output matrix.
        If ``False`` (default), features without a spec raise ``ValueError``.
    join_policy:
        ``"inner"`` (default) — only keep (trade_date, ts_code) rows that
        have ALL features.  ``"left"`` — keep all rows from the first feature.
    write_matrix_cache:
        If ``True``, write the assembled matrix to optional matrix cache.
    matrix_cache_root:
        Optional matrix cache root.

    Returns
    -------
    pd.DataFrame
        Columns: ``["trade_date", "ts_code"] + resolved_features``.

    Raises
    ------
    ValueError
        Missing + no compute, uncacheable + no allow, join mismatch.
    """
    # 1. Resolve
    discover_feature_sets()
    resolved = resolve_feature_set(feature_set_id)
    feature_ids = list(resolved.resolved_features)

    store = FeatureStore(root=feature_cache_root)

    # 2. Classify features: cached, missing, uncacheable
    cached: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    missing_spec: list[str] = []  # has spec but not cached
    missing_no_spec: list[str] = []  # no spec at all
    uncacheable: list[str] = []

    for fid in feature_ids:
        fk = FeatureCacheKey(
            feature_id=fid,
            universe=universe,
            source_manifest_hash=source_manifest_hash,
            compute_fn_hash=_PHASE1_HASH,
        )
        ck = compute_feature_cache_key(fk)

        if store.exists(fid, ck):
            df = store.read_feature(
                fid,
                expected_cache_key=ck,
                strict_source_hash=source_manifest_hash,
                date_start=date_start,
                date_end=date_end,
            )
            cached[fid] = df[["trade_date", "ts_code", fid]]
            continue

        # Cache miss
        spec = get_spec(fid)
        if spec is None:
            if allow_uncacheable:
                uncacheable.append(fid)
            else:
                missing_no_spec.append(fid)
        else:
            missing_spec.append(fid)

    # 3. Fail fast on no-spec
    if missing_no_spec:
        raise ValueError(
            f"FeatureSet '{feature_set_id}': {len(missing_no_spec)} features "
            f"have no compute spec and allow_uncacheable=False. "
            f"Missing: {missing_no_spec}. "
            f"Register in feature_compute_registry.py or set allow_uncacheable=True."
        )

    # 4. Compute missing (batch mode)
    if missing_spec and compute_missing:
        log.info(
            "Batch-computing %d missing features via phase1 builder...",
            len(missing_spec),
        )
        # Deduplicate raw_panel to prevent row explosion on join
        clean_panel = raw_panel.drop_duplicates(subset=["trade_date", "ts_code"]).copy()
        batch_result = compute_phase1_batch(clean_panel, missing_spec)
        for fid in missing_spec:
            if fid not in batch_result.columns:
                raise ValueError(
                    f"Phase1 builder did not produce '{fid}' during batch compute. "
                    f"Available: {list(batch_result.columns)}"
                )
            fk = FeatureCacheKey(
                feature_id=fid,
                date_end=date_end,
                source_manifest_hash=source_manifest_hash,
                compute_fn_hash=_PHASE1_HASH,
            )
            ck = compute_feature_cache_key(fk)
            df_part = batch_result[["trade_date", "ts_code", fid]]
            store.write_feature(
                fid,
                df_part,
                cache_key=ck,
                metadata={
                    "source_manifest_hash": source_manifest_hash,
                    "compute_fn_hash": _PHASE1_HASH,
                    "universe": universe,
                    "date_start": date_start,
                    "date_end": date_end,
                    "pit_policy": "rolling_past",
                },
            )
            cached[fid] = df_part
    elif missing_spec and not compute_missing:
        missing.extend(missing_spec)

    # 5. Handle uncacheable — qlib raw fields ($ prefix) read from panel
    if uncacheable:
        log.info("Handling %d uncacheable features...", len(uncacheable))
        raw_panel_clean = raw_panel.copy()
        rename_map = {c: c[1:] for c in raw_panel_clean.columns if c.startswith("$")}
        if rename_map:
            raw_panel_clean = raw_panel_clean.rename(columns=rename_map)

        for fid in uncacheable:
            if fid.startswith("$"):
                clean_name = fid[1:]
                if clean_name in raw_panel_clean.columns:
                    cached[fid] = raw_panel_clean[["trade_date", "ts_code", clean_name]].rename(columns={clean_name: fid})
                    log.info("  Raw field (from panel): %s", fid)
                    continue
            # Builder must produce it
            clean_panel = raw_panel.drop_duplicates(subset=["trade_date", "ts_code"]).copy()
            batch_result = compute_phase1_batch(clean_panel, [fid])
            if fid not in batch_result.columns:
                raise ValueError(
                    f"Uncacheable feature '{fid}' has no compute spec and is not a raw field. "
                    f"Available: {list(batch_result.columns)}"
                )
            cached[fid] = batch_result[["trade_date", "ts_code", fid]]
            log.info("  Computed (uncached): %s", fid)

    # 6. Handle missing (hard fail)
    if missing:
        raise ValueError(
            f"FeatureSet '{feature_set_id}': {len(missing)} features missing "
            f"from cache and compute_missing=False. Missing: {missing}"
        )

    # 7. Join into wide matrix — from assembled feature data, NOT raw_panel
    if not cached:
        raise ValueError(f"FeatureSet '{feature_set_id}': no features assembled")

    # Build index from all cached feature (trade_date, ts_code) pairs
    # Use inner join by default: only keep rows present in ALL features
    first_fid = feature_ids[0]
    if first_fid in cached:
        matrix = cached[first_fid][["trade_date", "ts_code"]].drop_duplicates().copy()
    else:
        # Find the first available feature for the base
        for fid in feature_ids:
            if fid in cached:
                matrix = cached[fid][["trade_date", "ts_code"]].drop_duplicates().copy()
                break

    for fid in feature_ids:
        if fid in cached:
            matrix = matrix.merge(
                cached[fid],
                on=["trade_date", "ts_code"],
                how=join_policy,
            )
    # 8. Write optional matrix cache
    if write_matrix_cache:
        from qsys.feature.cache import (
            FeatureCacheContext,
            compute_matrix_cache_key,
            matrix_cache_path,
            write_matrix_cache,
        )

        ctx = FeatureCacheContext(
            feature_set_id=resolved.feature_set_id,
            date_start=date_start,
            date_end=date_end,
            universe=universe,
            source_manifest_hash=source_manifest_hash,
        )
        mck = compute_matrix_cache_key(
            resolved.feature_set_id,
            resolved_features=feature_ids,
            required_transforms=[],
            context=ctx,
        )
        mpath = matrix_cache_path(resolved.feature_set_id, mck.key, root=matrix_cache_root)
        write_matrix_cache(
            matrix,
            feature_set_id=resolved.feature_set_id,
            cache_key=mck,
            resolved_features=feature_ids,
            path=mpath,
            context=ctx,
        )
        log.info("Optional matrix cache written: %s", mpath)

    return matrix
