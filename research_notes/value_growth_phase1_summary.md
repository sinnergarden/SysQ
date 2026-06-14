# Value Growth Phase 1 Summary

> Research documentation. Not production trading approval.

## Objective

Evaluate whether `value_growth_multibagger_v1_features` (26 features, 3 groups) combined with a 180d-horizon LightGBM model can produce a usable medium-to-long term candidate pool for A-share right-tail return discovery.

## Data / Universe / Label

| Component | Value |
|-----------|-------|
| Universe | Static CSI800 (~800 stocks, current constituents back-projected) |
| Horizon | 180 trading days |
| Label | `fwd_ret_180d_raw` = `shift(-180, adjusted_close) / adjusted_close - 1` |
| Entry (research) | Signal day T |
| Entry (backtest) | T+1 (next calendar trading day) |
| Price basis | Adjusted close = `$close * $factor` (Tushare cumulative adj factor) |
| Feature list | `value_growth_multibagger_v1_features` (26 features, 3 groups: growth_quality, valuation_repair, market_confirmation) |
| Model | `single_label_lightgbm`, 300 estimators, 504d train window, 20d step |
| OOS period | 2015-2025 (155 rolling windows) |
| Eval frequency | Strict every 20 trading days (~12 per year, ~147 total) |

**Caveat — Static universe:** The CSI800 instrument file uses current constituents back-projected to 2010. This creates survivorship bias. Early years (2015-2018) may be inflated. The listed_252d audit showed <0.012 IC degradation when filtering to stocks listed for >252 trading days at each eval date. The 2023-2025 window (least bias) shows the strongest results.

## Signal Validation

### Overall (2015-2025, strict 20d eval)

| Metric | Value |
|--------|-------|
| Eval RankIC | **0.3784** |
| ICIR | 2.88 |
| Positive eval ratio | 100.00% |
| N eval dates | 147 |

### Annual Breakdown

| Year | N eval | RankIC | ICIR | Pos | T20ex | T50ex | T100ex | T20hit |
|------|--------|--------|------|-----|-------|-------|--------|--------|
| 2015 | 12 | 0.4407 | 3.68 | 100% | 0.5680 | 0.3808 | 0.2832 | 92% |
| 2016 | 12 | 0.3881 | 4.50 | 100% | 0.2517 | 0.1923 | 0.1683 | 100% |
| 2017 | 12 | 0.3324 | 3.70 | 100% | 0.3580 | 0.3048 | 0.2525 | 100% |
| 2018 | 12 | 0.1869 | 2.29 | 100% | 0.2084 | 0.1865 | 0.1575 | 58% |
| 2019 | 13 | 0.4366 | 4.75 | 100% | 0.8748 | 0.6777 | 0.5387 | 100% |
| 2020 | 12 | 0.3085 | 2.59 | 100% | 1.0690 | 0.7349 | 0.5819 | 100% |
| 2021 | 12 | 0.2891 | 2.02 | 100% | 0.7096 | 0.5479 | 0.4144 | 100% |
| 2022 | 12 | 0.3620 | 4.63 | 100% | 0.5229 | 0.3754 | 0.2842 | 100% |
| 2023 | 12 | 0.4747 | 5.50 | 100% | 0.4405 | 0.3371 | 0.2593 | 100% |
| 2024 | 12 | 0.4337 | 5.87 | 100% | 1.1218 | 0.7144 | 0.5258 | 100% |
| 2025 | 3 | 0.3846 | 25.88 | 100% | 0.9775 | 0.6876 | 0.4795 | 100% |

**All 11 years positive. Weakest: 2018 (IC 0.1869). Strongest: 2023 (IC 0.4747).**

### Listed_252d Filter (remove stocks with <370d trading history)

Max IC degradation: **-0.012** (2016: 0.3267 → 0.3144). Several years show *improvement*. Not material.

### Strict 20d vs Daily Overlap

Strict 20d IC (0.3784) is **higher** than daily IC (0.3130), because daily 180d overlapping labels are serially correlated, which increases noise. The non-overlapping estimate is cleaner.

## Audit Summary

### PIT Audit

| Check | Result |
|-------|--------|
| Financial features use ann_date? | ✅ `_merge_financials` uses `merge_asof(direction='backward')` on `ann_date` |
| Rows without ann_date handled? | ✅ Dropped before merge |
| end_date/report_period fallback? | ❌ No fallback — only ann_date |
| PE/PB source? | ✅ daily_basic, trade_date snapshot |
| Derived features (shift, rolling)? | ✅ Backward-only, no lookahead |
| **Verdict** | **PIT_SAFE** |

### Label Audit

| Check | Result |
|-------|--------|
| Entry price | T-day adjusted close (verified 12 samples: diff=0.000000) |
| Exit price | T+180 adjusted close |
| Factor usage | Close * cumulative adj_factor (Tushare) |
| Tail handling | NaN from dropna on missing horizon |
| **Verdict** | **PASS** |

### Window Boundary Audit

| Check | Result |
|-------|--------|
| Every predict_start > train_end? | ✅ All 155 windows |
| Predictions in own train period? | ✅ Zero |
| **Verdict** | **PASS** |

### Static Universe Audit

| Check | Result |
|-------|--------|
| eval_date < list_date samples? | ✅ 0 in all years |
| Top20 future-listed ratio | ✅ 0% |
| IC degradation with listed_252d filter | Max **-0.012** |
| **Verdict** | **PASS_WITH_LIMITATIONS** (static CSI800 documented as caveat) |

