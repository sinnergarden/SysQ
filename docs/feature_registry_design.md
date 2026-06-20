# Feature Registry Design

> Part of Feature Framework refactoring (Phase 1+2)
> See also: [Feature Cache Design](feature_cache_design.md), [Feature Development](feature_development.md), [Agent Checklist](agent_feature_development_checklist.md)

## 1. Purpose

The Feature Registry is the single source of truth for all features in the Qsys framework. It provides:

- **Permanent feature identity** — each feature has a stable `feature_id`
- **Metadata** — kind, source, dependencies, PIT rules, cache policy, status
- **Consistency enforcement** — YAML configs, builder hooks, and registry must agree
- **Self-documentation** — the registry IS the documentation

## 2. Core Data Structure

```python
@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    name: str
    group: str
    kind: Literal["raw", "derived"]
    source: str | None
    dependencies: tuple[str, ...]
    compute_fn: str | None
    dtype: str | None
    pit_type: Literal["point_in_time", "rolling_past", "cross_sectional", "industry", "static"]
    cache_scope: Literal["per_date", "per_instrument", "panel", "none"]
    status: Literal["active", "experimental", "deprecated", "broken"]
    description: str
    owner: str | None = None
```

### Field Semantics

| Field | Meaning | Example |
|-------|---------|---------|
| `feature_id` | Permanent unique identifier | `relative_strength__ret_60d` |
| `name` | DataFrame column name | `ret_60d` |
| `group` | Logical group membership | `relative_strength` |
| `kind` | Raw (from source) or derived (computed) | `derived` |
| `source` | Data source table for raw, or empty | `qlib_bar` |
| `dependencies` | Raw fields or derived features this depends on | `("close",)` |
| `compute_fn` | Python function path | `groups.relative_strength.build_relative_strength_features` |
| `pit_type` | PIT classification (see §4) | `rolling_past` |
| `cache_scope` | Cache granularity (see Cache Design) | `panel` |
| `status` | Active / experimental / deprecated / broken | `active` |

## 3. Registry Storage

### Python Module: `qsys/feature/registry_v2.py`

Contains:
- `FeatureSpec` frozen dataclass
- `FEATURE_REGISTRY: dict[str, FeatureSpec]` — all registered features keyed by `feature_id`
- `FEATURE_NAME_INDEX: dict[str, str]` — name → feature_id mapping
- `get_feature(feature_id_or_name)` — lookup by either
- `get_active_features()` — filter by status="active"
- `resolve_dependency_chain(feature_ids)` — expand transitive dependencies
- `get_group_features(group_name)` — all features in a group
- `validate_registry()` — self-consistency checks

### Backward Compatibility

- `FEATURE_GROUPS` in `registry.py` remains untouched.
- `FeatureSpec` is additive — no existing code reads it.
- `registry_v2.get_feature()` delegates to `FeatureSpec` but code that uses `feature_name` as column name continues unchanged.
- YAML configs remain the same format; only validation is added.

## 4. PIT Classification

| pit_type | Rules | Examples |
|----------|-------|----------|
| `point_in_time` | Direct from data source, already PIT-safe via ann_date forward-fill | `roe`, `pe`, `margin_balance` |
| `rolling_past` | Uses only past window, per-instrument | `ret_60d`, `volume_ratio_20d` |
| `cross_sectional` | Rank/percentile/zscore per trade_date | `rps_60d`, `pe_rank_252d` |
| `industry` | Two-stage: groupby([trade_date,industry]).agg then groupby(industry).rolling | `industry_ret_20d`, `industry_breadth_60d` |
| `static` | Does not change with time | `industry_code` |

## 5. Consistency Rules

### Hard Rules (enforced by validate_registry())

1. **feature_id uniqueness**: No two FeatureSpecs share a feature_id.
2. **name uniqueness**: No two FeatureSpecs share a name, unless one is deprecated.
3. **dependency existence**: All dependencies reference valid feature_ids or known raw fields.
4. **broken exclusion**: Features with status="broken" cannot appear in active feature lists.
5. **experimental exclusion from production**: Status="experimental" features must not default to production configs.

### YAML Consistency

1. Every feature name in a YAML config must have a corresponding FeatureSpec.
2. Every feature_id with status="active" must be referenced by at least one YAML or group.
3. feature flags must produce exactly the features declared in registry for that group.

## 6. Migration from Legacy Registry

```python
# Old: FEATURE_GROUPS dict in registry.py
# New: FeatureSpec instances in registry_v2.py

# Step 1 (Phase 1): Create registry_v2.py alongside registry.py
# Step 2 (Phase 2): Add consistency tests that compare both
# Step 3 (Phase 4): Build new builder that reads from registry_v2
```

**Both registries coexist** until Phase 4. No existing code is broken.

## 7. Feature Naming Convention

- **feature_id**: `{group}__{feature_name}` — group prefix for uniqueness, double underscore separator
- **name**: Short, descriptive, snake_case. Matches existing column names.
- Dots for namespacing: unused currently, reserved for future use.

## 8. Dependency Graph

```
raw features (qlib_bar, fina_indicator, qlib_margin, shareholder)
  └── derived features (group-level compute functions)
        ├── basic rolling (ret_60d, volume_ratio_20d)
        ├── cross-sectional (rps_60d, pe_rank_252d)
        ├── composite scores (margin_crowding_score)
        └── cross-group interactions (v3b_interaction)
```

The resolver (`resolver.py` Phase 4) will:
1. Read target feature list from YAML
2. Expand transitive dependencies
3. Topologically sort by dependency
4. Compute raw → derived in order
5. Cache at configurable granularity
