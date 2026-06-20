# Feature Development Checklist (for Agents)

## Before Writing Code

### FeatureSpec / TransformSpec（框架修改专用）
- [ ] Add `FeatureSpec` to `populate_feature_specs.py`（不是直接写 registry_v2.py）
  - [ ] `feature_id` — permanent, stable
  - [ ] `name` — matches existing YAML output column name
  - [ ] `kind` — `"raw"` or `"derived"`
  - [ ] `dependencies` — list all direct inputs
  - [ ] `pit_type` — one of the four
  - [ ] `cache_scope` — `"none"` or `"panel"`
  - [ ] `status` — `"active"`, `"experimental"`, `"deprecated"`, or `"broken"`
  - [ ] `description` — one-line summary

### Registry (backward-compat path)
- [ ] Add to `FEATURE_GROUPS[group]["features"]` in `registry.py`
- [ ] Check `enabled_by` flag exists in `config.py`
- [ ] Add flag dispatch in `builder.py`
- [ ] Add auto-detection in `QlibAdapter._semantic_feature_flags()` if needed

## During Implementation

### Aggregation Rules
- [ ] Cross-sectional: `groupby("trade_date")`
- [ ] Temporal rolling: `groupby("ts_code").rolling(window)`
- [ ] Industry: collapse → `groupby(["trade_date","industry"])` → THEN `groupby("industry").rolling`
- [ ] Financial: use `ann_date`, not `end_date`
- [ ] QoQ: on report-level panel `.pct_change(4)`, not daily-expanded
- [ ] Proxy: name includes `"_proxy"`

### Numeric Hygiene
- [ ] No inf — use `_clip_inf()`
- [ ] No div-by-zero — use `_safe_div()`
- [ ] No cross-stock contamination — always `groupby("ts_code")` before rolling
- [ ] No intermediate columns leaked — clean up `_`-prefixed columns

## Before PR

### FeatureSet YAML Registration

- [ ] New feature added to a FeatureSet YAML (`configs/features/<name>.yaml`)
  - Legacy mode: add to `features` list
  - Additive mode: use `extends` + `add_features` (only if base set exists)
- [ ] Run resolver CLI to verify:
  ```bash
  python scripts/dev/resolve_feature_set.py \
      --feature-set configs/features/<name>.yaml
  ```
- [ ] Confirm no "missing", no "broken", no "unresolved transforms" you didn't expect
- [ ] Manifest written to `artifacts/feature_manifests/`

### Testing
- [ ] `python -m unittest tests/features/test_feature_registry_consistency.py`
- [ ] `python -m unittest tests/features/test_feature_builder_isolation.py`
- [ ] `python -m unittest tests/features/test_industry_aggregation_contract.py`
- [ ] `python -m unittest tests/features/test_financial_statement_feature_semantics.py`
- [ ] `python -m unittest tests/features/test_feature_set_resolver.py`
- [ ] `python -m unittest discover tests/features/`

### PR Body Must Include
```
## Feature Summary
- feature_id: `my_new_feature`
- source: derived from `close` + `volume`
- dependencies: `close`, `ret_60d`
- PIT rule: rolling_past
```

## Prohibitions
1. status=broken features must NOT enter any active feature list.
2. Feature flags cannot silently enable extra features beyond YAML/registry.
3. No import from `archive/`.
4. No `exclude_features` / `exclude_groups` in YAML — only additive.
