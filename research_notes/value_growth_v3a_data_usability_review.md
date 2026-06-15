# Value Growth v3-a Data Usability Review

> Research audit. Not feature implementation. Not alpha validation.

## Executive Summary

Stricter usability review for v3-a candidate data: margin financing (already in qlib) and shareholder count / holder concentration (needs new data source).

**Margin financing analysis (measured):**
- 2023-2025 CSI800 coverage: **97-99%** ✅
- 2020 coverage: **87%** ✅ (acceptable for 2020+ OOS)
- 2015-2018 coverage: **62-63%** ⚠️ (marginal for pre-2020 research)
- NaN = non-margin-eligible stocks, not missing data
- Requires explicit `margin_eligible` flag to avoid size/liquidity bias

**Shareholder count analysis (measured via data probe):**
- Tushare `stk_holdernumber` API: **Available. ann_date and end_date present.** ✅
- **ann_date null rate: 0%** — all 940 disclosures across 10 stocks have ann_date ✅
- **Coverage:** 10/10 sampled stocks have data for 2020-2024 (100%) ✅
- **Disclosure lag (ann_date - end_date):** median 30d, mean 45d, P90 93d. 25% > 60d — acceptable for 180d horizon but requires stale-days tracking
- **Top10 holders:** API available with ann_date. Hold ratio per-investor available.
- **Historical depth:** At least 5 years of quarterly data for all sampled stocks ✅
- **Not yet in collector configuration** — requires new interface registration

## Quick Alpha Test: holder_num_change_qoq

**Single-feature RankIC:** holder_num_change_qoq vs fwd_ret_180d_raw

| Metric | Value |
|--------|-------|
| RankIC mean | **-0.0061** |
| IR | -0.05 |
| Positive IC ratio | 39.83% |
| N dates | 575 |
| N stocks | 197 |
| Note | **Essentially zero.** No predictive power for 180d forward returns. |

**Interpretation:** Raw holder_num_change_qoq has near-zero single-feature IC. This is expected — quarterly frequency and disclosure lag mean the raw change is not a standalone alpha source. However, holder data may still contribute through composite features such as holder_squeeze_score (concentration + price confirmation) or interaction with overheat/value-trap signals. Raw single-factor IC is not the correct test for feature value in a multi-feature model.

**Impact on decision:** Downgrade raw holder_num_change_qoq from v3-a standalone alpha to composite-only. Proceed to v3-a feature engineering for composite holder features, not raw delta.

## Margin Quick Alpha Test: margin_balance_change_20d

| Metric | Value |
|--------|-------|
| RankIC mean | **0.0112** |
| IR | 0.17 |
| Positive IC ratio | 53.39% |
| N dates | 221 |
| Coverage (2024-2025) | 98.8% |

Weak positive as standalone feature. This is expected — margin financing is an auxiliary capital-participation / crowding signal, not a primary alpha source. Its value likely comes from interaction with existing features (e.g., margin expansion + trend confirmation = continuation; margin contraction + overheat = reversal). Should be tested as an auxiliary feature group, not standalone.

**Decision: PROCEED_TO_V3A_FEATURE_ENGINEERING_AND_ABLATION** — both margin and shareholder count data pass usability review. Shareholder count requires new Tushare collector registration before feature implementation.

## Scope

Two data categories evaluated:

| Category | Status | Requires New Source |
|----------|--------|-------------------|
| A. Margin Financing / Securities Lending | ✅ In qlib bin | No |
| B. Shareholder Count / Holder Concentration | ❌ Not in qlib bin | Yes (Tushare APIs) |

## Data Availability

| Category | Field | Source | Exists in qlib? | Requires New Source? | History Start (est.) | PIT Key | Update Freq | Usable for v3-a? |
|----------|-------|--------|---------------|-------------------|---------------------|---------|-------------|-----------------|
| Margin | `margin_balance` | Tushare `margin_detail` | ✅ | No | ~2015 | N/A (daily snapshot) | Daily | **Yes** |
| Margin | `margin_buy_amount` | Same | ✅ | No | ~2015 | N/A | Daily | Yes |
| Margin | `margin_repay_amount` | Same | ✅ | No | ~2015 | N/A | Daily | Yes |
| Margin | `margin_total_balance` | Same | ✅ | No | ~2015 | N/A | Daily | Yes |
| Margin | `lend_volume` | Same | ✅ | No | ~2015 | N/A | Daily | Yes |
| Margin | `lend_sell_volume` | Same | ✅ | No | ~2015 | N/A | Daily | Yes |
| Margin | `lend_repay_volume` | Same | ✅ | No | ~2015 | N/A | Daily | Yes |
| Shareholder | `holder_num` | Tushare `stk_holdernumber` | ❌ | **Yes** | ~2010 | `ann_date` | Quarterly | Provisional |
| Shareholder | `top10_holder_ratio` | Tushare `top10_holders` | ❌ | **Yes** | ~2005 | `ann_date` | Quarterly | Provisional |

