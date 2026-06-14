# Code Review Summary — Value Growth Phase 1

> Research-note PR review. Not a merge gate. Not a production trading approval.

## Reviewed Files

| File | Focus |
|------|-------|
| `qsys/label/compute.py` | Label computation, adjusted close, forward return |
| `scripts/compute_labels.py` | Label CLI, manifest, coverage |
| `qsys/data/collector.py:432-509` | `_merge_financials` PIT logic |
| `qsys/data/adapter.py:295-368` | Semantic support fields, rename_map, `$industry` |
| `qsys/analysis/research_diagnostics.py` | Diagnostics engine |
| `qsys/backtest/strategy_runner.py` | `run_from_signal_cache` API |
| `qsys/backtest/engine.py` | `build_trading_day_windows`, rebalance logic |
| `qsys/research/rolling_window.py` | `build_rolling_windows` |
| `scripts/run_research.py` | Research entrypoint |
| `configs/research/value_growth_*.yaml` | Research configs |
| `configs/features/value_growth_multibagger_v1_features.yaml` | Feature list |

## High-Risk Findings

### H1. Label entry = T, not T+1 — PIT asymmetry

**File:** `qsys/label/compute.py:72`

```python
shifted = frame.groupby("instrument")["_adj_price"].transform(lambda s: s.shift(-horizon))
```

The label uses T-day adjusted close as entry price. Signal generation uses the same T-day feature snapshot. This is internally consistent (both are T), but **backtest entry assumes T+1** (see scratch script). The gap between research eval (T entry) and backtest execution (T+1) is not handled by the framework — it's handled ad-hoc in the scratch backtest script.

**Assessment:** Not a BLOCKER for the research-note PR. The backtest scratch script correctly implements T+1. The gap is well-documented in the research notes. **Recommendation:** The framework should support an `entry_lag` parameter (0=current-day, 1=next-day) in label computation and signal evaluation.

### H2. Static CSI800 universe — no historical membership tracking

**File:** `qsys/data/adapter.py` `_prepare_csvs` → qlib dump pipeline

The CSI800 instrument file writes current constituents with end_date forced to the latest calendar date. There is no historical membership reconstruction — a stock that entered CSI800 in 2023 is included for 2010. This affects all research/backtest results.

**Assessment:** Well-documented in all audit notes. **Not fixable without external data (historical CSI800 index constituent lists).** Marked as the top caveat.

### H3. No feature snapshot at inference time

**File:** `qsys/research/generators/lightgbm_single_label.py` (reviewed during Phase 1)

The `generate()` method produces score + signal metadata, but does NOT save the feature matrix values used for that inference. This means:

- Candidate explainability requires re-querying qlib (which may not match inference-time state)
- Post-hoc feature attribution (SHAP) is possible but requires re-running full inference
- Feature values for the "latest" inference are not available if qlib data has been updated

**Assessment:** Not a research-phase blocker. Long-term, the framework should optionally persist `feature_values` alongside `score` in the SignalStore.

## Medium-Risk Findings

### M1. _merge_financials drops ann_date after merge

**File:** `qsys/data/collector.py:490`

```python
merged = merged.drop(columns=["trade_date_dt", "ann_date_dt", "ann_date"], errors="ignore")
```

After the PIT merge, `ann_date` is dropped. This means downstream code (adapter, feature builder) cannot verify PIT recency. For diagnostics or audit, knowing "this roe value was announced on date X" is useful.

**Assessment:** Not a bug — the current pipeline is correct. But dropping ann_date reduces auditability. **Suggestion:** Keep `ann_date` in the canonical feather if space permits.

### M2. compute_labels.py uses `get_trading_calendar` for coverage, not actual row count

**File:** `scripts/compute_labels.py:51`

```python
expected_rows = effective_dates * n_insts if n_dates and n_insts else 0
```

The coverage formula uses `(n_dates - horizon) * n_instruments` as expected rows, but actual rows may differ due to:
- Stocks with missing prices in parts of the date range
- Backward fill gaps in qlib data
- Universe changes across time

