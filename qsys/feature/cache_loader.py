"""Cache-aware feature matrix loader for the research pipeline.

**Opt-in only** — default paths are untouched.

Usage::
    from qsys.feature.cache_loader import load_feature_matrix_with_cache

    matrix = load_feature_matrix_with_cache(
        raw_panel,
        feature_set_id="value_growth_multibagger_v3a_features",
        source_manifest_hash="src_v1",
        use_feature_cache=True,
        materialize_on_miss=True,
    )

Behavior matrix::

    use_feature_cache  |  cache status  |  materialize_on_miss  |  result
    -------------------+----------------+-----------------------+-------------------
    False              |  (ignored)     |  (ignored)            |  existing builder
    True               |  hit           |  (ignored)            |  cached matrix
    True               |  miss          |  False                |  FAIL (ValueError)
    True               |  miss          |  True                 |  auto-materialize
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qsys.feature.cache import (
    FeatureCacheContext,
    compute_matrix_cache_key,
    matrix_cache_path,
    cache_exists,
    read_matrix_cache,
)
from qsys.feature.resolver_v2 import resolve_feature_set, discover_feature_sets
from qsys.feature.build_plan import build_plan_from_resolved
from qsys.feature.validate import validate_feature_matrix_columns
from qsys.utils.logger import log


def load_feature_matrix_with_cache(
    raw_panel: pd.DataFrame,
    *,
    feature_set_id: str,
    date_start: str | None = None,
    date_end: str | None = None,
    universe: str | None = None,
    source_manifest_hash: str = "",
    cache_root: str | Path = "data/feature_cache",
    use_feature_cache: bool = False,
    materialize_on_miss: bool = False,
) -> pd.DataFrame:
    """Load a feature matrix, optionally via the feature cache.

    Parameters
    ----------
    raw_panel:
        Raw input DataFrame.  Only used when cache is disabled or when
        ``materialize_on_miss=True`` and cache misses.
    feature_set_id:
        Feature set ID or YAML path.
    date_start, date_end, universe:
        Cache key parameters.
    source_manifest_hash:
        Version hash of the source data.  Must be deterministic for cache
        reproducibility.
    cache_root:
        Root directory for cache files.
    use_feature_cache:
        If ``False`` (default), returns ``raw_panel`` unchanged (existing
        path).  If ``True``, attempts to load from cache.
    materialize_on_miss:
        If ``True``, automatically materializes on cache miss.  Only
        meaningful when ``use_feature_cache=True``.

    Returns
    -------
    pd.DataFrame
        Feature matrix with columns ``["trade_date", "ts_code"] +
        resolved_features``.

    Raises
    ------
    ValueError
        On cache miss when ``materialize_on_miss=False``, or on column
        validation failure.
    """
    if not use_feature_cache:
        log.info("Feature cache disabled (use_feature_cache=False) — using existing path")
        return raw_panel

    # ── Resolve feature set ──
    discover_feature_sets()
    resolved = resolve_feature_set(feature_set_id)
    plan = build_plan_from_resolved(resolved)

    context = FeatureCacheContext(
        feature_set_id=resolved.feature_set_id,
        date_start=date_start,
        date_end=date_end,
        universe=universe,
        source_manifest_hash=source_manifest_hash,
    )

    matrix_ck = compute_matrix_cache_key(
        resolved.feature_set_id,
        resolved_features=list(resolved.resolved_features),
        required_transforms=list(resolved.required_transforms),
        context=context,
    )
    matrix_path = matrix_cache_path(resolved.feature_set_id, matrix_ck.key, root=cache_root)

    # ── Check cache hit ──
    if cache_exists(matrix_path):
        log.info(
            "Feature cache HIT: %s (key=%s, %d features, path=%s)",
            resolved.feature_set_id, matrix_ck.key,
            len(resolved.resolved_features), matrix_path,
        )
        df = read_matrix_cache(
            path=matrix_path,
            expected_cache_key=matrix_ck.key,
            expected_features=list(resolved.resolved_features),
        )
        return validate_feature_matrix_columns(df, list(resolved.resolved_features))

    # ── Cache miss ──
    log.warning(
        "Feature cache MISS: %s (key=%s)",
        resolved.feature_set_id, matrix_ck.key,
    )

    if not materialize_on_miss:
        raise ValueError(
            f"Feature cache MISS for '{resolved.feature_set_id}' "
            f"(key={matrix_ck.key}) and materialize_on_miss=False. "
            f"Either:\n"
            f"  1. Run backfill_feature_cache.py first\n"
            f"  2. Or set materialize_on_miss=True"
        )

    # ── Auto-materialize ──
    log.info("Auto-materializing feature cache for '%s' ...", resolved.feature_set_id)
    from qsys.feature.materializer import materialize_feature_set_cache  # noqa: PLC0415

    mat_result = materialize_feature_set_cache(
        raw_panel,
        feature_set_id=feature_set_id,
        date_start=date_start,
        date_end=date_end,
        universe=universe,
        source_manifest_hash=source_manifest_hash,
        cache_root=cache_root,
        force=False,
    )

    if not mat_result.get("hit") and mat_result.get("transform_count", 0) == 0:
        log.warning(
            "Materialization produced no new transforms (existing cache may "
            "have been reused)"
        )

    # Read back from cache
    df = read_matrix_cache(
        path=matrix_path,
        expected_cache_key=matrix_ck.key,
        expected_features=list(resolved.resolved_features),
    )
    return validate_feature_matrix_columns(df, list(resolved.resolved_features))