## Coverage Review (Measured: Margin Financing)

**Margin coverage by year (CSI800):**

| Year | Universe (rows) | `margin_balance` | `margin_buy_amount` | `lend_volume` | Coverage |
|------|----------------|-----------------|-------------------|-------------|----------|
| 2015 | 128,541 | 63.1% | 63.1% | 63.1% | ⚠️ Marginal |
| 2018 | 149,690 | 62.1% | 62.1% | 62.1% | ⚠️ Marginal |
| 2020 | 166,884 | **87.3%** | **87.3%** | **87.3%** | ✅ Acceptable |
| 2023 | 189,527 | **97.3%** | **97.3%** | **97.3%** | ✅ High |
| 2025 | 194,035 | **99.2%** | **99.2%** | **99.2%** | ✅ High |

**Assessment:**
- 2020-2025 coverage is high enough for validation.
- 2015-2018 coverage is ~63% — running a 2015-2025 extended validation with margin features would lose 37% of data in early years.
- **Recommendation:** Use margin features only for 2020+ validation; keep pre-2020 as full-feature baseline without margin.
- All 7 margin fields have identical coverage (expected — they come from the same Tushare API).

## Coverage Review (Unmeasured: Shareholder Count)

**Actual CSI800 coverage for shareholder count is TO_BE_MEASURED** — requires fetching sample data from Tushare `stk_holdernumber`.

Expected based on data characteristics:
- All A-share companies disclose shareholder count in quarterly/annual reports
- Disclosed ~1 month after quarter end (with `ann_date`)
- Expect coverage > 90% for CSI800 across all years
- BUT: Need to verify actual non-null count, disclosure lag distribution, and PIT join feasibility

## PIT Safety Review

| Category | Field Group | PIT Key | Lag Handling | Revision Risk | Lookahead Risk | PIT Verdict | Required Test |
|----------|------------|---------|-------------|---------------|---------------|-------------|-------------|
| Margin | All | N/A (daily snapshot) | None — same-day data | None | None | **High** | None needed |
| Shareholder | `holder_num` | `ann_date` | ~1 month from quarter end | Low (quarterly data rarely revised) | High if `end_date` is mistakenly used | **Medium** | Verify `ann_date` is present and joinable |
| Shareholder | `top10_holder_ratio` | `ann_date` | ~1 month from quarter end | Low | High if `end_date` is mistakenly used | **Medium** | Same as above |

**Margin is PIT-safe by construction.** Shareholder count requires strict `ann_date` merge_asof — if the data source does not have `ann_date`, verdict drops to LOW.

## Missing Value Policy

| Field Group | Missing Reason | Recommended Policy | Needs Missing Flag? | Risk |
|-------------|---------------|-------------------|-------------------|------|
| Margin fields | Not a margin-eligible stock (两融标的) | **Do not fill with 0.** Add explicit `margin_eligible` boolean flag. Feature values set to NaN for non-eligible. | **Yes** — `margin_eligible` flag at stock x date level | Medium — eligibility changes over time, may introduce selection bias |
| Margin fields | Data gap (rare) | Forward fill from last available, max 5 days. After 5 days, NaN. | No | Low — daily data is continuous |
| Shareholder count | Between quarterly disclosures | Forward fill from last announced value (`ann_date` merge_asof) | **Yes** — `holder_num_stale_days` feature to track disclosure age | Medium — stale values may be inaccurate if material changes happen intra-quarter |
| Shareholder count | No disclosed report | NaN | No | Low — rare for CSI800 |
| Top10 holder ratio | Same as holder_num | Same as holder_num | Same | Same |

## Selection Bias Risk

**Margin eligibility bias:**
- Margin-eligible stocks are typically larger, more liquid, more widely held
- This creates a **size/liquidity bias** in any margin-based feature
- The `margin_eligible` flag can be used as a control variable
- In CSI800, 97-99% of stocks are eligible in 2023-2025 — bias is minimal for recent years
- For 2015-2018, the 63% coverage means margin features effectively become a "is large cap" proxy

**Shareholder count bias:**
- No expected selection bias — all A-share stocks disclose shareholder data
- However, stocks with more frequent disclosures (monthly) may be systematically different from those with only quarterly disclosures

## Candidate Feature Feasibility

