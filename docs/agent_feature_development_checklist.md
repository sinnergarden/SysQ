# Feature Development Checklist (for Agents)

## Before Writing Code

- [ ] Add `FeatureSpec` to `qsys/feature/registry_v2.py`
  - [ ] `feature_id` — permanent, stable
  - [ ] `name` — matches existing YAML output column name
  - [ ] `kind` — `"raw"` or `"derived"`
  - [ ] `dependencies` — list all direct inputs (raw → derived chain)
  - [ ] `pit_type` — one of: `rolling_past`, `cross_sectional`, `point_in_time`, `static`
  - [ ] `cache_scope` — `panel`, `per_date`, `per_instrument`, or `none`
  - [ ] `status` — `active`, `experimental`, `deprecated`, or `broken`
  - [ ] `description` — one-line summary of economic meaning

- [ ] Confirm the `name` does NOT already exist in `FEATURE_GROUPS` or across YAML configs
- [ ] Confirm no feature name duplicates across groups (if so, use alias or rename)

## During Implementation

### Aggregation Rules

- [ ] If **cross-sectional**: `groupby("trade_date")` — always
- [ ] If **temporal rolling**: `groupby("ts_code").rolling(window)` — never raw `.rolling()`
- [ ] If **industry aggregation**: two-step process
  - Step 1: `groupby(["trade_date", "industry"])` collapse to daily industry panel
  - Step 2: `groupby("industry").rolling(window)` on the panel
- [ ] If **financial statement**: use `ann_date` as visibility, never `end_date`
- [ ] If **quarter-over-quarter**: compute on report-level panel with `.pct_change(4)`, NOT on daily PIT-expanded data
- [ ] If using a **proxy** for a real economic concept (e.g. free_cashflow as capex): name must include `"_proxy"`

### Numeric Hygiene

- [ ] No `inf` values — use `_clip_inf()` helper
- [ ] No division by zero — use `_safe_div()` helper (divides by den.replace(0, NaN))
- [ ] No cross-stock contamination — always `groupby("ts_code")` before `.rolling()`
- [ ] No intermediate columns leaked — clean up `_`-prefixed columns at function end

## Before PR

### YAML & Registry

- [ ] Add `feature_groups` reference in appropriate `configs/features/<name>.yaml`
- [ ] Register feature name in `qsys/feature/registry.py` `FEATURE_GROUPS[group]["features"]`
- [ ] Check `enabled_by` flag exists in `qsys/feature/config.py` `RESEARCH_FEATURE_FLAGS`
- [ ] Add flag dispatch in `qsys/feature/builder.py`
- [ ] Add auto-detection in `QlibAdapter._semantic_feature_flags()` (in `qsys/data/adapter.py`)

### Testing

- [ ] Run `python -m unittest tests/features/test_feature_registry_consistency.py`
- [ ] Run `python -m unittest tests/features/test_feature_builder_isolation.py`
- [ ] Run `python -m unittest tests/features/test_industry_aggregation_contract.py`
- [ ] Run `python -m unittest tests/features/test_financial_statement_feature_semantics.py`
- [ ] Run `python -m unittest discover tests/features/`
- [ ] If new `feature_groups` entry: verify YAML ↔ registry ↔ builder alignment

### PR Body Must Include

```markdown
## Feature Summary
- **feature_id**: `my_new_feature`
- **source**: derived from `close` + `volume`
- **dependencies**: `close`, `ret_60d`
- **cache policy**: panel (rolling_past, cacheable)
- **PIT rule**: rolling_past (time-series rolling only)
## Verification
- [ ] Registry consistency tests pass
- [ ] Builder isolation tests pass
- [ ] Industry aggregation contract tests pass
```

## Prohibitions

1. **Broken features cannot enter active feature list.** If status=broken, remove from YAML/groups.
2. **Feature flags cannot silently enable extra features** beyond what YAML/registry declares.
3. **No direct import from `archive/`.** Archive is read-only historical reference.
4. **No "latest model directory" strategy.** Daily ops consumes explicit manifest, not latest-dated model.
