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



def _build_cache_key(
    feature_id: str,
    *,
    universe: str | None = None,
    source_manifest_hash: str = "",
) -> tuple[FeatureCacheKey, str]:
    """Build a unified FeatureCacheKey + its computed key string."""
    fk = FeatureCacheKey(
        feature_id=feature_id,
        universe=universe,
        source_manifest_hash=source_manifest_hash,
        compute_fn_hash=_PHASE1_HASH,
        pit_policy="rolling_past",
    )
    return fk, compute_feature_cache_key(fk)


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
    join_policy: str = "left",
    anchor_df: pd.DataFrame | None = None,
    write_matrix_cache: bool = False,
    matrix_cache_root: str | Path = "data/feature_cache/matrices",
) -> pd.DataFrame:
    """Assemble a feature matrix from per-feature caches.

    Parameters
    ----------
    raw_panel:
        Raw input panel.  Used for computing missing features and as
        the anchor row set (when ``anchor_df`` is not provided).
    feature_set_id:
        Feature set ID or YAML path.
    universe, date_start, date_end, source_manifest_hash:
        Cache key components.
    feature_cache_root:
        Per-feature cache root.
    compute_missing:
        If ``True``, compute missing features on the fly.
    allow_uncacheable:
        If ``True``, features without a compute spec are used from the
        raw panel or computed inline (not cached).
    join_policy:
        Only ``"left"`` (default) is supported — the matrix is anchored
        to *anchor_df* or *raw_panel* rows, and feature values are
        left-joined.  This guarantees exact row count parity with the
        no-cache path.
    anchor_df:
        Optional explicit anchor DataFrame.  Must have ``trade_date``
        and ``ts_code`` columns.  When provided, the output contains
        exactly the (trade_date, ts_code) pairs from this anchor.
        When ``None``, uses ``raw_panel`` deduped on
        (trade_date, ts_code) as the anchor.
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
        fk, ck = _build_cache_key(fid, universe=universe, source_manifest_hash=source_manifest_hash)

        if store.exists(fid, ck):
            df = store.read_feature(
                fid,
                expected_cache_key=ck,
                strict_source_hash=source_manifest_hash,
                # NOTE: no date_start/date_end — read FULL backfill range
                # so rank-based features reflect the full universe.
                # Date filtering is handled by the anchor below.
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
            fk, ck = _build_cache_key(fid, universe=universe, source_manifest_hash=source_manifest_hash)
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
                    df = raw_panel_clean[["trade_date", "ts_code", clean_name]].drop_duplicates(
                        subset=["trade_date", "ts_code"]
                    ).rename(columns={clean_name: fid})
                    cached[fid] = df
                    log.info("  Raw field (from panel): %s (%d rows)", fid, len(df))
                    continue
                if fid in raw_panel.columns:
                    df = raw_panel[[
                        "trade_date", "ts_code", fid
                    ]].drop_duplicates(subset=["trade_date", "ts_code"]).copy()
                    cached[fid] = df
                    log.info("  Raw field (from panel, $ name): %s (%d rows)", fid, len(df))
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

    # 7. Build anchor — the exact (trade_date, ts_code) rows the output must have.
    #    When anchor_df is provided, use it directly; otherwise use raw_panel.
    if anchor_df is not None:
        anchor = anchor_df[["trade_date", "ts_code"]].drop_duplicates().copy()
    else:
        anchor = raw_panel[["trade_date", "ts_code"]].drop_duplicates().copy()
    anchor["trade_date"] = anchor["trade_date"].astype(str)

    # Apply optional date range filter to anchor only.
    # Feature values are read from cache WITHOUT date filter (full range),
    # because rank-based features depend on the full universe range.
    # Date filtering happens on the anchor (row set), not on the cached values.
    if date_start is not None:
        anchor = anchor[anchor["trade_date"] >= date_start]
    if date_end is not None:
        anchor = anchor[anchor["trade_date"] <= date_end]

    if anchor.empty:
        raise ValueError(f"FeatureSet '{feature_set_id}': anchor has zero rows")

    log.info(
        "Matrix anchor: %d rows (from %s, date=[%s, %s])",
        len(anchor), "explicit anchor_df" if anchor_df is not None else "raw_panel",
        date_start or "all", date_end or "all",
    )

    # 8. Left-join every feature onto the anchor.
    #    Features are read from cache WITHOUT date_start/date_end so that
    #    rank-based features reflect the full backfill range.
    #    This guarantees exact value parity with the no-cache builder path.
    for fid in feature_ids:
        if fid in cached:
            src = cached[fid].copy()
            src["trade_date"] = src["trade_date"].astype(str)
            anchor = anchor.merge(
                src[["trade_date", "ts_code", fid]],
                on=["trade_date", "ts_code"],
                how="left",
            )

    matrix = anchor.copy()

    # 9. Write optional matrix cache
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