The manifest coverage ratio may be misleading during periods of incomplete data.

**Assessment:** Low severity. The formula is a rough approximation. Actual coverage (NaN check on merged data) is more reliable and was used in the audit.

### M3. Industry cap doesn't bind in simple backtest

The scratch backtest showed <1% difference between capped and uncapped. Investigation revealed:
- Industry classification has high granularity (~110 industries, many with 1-5 stocks each)
- At 25% cap on top50: only industries with >12.5 stocks would bind, which rarely happens
- With coarser classification (SW L1 ~30 industries), the cap would bind more

**Assessment:** Not a bug, but the 25% industry cap parameter is ineffective given current industry granularity. A 15% or 10% cap would bind more. Documented in backtest note.

## Low-Risk Findings

### L1. Rolling window uses calendar-day estimation for back-date

**File:** `qsys/research/rolling_window.py:22-24`

```python
dt = dt - timedelta(days=int(n_days * 1.4) + buffer + 5)
```

The `_calendar_backdate` function approximates trading days by multiplying by 1.4. This works but may slightly over- or under-shoot when the date range includes holidays.

**Assessment:** Since the function is only used to compute a conservative `_extended_start` for the qlib features call, over-shooting is harmless (extra dates are just not queried). No issue.

### L2. Backtest `rebalance_freq` only supports daily/weekly

**File:** `qsys/backtest/strategy_runner.py` API

The `run_from_signal_cache` method supports `daily` or `weekly` rebalance. For the 20d rebalance used in Phase 1 validation, a scratch script was required. This is documented as a framework gap.

## No-Issue Confirmations

| Check | Status |
|-------|--------|
| PIT: _merge_financials uses ann_date, direction=backward | ✅ PASS |
| PIT: Rows without ann_date are dropped | ✅ PASS |
| PIT: No end_date/report_period fallback | ✅ PASS |
| PIT: roe comes from fina_indicator (ann_date), not straight from daily_basic | ✅ PASS |
| Label: adjusted_close = close * factor | ✅ PASS |
| Label: shift(-horizon) is backward-only, no lookahead | ✅ PASS |
| Label: last H days without label are dropped via dropna | ✅ PASS |
| Window: rolling_window predict_start > train_end | ✅ PASS |
| Window: predict window size = step (no overlap) | ✅ PASS |
| Eval: strict 20d sampled from sorted dates | ✅ PASS |
| Eval: 180d overlap caveat documented | ✅ PASS |
| Backtest: T+1 entry correctly shifts by 1 calendar day | ✅ PASS |
| Backtest: adjusted close used for PnL | ✅ PASS |
| Backtest: 20bps cost deducted | ✅ PASS |
| Backtest: rank_weight normalized to 1.0 | ✅ PASS |
| Universe: no eval_date < list_date samples | ✅ PASS |
| Universe: listed_252d filter used in audit | ✅ PASS |

## Required Fixes Before Production

1. **Add `entry_lag` to label/signal pipeline** — Currently T entry is hardcoded. Production needs T+1.
2. **Persist feature matrix snapshot at inference time** — For explainability and bug investigation.
3. **Support 20d rebalance in BacktestRunner** — Weekly and daily are not the "every N trading days" needed for medium-frequency signals.
4. **Add historical CSI800 membership tracking** — Requires external data source (CSI800 historical constituents). Without it, backtest results cannot be certified as free of forward-looking universe bias.

## Non-blocking Improvements

1. Keep `ann_date` in canonical feather for auditability.
2. Use actual NaN-free row count for coverage in `compute_labels.py`, not formula estimate.
3. Add `rebalance_freq` option for arbitrary N-day intervals in BacktestRunner.

## Final Verdict

**No blocker found for research-note PR.** All identified issues are either:
- Well-documented caveats (static universe, T+1 gap, no feature snapshot)
- Low-severity framework improvements (20d rebalance, coverage formula)

Production trading still requires:
1. Realistic execution backtest (limit orders, slippage, partial fills)
2. Historical universe reconstruction
3. T+1 entry gap verified with live signal
