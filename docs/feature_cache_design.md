# Feature Cache Design

> Part of Feature Framework refactoring (Phase 1+2: Design; Phase 3: Implementation)
> See also: [Feature Registry Design](feature_registry_design.md), [Feature Development](feature_development.md)

## 1. Problem

Current derived features are recomputed every research run. With 67 evaluation windows, each triggering a full feature build:

- Rolling windows (60d, 120d, 252d, 756d) computed 67× from scratch
- Cross-sectional ranks and scores computed 67× from scratch
- Industry aggregation recomputed 67× from scratch
- Typical compute time: O(N_windows × N_features × N_stocks × N_days)

For expensive features (industry momentum, 756d percentiles, cross-group interactions), this makes iterative research slow and wasteful.

## 2. Cache Architecture

### 2.1 Two-Layer Design

```
Layer 1: Per-Feature Cache
  data/feature_cache/features/{feature_id}/{cache_key}.parquet
  Columns: trade_date, ts_code, feature_value

Layer 2: Feature Matrix Cache
  data/feature_cache/matrices/{feature_list_id}_{cache_key}/panel.parquet
  Columns: trade_date, ts_code, feature_1, ..., feature_N
```

### 2.2 Cache Key

```python
cache_key = hash(
    feature_id
    + compute_fn_hash          # SHA256 of the compute function source
    + dependencies_hash        # SHA256 of all dependency values
    + date_range               # "2020-01-01_2025-12-31"
    + universe                 # "csi500"
    + pit_policy               # "backward"
)
```

The key ensures:
- **Feature code change → cache miss** (compute_fn_hash)
- **Dependency data change → cache miss** (dependencies_hash)
- **Different date range → cache miss** (date_range)
- **Different universe → cache miss** (universe)

### 2.3 compute_fn_hash

```python
def _compute_fn_hash(compute_fn_path: str) -> str:
    """Hash the source code of the compute function."""
    import hashlib, inspect
    mod_path, fn_name = compute_fn_path.rsplit(".", 1)
    module = importlib.import_module(f"qsys.feature.{mod_path}")
    fn = getattr(module, fn_name)
    source = inspect.getsource(fn)
    return hashlib.sha256(source.encode()).hexdigest()[:16]
```

This means any change to the compute function's source code invalidates the cache. Changes to imports, comments, or docstrings do NOT invalidate (same `getsource` output for those parts).

## 3. API Design

### Per-Feature Cache

```python
def has_feature(
    feature_id: str,
    date_range: tuple[str, str],
    universe: str,
) -> bool

def load_feature(
    feature_id: str,
    date_range: tuple[str, str],
    universe: str,
) -> pd.DataFrame | None

def save_feature(
    feature_id: str,
    df: pd.DataFrame,              # columns: trade_date, ts_code, feature_value
    date_range: tuple[str, str],
    universe: str,
) -> None

def get_feature_cache_key(
    spec: FeatureSpec,
    context: FeatureBuildContext,
) -> str
```

### Feature Matrix Cache

```python
def has_matrix(
    feature_list_id: str,
    date_range: tuple[str, str],
    universe: str,
) -> bool

def load_matrix(
    feature_list_id: str,
    date_range: tuple[str, str],
    universe: str,
) -> pd.DataFrame | None

def save_matrix(
    feature_list_id: str,
    df: pd.DataFrame,
    date_range: tuple[str, str],
    universe: str,
    feature_ids: list[str],
) -> None
```

## 4. Cache Storage Paths

```
data/feature_cache/
├── features/
│   ├── relative_strength__ret_60d/
│   │   ├── a1b2c3d4e5f6a7b8.parquet
│   │   └── f9e8d7c6b5a4f3e2.parquet
│   ├── industry_momentum__industry_ret_20d/
│   │   └── ...
│   └── ...
├── matrices/
│   ├── value_growth_multibagger_v3a_features_a1b2c3d4/
│   │   └── panel.parquet
│   └── ...
```

## 5. Cache Invalidation

### Automatic Invalidation Triggers

1. **Code change**: compute_fn_hash changes
2. **Dependency data change**: source parquet file mtime or content hash
3. **Explicit invalidation**: `clear_feature_cache(feature_id)`

### Staleness Detection

```python
def is_cache_stale(
    feature_id: str,
    cache_path: Path,
) -> bool:
    """Check if source data or code is newer than cache."""
    cache_mtime = cache_path.stat().st_mtime
    spec = get_feature(feature_id)
    if spec.compute_fn:
        fn_mtime = _compute_fn_mtime(spec.compute_fn)
        if fn_mtime > cache_mtime:
            return True
    return False
```

## 6. Integration with Builder

### New Flow (Phase 4)

```python
def build_features_from_feature_list(
    raw_panel: pd.DataFrame,
    feature_list_id: str,
    context: FeatureBuildContext,
    use_cache: bool = True,
) -> tuple[pd.DataFrame, FeatureManifest]:

    spec_list = resolve_feature_list_to_specs(feature_list_id)

    for spec in spec_list:
        if spec.kind == "raw":
            continue  # already in raw_panel
        if use_cache and has_feature(spec.feature_id, context):
            cached = load_feature(spec.feature_id, context)
            result[spec.name] = cached
        else:
            computed = compute_feature(spec, raw_panel)
            if use_cache:
                save_feature(spec.feature_id, computed, context)
            result[spec.name] = computed

    return pd.DataFrame(result), build_manifest(spec_list)
```

## 7. Legacy Cache (cache.py)

The existing `cache.py` at `data/canonical/features/<universe>/<hash>.parquet` is a simpler per-query cache used by `QlibAdapter`. It remains functional but is **not replaced** — Phase 3 cache is an additional layer for expensive derived features only.

## 8. Future: Warming

A `warm_feature_cache.py` script can pre-compute expensive features for standard universes/date ranges:

```bash
python scripts/warm_feature_cache.py \
    --feature-list value_growth_multibagger_v3a_features \
    --universes csi500 csi800 \
    --start 2018-01-01 --end 2026-06-01
```

This is Phase 3+ scope.
