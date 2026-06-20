# Feature Framework Inventory & Refactor Plan

> Date: 2026-06-20
> Author: Agent (refactor phase 1+2)
> Status: Draft — output of `generate_feature_inventory.py` + manual audit

## 1. Current State Summary

The feature framework lives in `qsys/feature/` with the following modules:

| Module | Purpose | Lines | Status |
|--------|---------|-------|--------|
| `registry.py` | `FEATURE_GROUPS` dict, `FeatureListRegistry` YAML loader | ~320 | Active |
| `builder.py` | `build_phase1_features()` — flag-based builder | ~110 | Active, legacy |
| `resolver.py` | `resolve_feature_list()`, `build_feature_manifest()` | ~560 | Active, heavy |
| `cache.py` | Per-query parquet cache (data/canonical/features/) | ~68 | Active, minimal |
| `config.py` | `RESEARCH_FEATURE_FLAGS` | 17 | Active |
| `groups/` (dir) | 11 feature group implementations | ~900 | Active |
| `library.py` | `FeatureLibrary` static config methods + `FeatureResearch` | ~465 | Active, hybrid |
| `transforms.py` | Winsorize, z-score, rank helpers | ~46 | Active |

### Feature Groups (11 in FEATURE_GROUPS)

| Group | Features | Flag | Status |
|-------|----------|------|--------|
| microstructure | 7 | enable_microstructure_features | active |
| liquidity | 7 | enable_liquidity_features | active |
| tradability | 7 | enable_tradability_features | active |
| relative_strength | 54 | enable_relative_strength_features | active |
| regime | 8 | enable_regime_features | active |
| industry_context | 7 | enable_industry_context_features | active |
| fundamental_context | 55 | enable_fundamental_context_features | active |
| v3a_margin | 9 | enable_v3a_margin_features | active |
| v3a_shareholder | 10 | enable_v3a_shareholder_features | active |
| v3b_price_volume | 14 | enable_v3b_price_volume_features | active |
| v3b_interaction | 5 | enable_v3b_interaction_features | active |
| industry_momentum | 11 | enable_industry_momentum_features | experimental |

**Total registered features:** 169 (excluding dupes from registry restructure)

### YAML Feature Lists (14 files)

| YAML ID | Feature Count | Notes |
|---------|--------------|-------|
| alpha_v1_clean_132 | ~132 | Alpha158 expressions + raw |
| momentum_price_volume_v1 | 6 | Qlib expressions |
| value_growth_multibagger_v1_features | 26 | v1 growth + valuation + market_confirm |
| value_growth_multibagger_v2_features | ~64 | v1 + continuation + volume + acceleration + path_classifier |
| value_growth_multibagger_v3a_features | 83 | v2 + margin + shareholder |
| value_growth_v2_margin_features | 73 | v2 + margin |
| value_growth_v2_shareholder_features | 74 | v2 + shareholder |
| value_growth_60d_full_price_volume_features | 81 | All PV groups |
| value_growth_60d_structured_price_volume_features | 35 | Curated 60d timing |
| value_growth_60d_v3a_full_plus_structured_pv_features | 98 | v3a + structured PV |
| value_growth_existing_price_volume | 23 | Existing v2 PV |
| value_growth_v3a_plus_industry_momentum_features | 95 | v3a + industry momentum |
| value_growth_v3b_pv_features | 97 | v3a + v3b PV |
| value_growth_v3b_pv_interact_features | 102 | v3a + v3b PV + interactions |

### Raw Feature Sources

| Source | Fields |
|--------|--------|
| qlib_bar | $open, $high, $low, $close, $vwap, $volume, $amount, $factor, $total_mv, $circ_mv |
| fina_indicator | $roe, $grossprofit_margin, $debt_to_assets, $op_cashflow, $pe, $pb, $ps, $net_income, $revenue, $total_assets, $equity |
| qlib_margin | $margin_balance, $margin_buy_amount, $margin_repay_amount, $margin_total_balance |
| shareholder_parquet | holder_num, top10_holder_ratio (via merge_asof ann_date) |

## 2. Issues Found

### 2.1 Registry vs YAML Mismatch

- 103 "yaml_only" features in inventory — these are raw qlib expressions (`$close`, `($high-$low)/$open`, etc.) not registered in `FEATURE_GROUPS`.
- Some YAML files reference features not in registry (case sensitivity, qlib expressions vs named features).
- Feature names appear in multiple groups (e.g., `ret_20d` in both `relative_strength` and `regime` indirectly).

### 2.2 Feature Flag vs YAML Decoupling

