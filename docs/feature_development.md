# Feature Development Contract

> Part of Feature Framework refactoring (Phase 1+2)
> See also: [Feature Registry Design](feature_registry_design.md), [Feature Cache Design](feature_cache_design.md), [Agent Checklist](agent_feature_development_checklist.md)

## 1. Raw vs Derived Feature Definitions

### Raw Feature

A **raw feature** comes directly from a data source without business logic computation.

- **Source**: qlib raw fields (`$close`), fina_indicator (`$roe`), margin data, shareholder parquet files
- **Computation**: None (at feature level; qlib may apply forward-fill via ann_date)
- **PIT**: Already PIT-safe at data layer
- **Cache**: Not cached (re-read from source on each run)
- **Examples**: `$close`, `$roe`, `margin_balance`, `holder_num`

### Derived Feature

A **derived feature** is computed from raw features or other derived features.

- **Source**: One or more raw/derived features
- **Computation**: Python function in `qsys/feature/groups/`
- **PIT**: Must be ensured by implementation (never use future data)
- **Cache**: Eligible for caching (expensive ones should be cached)
- **Examples**: `ret_60d`, `rps_industry_60d`, `margin_crowding_score`

## 2. PIT Rules (Strict)

### Rule 1: Rolling features use past only
```python
# ✅ Correct
out.groupby("ts_code")["close"].transform(
    lambda s: s.rolling(60).mean()
)
# ❌ Wrong — no groupby, cross-stock contamination
out["close"].rolling(60).mean()
```

### Rule 2: Cross-sectional features group by trade_date
```python
# ✅ Correct
out.groupby("trade_date")["ret_60d"].rank(pct=True)
# ❌ Wrong — no trade_date groupby, cross-date contamination
out["ret_60d"].rank(pct=True)
```

### Rule 3: Industry aggregation is two-stage
```python
# Stage 1: Cross-sectional per date×industry
ind_panel = out.groupby(["trade_date", "industry"]).agg(
    ind_ret=("_daily_ret", "mean"),
).reset_index()
# Stage 2: Temporal rolling per industry
ind_panel.groupby("industry")["ind_ret"].transform(
    lambda s: s.rolling(20).mean()
)
# ❌ Wrong — single-stage groupby(industry) mixes dates
out.groupby("industry")["ret_1d"].transform("mean")
```

### Rule 4: Financial features use ann_date
```python
# ✅ Correct — merge_asof backward on ann_date
pd.merge_asof(left, right, on="_dt", by="_inst", direction="backward")
# ❌ Wrong — using end_date as visible date
# end_date is not the date the market knows the data
```

### Rule 5: Quarterly yoy on report-level, not daily
```python
# ✅ Correct
report_df.sort_values(["inst", "end_date"])
report_df["yoy"] = report_df.groupby("inst")["value"].pct_change(4)
# ❌ Wrong — pct_change(4) on daily PIT-expanded data
# 4 trading days ≠ 4 quarters
```

### Rule 6: No semantic proxy without naming it
```python
# ❌ Wrong — free_cashflow is NOT capex
out["capex_to_assets"] = free_cashflow / total_assets  # semantically wrong
# ✅ Correct — name says what it is
out["free_cashflow_to_assets"] = free_cashflow / total_assets
# Or skip if the real field is unavailable
```

## 3. Feature Registry Requirements

Every feature MUST have:

1. A `FeatureSpec` entry in `registry_v2.FEATURE_REGISTRY`
2. All fields populated (no `None` for required fields)
3. `dependencies` listing all raw/derived inputs
4. A `compute_fn` path for derived features
5. Appropriate `pit_type` classification
6. Accurate `cache_scope`
7. A meaningful `description`

## 4. Feature List YAML Requirements

Every YAML file MUST:

1. Reference only features that exist in the registry
2. Be validated by `validate_feature_list()` against registry
3. Not include features with status="broken"
4. Default to excluding features with status="experimental" (in production configs)

## 5. Code Organization

### Where to Put New Features

| Feature Type | Location |
|-------------|----------|
| New group of features | `qsys/feature/groups/{group_name}.py` |
| Transformation helper | `qsys/feature/transforms.py` |
| Feature list config | `configs/features/{feature_list_id}.yaml` |
| Registry entry | `qsys/feature/registry_v2.py` |
| Legacy registry entry | `qsys/feature/registry.py` FEATURE_GROUPS |
| Builder hook | `qsys/feature/builder.py` (legacy) |
| Unit test | `tests/features/test_{group_name}.py` |

### Group Module Contract

Every group module must export a `build_*` function with signature:

```python
def build_<group_name>_features(df: pd.DataFrame) -> pd.DataFrame:
    """One-sentence description.
    
    PIT: <explicit PIT rules for this group>
    Dependencies: <list of required input columns>
    """
    out = df.copy()
    # ... feature computations ...
    return out
```

Rules:
1. Input `df` must not be mutated (copy at function start)
2. All intermediate columns prefixed with `_` must be dropped at end
3. All NaN/Inf handling must be explicit
4. `groupby("ts_code")` for per-instrument operations
5. `groupby("trade_date")` for cross-sectional operations
6. `groupby(["trade_date", "industry"])` for industry aggregations

## 6. Test Requirements

Every feature group must have:

1. **Basic test**: Correct number of features, no NaN explosion
2. **PIT test**: No future data used
3. **Group validation test**: Features match registry declaration

Plus cross-cutting tests:

4. **Registry consistency**: IDs unique, deps exist, YAML matches
5. **Builder isolation**: No cross-contamination between groups
6. **Industry aggregation contract**: Two-stage methodology
7. **Financial statement semantics**: ann_date, report-level yoy

## 7. PR Requirements

Every feature addition PR must:

1. Include registry entry
2. Include YAML config update (if new feature list)
3. Include builder hook (if new group)
4. Include tests for the feature
5. Update this doc if any contract rule is extended
6. Include PR body with: feature_ids, sources, dependencies, cache policy, PIT rules