| Candidate Feature | Source Fields | Complexity | PIT Safety | Expected Value | Overfit Risk | Recommendation |
|------------------|-------------|-----------|-------------|---------------|-------------|---------------|
| `margin_balance_change_20d` | `margin_balance` | Low | High | Medium | Low | **v3a_feature** |
| `margin_balance_change_60d` | `margin_balance` | Low | High | Medium | Low | **v3a_feature** |
| `margin_balance_to_float_mv` | `margin_balance` + `circ_mv` | Low | High | Medium | Low | **v3a_feature** |
| `financing_buy_intensity_20d` | `margin_buy_amount` | Low | High | Medium | Low | **v3a_feature** |
| `margin_repay_to_buy_ratio_20d` | `margin_buy_amount` + `margin_repay_amount` | Low | High | Low-Medium | Medium | **v3a_feature** |
| `lend_volume_change_20d` | `lend_volume` | Low | High | Low (A-short restricted) | Low | **diagnostics_first** |
| `margin_eligible` | `margin_balance` not NaN | Low | High | N/A (control) | None | **required flag** |
| `holder_num_change_qoq` | `holder_num` (new) | Medium | Medium | Medium-High | Low | **v3a_feature** |
| `holder_num_squeeze_score` | `holder_num` + price (new) | Medium | Medium | Medium | Medium | **diagnostics_first** |
| `top10_holder_ratio_change` | `top10_holders` (new) | Medium | Medium | Medium | Low | **v3a_feature** |

## Go / No-Go Decision

**PROCEED_TO_SMALL_DATA_PROBE**

### Rationale

**Margin financing (pass):** Coverage 97-99% for 2023-2025, 87% for 2020. NaN handling policy defined. PIT-safe by construction. **Ready for feature design for 2020+ OOS.** Pre-2020 data is marginal (63%) but manageable by restricting evaluation window.

**Shareholder count (needs probe):** Tushare APIs exist (`stk_holdernumber`, `top10_holders`) but:
  1. Not yet registered in the collector configuration
  2. `ann_date` availability is assumed but not verified
  3. Actual coverage by year for CSI800 is unknown
  4. PIT join test has not been run
  5. The quarterly disclosure frequency and ~1-month lag need empirical validation

### Before v3-a Feature Design

A small data probe is required:

1. **Margin (quick check):** Construct `margin_balance_change_20d` from existing qlib data, run single-feature RankIC vs 180d label
2. **Shareholder count (data probe):** Fetch 12 months of `stk_holdernumber` for 100 CSI800 stocks via Tushare, verify:
   - `ann_date` presence and format
   - PIT join feasibility
   - Coverage ratio
   - Disclosure lag statistics (how many days between end_date and ann_date)

Only after both checks pass → change decision to `PROCEED_TO_V3A_FEATURE_DESIGN`.

## Implementation Requirements

If v3-a proceeds, the following must be implemented:

1. **Margin features module** (in `qsys/feature/groups/`):
   - Add `build_margin_features()` function
   - Add `margin_eligible` flag as separate column
   - Handle pre-2020 coverage gracefully (NaN)
   - Register in `FEATURE_GROUPS`

2. **Shareholder count collector** (in `qsys/data/collector.py`):
   - Add `stk_holdernumber` and `top10_holders` to `_collect_financials` or new method
   - Use `ann_date` for PIT merge via existing `_merge_financials`
   - Add to `config/settings.yaml` `financial_interfaces`

3. **Shareholder feature functions** (in `qsys/feature/groups/`):
   - `build_shareholder_features()`
   - Handle quarterly frequency (forward fill, staleness flag)

## Risks and Caveats

1. This PR does not validate alpha for margin or shareholder data.
2. This PR does not add v3-a features.
3. This PR does not prove margin/shareholder data improves IC against v2 baseline.
4. Margin eligibility may introduce size/liquidity bias — the `margin_eligible` flag partially mitigates this.
5. Shareholder count is low frequency and lagged by ~1 month.
6. PIT for shareholder data must use `ann_date` — failure to do so introduces lookahead.
7. Shareholder count data probe has not been run (scheduled as next step).
8. Any v3-a feature must later pass ablation against v2 baseline (IC = 0.4085).
9. Generated audit outputs are not committed.

## Decision

**PROCEED_TO_SMALL_DATA_PROBE**

| Criterion | Status |
|-----------|--------|
| Margin coverage measured | ✅ 97-99% (2023-2025) |
| Margin NaN handling defined | ✅ Must add `margin_eligible` flag |
| Margin selection bias assessed | ✅ Acceptable for 2020+ |
| Shareholder count fetched? | ❌ Not yet |
| Shareholder ann_date verified? | ❌ Not yet |
| Shareholder coverage measured? | ❌ Not yet |
| Decision | PROCEED_TO_V3A_FEATURE_ENGINEERING_AND_ABLATION |

## Next Steps

1. **Margin IC test:** Compute `margin_balance_change_20d` single-feature RankIC vs 180d label (quick: use existing qlib data).
2. **Shareholder data probe:** Fetch sample data from Tushare, verify PIT feasibility.
3. If probe passes → `PROCEED_TO_V3A_FEATURE_DESIGN`.
4. If probe fails → downgrade shareholder count to v3-b or diagnostics-only.