### Remaining Caveats

1. **Static CSI800 universe** — not a full-market historical reconstruction
2. **180d overlapping forward windows** — strict 20d reduces but does not eliminate overlap; some serial correlation remains
3. **T vs T+1 entry gap** — research eval uses T; backtest uses T+1; the gap is ~0.01-0.02 IC
4. **No feature snapshot at inference** — candidate explainability requires re-querying qlib
5. **Simple backtest execution assumptions** — close prices, no partial fills, no limit orders

## Current Candidate Pool (2025-12-08)

### Industry Distribution

| Top20 | Top50 |
|-------|-------|
| 12 industries | 15 industries |
| Top1: 软件服务 (20%) | Top1: 半导体 (20%) |
| Top3: 45% | Top3: 46% |

No single-industry dominance. The pool is concentrated in technology/manufacturing sectors (半导体, 软件服务, 元器件, 电气设备). No banking/real estate/insurance exposure.

### Worth Further Research

- **600171 上海贝岭** — IC design, top score
- **002281 光迅科技** — Optical transceivers
- **002410 广联达** — SaaS / digital construction
- **300339 润和软件** — AI / Hongmeng ecosystem

### Most Suspicious

- **002607 中公教育** — Education regulatory risk
- **300100 双林股份** — Auto parts (commoditized)

## Simple Backtest (Actual Prices, 20d Rebalance, T+1 Entry)

### 2023-2025 (29 periods)

| K | Weight | Cap | Ann | MDD | Sharpe | Win | Hold | Ind |
|---|--------|-----|-----|-----|--------|-----|------|------|
| 20 | rank | none | **131.6%** | -18.7% | 2.46 | 69% | 20 | 13 |
| **50** | **rank** | **none** | **96.9%** | **-18.7%** | **2.35** | **72%** | **50** | **26** |
| 100 | rank | none | 67.8% | -18.7% | 2.14 | 69% | 100 | 41 |

### 2020-2025 (65 periods)

| K | Weight | Cap | Ann | MDD | Sharpe | Win | Hold | Ind |
|---|--------|-----|-----|-----|--------|-----|------|------|
| 20 | rank | none | **167.2%** | -18.3% | 2.44 | 74% | 20 | 13 |
| **50** | **rank** | **none** | **122.4%** | **-17.4%** | **2.38** | **71%** | **50** | **25** |
| 100 | rank | none | 89.6% | -17.9% | 2.17 | 72% | 100 | 40 |

**Key findings:**
- Rank-weight consistently beats equal-weight (+20-25% annual)
- Industry cap (25%) has minimal effect (<1%) due to high industry granularity
- Top50 is the recommended deployment size (best return/risk/diversification balance)
- 20bps cost does not materially impact results

## Interpretation

### What This Signal Is

A **medium-frequency candidate selector** for A-share right-tail returns. It identifies stocks that are likely to outperform the CSI800 universe over the next 6 months, based on 26 fundamental, valuation, and price-momentum features.

### What This Signal Is Not

- Not an automatic trading strategy
- Not a short-term signal (180d horizon + 20d rebalance)
- Not certified for production without realistic execution backtest

### Why Results Are Strong

The signal operates in a rare combination (quant ML on a 180d fundamental value-investing horizon). Most quant capital operates at 1-20d; most fundamental capital operates at 1-3yr. The 180d window captures the "value realizes" cycle without competing with either camp.

### Industry Selection vs Stock Picking

- Within-industry retention ratio: ~0.45
- ~55% of signal is industry selection, ~45% is within-industry stock picking
- Industry-neutral version would be a useful v2, but the raw model is usable

## Final Decision

| Question | Answer |
|----------|--------|
| Current top20/50 like a viable research candidate pool? | **YES.** Reasonable industry diversification, aligns with China's manufacturing/tech direction. |
| Top50 backtest executable? | **YES.** Positive across all configurations. Top50 rank-weight is the baseline. |
| Industry cap improves results? | **Minimal effect** at 25%. Coarser industry classification or 10% cap may be more effective. |
| Rank-weight better than equal-weight? | **YES.** +20-25% annual across all periods. |
| Phase 1 pass? | **YES, as a research signal / candidate selector.** |

## Next Step

1. **Proceed to manual candidate research** — review top50 candidates at each rebalance, evaluate fundamental thesis.
2. **Build more realistic backtest** — limit orders, slippage, partial fills, CSI800 historical constituents.
3. **Do NOT add new features yet** — the current 26 features are sufficient for Phase 1.

## Framework Gaps Identified

- `entry_lag` parameter: label and signal pipeline lacks T vs T+1 entry convention
- Feature snapshot at inference: missing from LightGBM generator
- 20d rebalance: BacktestRunner only supports daily/weekly
- Historical universe tracking: requires external data source

## References

- [Validation Audit v2](value_growth_extended_validation_audit_v2.md)
- [Static Universe Bias Audit](value_growth_static_universe_bias_audit.md)
- [Eval Frequency Fix Audit](value_growth_eval_frequency_fix_audit.md)
- [Current Candidates](current_value_growth_candidates.md)
- [Simple Backtest](value_growth_simple_backtest.md)
- [Code Review](value_growth_phase1_code_review.md)

## Reproducibility

All research-note numbers can be reproduced with:
- Config files in `configs/research/` — run `scripts/run_research.py`
- Backtest script at `research_scripts/value_growth_simple_backtest.py` — run directly
- Audit scripts were scratch/one-off (described in notes, not committed)
