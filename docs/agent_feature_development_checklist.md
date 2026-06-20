# Agent Feature Development Checklist

> Use this checklist when adding or modifying features in Qsys.
> Each item is a guardrail against historical bugs.
>
> Derived from: [Feature Development](feature_development.md)
> See also: [Registry Design](feature_registry_design.md), [Cache Design](feature_cache_design.md)

## Pre-Flight

- [ ] I have read ALL four design docs (registry, cache, development, this checklist)
- [ ] I have read the existing feature group code for the pattern
- [ ] I have checked the feature inventory (`artifacts/feature_registry_audit/feature_inventory.csv`) for existing features

## Registry

- [ ] NEW: Feature registered as `FeatureSpec` in `registry_v2.FEATURE_REGISTRY`
- [ ] NEW: `feature_id` is unique (check against existing registry)
- [ ] NEW: `name` is the DataFrame column name (matches downstream)
- [ ] ALL: `kind` correctly set to "raw" or "derived"
- [ ] ALL: `dependencies` lists every raw field or derived feature used
- [ ] DERIVED: `compute_fn` path is accurate and importable
- [ ] ALL: `pit_type` is correct (point_in_time / rolling_past / cross_sectional / industry)
- [ ] ALL: `cache_scope` correctly reflects computation cost
- [ ] ALL: `status` set correctly (experimental if unvalidated, broken if known wrong)
- [ ] NEW: Feature added to `FEATURE_GROUPS` in `registry.py` (legacy compat)
- [ ] NEW: Builder hook added to `builder.py` under correct feature flag
- [ ] NEW: Feature flag added to `config.py` (default False for new flags)

## Industry Aggregation

- [ ] ✋ STAGE 1: `ind_panel = out.groupby(["trade_date", "industry"]).agg(...)` — cross-sectional per date×industry
- [ ] ✋ STAGE 2: `ind_panel.groupby("industry")["col"].transform(lambda s: s.rolling(N).mean())` — temporal per industry
- [ ] NEVER: `out.groupby("industry")["col"].transform("mean")` — crosses dates
- [ ] NEVER: `out.groupby("industry")["col"].transform(lambda s: s.rolling(N).mean())` — crosses dates
- [ ] TEST: `test_industry_aggregation_contract.py` added/modified

## Cross-Sectional (Ranks, Percentiles, Z-scores)

- [ ] CORRECT: `out.groupby("trade_date")["col"].transform(...)` — per-date groupby
- [ ] NEVER: `out["col"].rank()` — no groupby, crosses dates
- [ ] DANGER: Cross-sectional z-score on future-dependent distribution — check inference mode

## Rolling / Temporal

- [ ] CORRECT: `out.groupby("ts_code")["col"].transform(lambda s: s.rolling(N).mean())` — per-instrument
- [ ] NEVER: `out["col"].rolling(N).mean()` — crosses stocks
- [ ] min_periods set appropriately (min_periods=max(2, N//4) recommended)
- [ ] shift/pct_change always inside `groupby("ts_code")`

## Financial Statement Features

- [ ] CORRECT: `merge_asof(direction="backward")` using `ann_date` column
- [ ] WRONG: Using `end_date` as visible date (future information leak)
- [ ] CORRECT: Annual report yoy computed on report-level data with `pct_change(4)`
- [ ] WRONG: `pct_change(4)` on daily PIT-expanded rows (4 days ≠ 4 quarters)
- [ ] CORRECT: Capex named correctly, not proxied by free_cashflow
- [ ] SEMANTIC: Feature name matches economic meaning, not what data is available

## YAML Config

- [ ] NEW/CHANGED: Feature list YAML updated (or existing one extended)
- [ ] MATCH: All YAML features have corresponding registry entries
- [ ] ACTIVE: No `status=broken` features in YAML
- [ ] PRODUCTION: No `status=experimental` features in production YAML (unless explicit)

## Cache

- [ ] EXPENSIVE: Derived features with rolling windows ≥ 60d marked as cacheable
- [ ] COMPOSITE: Cross-group interaction features marked as cacheable
- [ ] CACHE KEY: Cache considers compute_fn hash + dependencies hash
- [ ] PHYSICS: Verifying cache doesn't stale-hide bugs from incomplete invalidation

## Builder Isolation

- [ ] ONLY: My feature flag triggers only my feature group's compute function
- [ ] NOT: My feature flag does not trigger other groups (no cross-coupling)
- [ ] DUPLICATE: No duplicate calls to the same build function
- [ ] ORDER: Builder order respects dependencies (raw → derived → composite)

## Testing

- [ ] UNIT: `tests/features/test_{group_name}.py` added with:
  - [ ] Correct feature count
  - [ ] Deterministic synthetic data
  - [ ] Expected value assertions (not just shape checks)
  - [ ] NaN behavior verification
- [ ] REGISTRY: `test_feature_registry_consistency.py` updated
- [ ] ISOLATION: `test_feature_builder_isolation.py` updated
- [ ] INDUSTRY: `test_industry_aggregation_contract.py` updated (if industry feature)
- [ ] SEMANTICS: `test_financial_statement_feature_semantics.py` updated (if fin statement feature)
- [ ] CACHE: `test_feature_cache.py` updated (if cacheable feature)

## Documentation

- [ ] `docs/feature_development.md` — PIT rules updated if new pattern
- [ ] `docs/agent_feature_development_checklist.md` — checklist item updated if new guard rail
- [ ] `research_notes/feature_framework_inventory_and_refactor_plan.md` — inventory updated
- [ ] PR body includes: feature_id, source, dependencies, cache policy, PIT rule, test coverage

## Release / PR

- [ ] Run: `python -m pytest tests/features/ -v`
- [ ] Run: `python -m py_compile qsys/feature/registry_v2.py`
- [ ] Check: No `import` of non-existent modules
- [ ] Check: No duplication with existing features
- [ ] Check: Legacy YAML configs unchanged (backward compat guaranteed)
- [ ] Check: CLAUDE.md documents synced