- `configs/research/60d/abl_60d_v3a_plus_industry_momentum_delayed60.yaml` uses `feature_list_id: value_growth_multibagger_v3a_features` but opens industry momentum via extra feature flags. The YAML metadata doesn't match true feature set.
- Builder hook sequence is opaque: you must read the full builder.py to know which flags produce which features.

### 2.3 Historical Implementation Bugs (Prevented by This Refactor)

See detailed analysis in `v3a_case_mining_error_analysis.md`. Key classes:

1. **Industry aggregation cross-date contamination** — industry momentum originally grouped by `industry` alone, mixing dates.
2. **Cross-stock rolling contamination** — rolling/pct_change without `groupby("ts_code")`.
3. **Series-as-column in groupby** — `out.get()` returning Series used as column name in groupby.
4. **free_cashflow as capex proxy** — economic meaning mismatch, feature removed.
5. **Quarterly yoy at daily frequency** — `pct_change(4)` on daily PIT-expanded data, not report-level.
6. **Duplicate builder calls** — `build_v3a_v3b_interaction_features` called multiple times.
7. **Label maturity confusion** — calendar vs trading days for fwd_ret labels.

### 2.4 Cache Limitations

- Current `cache.py` is per-query (universe, fields, start, end) — no per-feature cache.
- Cache key does not include feature implementation hash.
- No cache for per-feature expensive computations.

### 2.5 Missing Tests

- No registry consistency tests
- No builder isolation tests
- No industry aggregation contract tests
- No financial statement semantic tests
- No cache correctness tests

## 3. Refactor Plan

### Phase 1 (This PR): Inventory + Registry Spec + Design Docs

- [x] Feature inventory CSV (`artifacts/feature_registry_audit/feature_inventory.csv`)
- [x] FeatureSpec dataclass + registry_v2.py with all active features
- [x] Feature registry design doc
- [x] Feature cache design doc
- [x] Feature development contract doc
- [x] Agent feature development checklist

### Phase 2 (This PR): Consistency Tests

- [x] Registry consistency tests (test_feature_registry_consistency.py)
- [x] Builder isolation tests (test_feature_builder_isolation.py)
- [x] Industry aggregation contract tests (test_industry_aggregation_contract.py)
- [x] Financial statement semantic tests (test_financial_statement_feature_semantics.py)

### Phase 3 (Next PR): Cache Implementation

- Per-feature parquet cache (data/feature_cache/features/{feature_id}/)
- Feature matrix cache (data/feature_cache/matrices/)
- Cache key with compute_fn hash
- Cache test file

### Phase 4 (Next PR): Builder Refactor

- `build_features_from_feature_list()` as primary entry point
- YAML → Resolver → Registry Spec → Compute → Cache
- Legacy `build_phase1_features()` marked deprecated

## 4. FeatureSpec Design (registry_v2.py)

```python
@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str        # permanent, unique
    name: str              # actual DF column name (kept unchanged)
    group: str             # logical group
    kind: Literal["raw", "derived"]
    source: str | None     # data source table
    dependencies: tuple[str, ...]  # other feature_ids or raw field deps
    compute_fn: str | None # function path
    pit_type: Literal["point_in_time", "rolling_past", "cross_sectional", "industry", "static"]
    cache_scope: Literal["per_date", "per_instrument", "panel", "none"]
    status: Literal["active", "experimental", "deprecated", "broken"]
    description: str
    dtype: str | None = None
    owner: str | None = None
```

### Key Design Decisions

1. **feature_id is permanent** — once assigned, never changes or reuses.
2. **name is the DataFrame column name** — changes to feature names require migration.
3. **dependencies use feature_id** — enabling dependency graph traversal.
4. **status=broken blocks production** — active feature lists must exclude broken.
5. **YAML behavior is unchanged** — backward compatibility guaranteed.

## 5. Migration Commitment

 | What | Changes | YAML Impact |
|------|---------|-------------|
| Phase 1+2 | New registry_v2.py, new tests | None |
| Phase 3 | New cache.py | None |
| Phase 4 | New builder entry point | None (new function, old kept) |

**No YAML file will be modified** by this PR. All downstream experiments remain reproducible.

## 6. References

- `artifacts/feature_registry_audit/feature_inventory.csv` — machine-readable inventory
- `artifacts/feature_registry_audit/inventory_summary.json` — aggregate counts
- `docs/feature_registry_design.md` — FeatureSpec design
- `docs/feature_cache_design.md` — cache architecture
- `docs/feature_development.md` — development contract
- `docs/agent_feature_development_checklist.md` — agent checklist
- `docs/ARCHITECTURE.md` — system architecture (features in Data layer)
- `qsys/feature/registry_v2.py` — FeatureSpec + FEATURE_REGISTRY
