# Script Inventory & Consolidation Plan

> Last updated: 2026-03-22
> Status: P1.1 in progress

## 1. Overview

Total scripts in `scripts/`: **16**

Classification:
- **Keep**: 9 (active production/research entrypoints)
- **Merge**: 2 (functionality covered by others)
- **Deprecate**: 2 (already redirected with warnings)
- **Review**: 3 (utility/debug, lower priority)

---

## 2. Inventory Table

| Script | Type | Status | Reason |
|--------|------|--------|--------|
| `run_daily_trading.py` | ops | **KEEP** | Primary daily ops entrypoint (盘前) |
| `run_post_close.py` | ops | **KEEP** | Primary daily ops entrypoint (盘后) |
| `run_train.py` | training | **KEEP** | Primary training entrypoint with click CLI |
| `run_backtest.py` | research | **KEEP** | Primary backtest entrypoint |
| `run_strict_eval.py` | research | **KEEP** | Primary strict evaluation entrypoint |
| `run_update.py` | data | **KEEP** | Data update entrypoint (universe/date based) |
| `update_data_all.py` | data | **KEEP** | Full data status check & alignment |
| `create_instrument_csi300.py` | data | **KEEP** | Universe initialization |
| `run_compare.py` | research | **KEEP** | Model comparison utility |
| `run_plan.py` | ops | **DEPRECATE** | Legacy, prints warning, redirects to run_daily_trading.py |
| `run_reconcile.py` | ops | **DEPRECATE** | Legacy, prints warning, redirects to run_daily_trading.py |
| `debug_data_quality.py` | debug | **REVIEW** | Debug utility, lower priority |
| `debug_model_performance.py` | debug | **REVIEW** | Debug/analysis utility |
| `check_amount.py` | utility | **MERGE** | Thin wrapper, likely covered by other tools |
| `rebuild_qlib_bin.py` | utility | **MERGE** | Thin wrapper around dump_bin.py |
| `dump_bin.py` | lib | **KEEP** | Core qlib data handling (imported as lib) |
| `run_train.py` | | | (already listed above) |

---

## 3. Redundant / Overlapping Scripts

### 3.1 Deprecated but still present

| Script | Replacement | Action |
|--------|-------------|--------|
| `run_plan.py` | `run_daily_trading.py` | Already prints warning. Can remove after confirmation. |
| `run_reconcile.py` | `run_daily_trading.py` or `run_post_close.py` | Already prints warning. Can remove after confirmation. |

**Risk**: Low. Both print deprecation warnings at runtime. Can be removed in next cleanup cycle.

### 3.2 Potential merge candidates

| Script | Concern | Recommendation |
|--------|---------|----------------|
| `check_amount.py` | Very thin wrapper around qlib init | Merge into `debug_data_quality.py` or remove |
| `rebuild_qlib_bin.py` | Thin wrapper calling `dump_bin` main | Merge into `dump_bin.py` as CLI option |

### 3.3 Active vs Debug

- Active production scripts (9): clear CLI interfaces, tracked in RUNBOOK
- Debug/analysis scripts (3): lower priority, no CLI standardization

---

## 4. Implemented First Cleanup

### 4.1 Already done (no code change needed)

Both `run_plan.py` and `run_reconcile.py` already:
- Print `log.warning("Legacy entrypoint detected...")` 
- Reference the recommended alternative

This satisfies the "mark deprecated" requirement.

### 4.2 Implemented minimal cleanup

1. **Deprecated aliases are explicitly marked**
   - `run_plan.py`
   - `run_reconcile.py`

2. **Primary scripts now carry top-of-file usage notes**
   - purpose
   - typical command
   - important arguments

3. **Optionally next**: Remove `check_amount.py` or merge into `debug_data_quality.py`

---

## 5. Next Cleanup Steps (Require User Approval)

1. **High priority**:
   - [ ] Confirm `run_plan.py` and `run_reconcile.py` deprecation is acceptable, then delete them
   - [ ] Merge `check_amount.py` into `debug_data_quality.py` or remove

2. **Medium priority**:
   - [ ] Standardize debug scripts CLI or move to `tools/` subdir
   - [ ] Consider merging `rebuild_qlib_bin.py` into `dump_bin.py`

3. **Low priority**:
   - [x] Add consistent top-level usage docstrings to primary keep scripts
   - [ ] Add shebang line to all entrypoints

---

## 6. Classification Criteria Used

Based on RUNBOOK.md and ROADMAP.md:

1. **Has CLI (click/argparse)** → likely active
2. **Referenced in RUNBOOK** → production critical
3. **Prints "Legacy" warning** → already deprecated
4. **Imported as module, no CLI** → library (keep)
5. **No clear purpose or redundant** → merge/deprecate

---

## 7. Notes

- Main entrypoints are now `run_daily_trading.py` and `run_post_close.py` for daily ops
- Training/research flows are covered by `run_train.py`, `run_backtest.py`, `run_strict_eval.py`
- Data flows: `run_update.py`, `update_data_all.py`, `create_instrument_csi300.py`
- 16 scripts total → target ~12 after cleanup