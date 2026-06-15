# Value Growth v3 Capital / Holder / Chip Data Audit

> Research audit. Not feature implementation. Not production approval.

## Executive Summary

Audited 6 data categories for potential value-growth v3 features: margin financing, northbound connect, shareholder concentration, institution/fund holdings, money flow, and chip/cost distribution.

**Key conclusion: Proceed to data source audit for margin financing + shareholder count as v3-a candidates. Defer other categories to v3-b or diagnostics-only.**

Only margin financing and money flow data currently exist in the qlib bin. All other categories require new data sourcing and PIT verification before any feature design.

## Motivation

Value-growth v2 (64 features) achieved 0.4085 IC with fundamental + valuation + price features. The model is strong on "what the company is worth" and "what the price trend is" but has **zero visibility into:**

- Who is buying/selling (smart money vs retail)
- Whether holders are concentrated or dispersing
- Whether margin financing is expanding or contracting
- Whether institutional ownership is increasing
- Whether chip costs suggest support/resistance levels

These dimensions could add orthogonal signal for 180d horizon.

## Data Categories

### A. Margin Financing / Securities Lending (两融)

**Already in qlib bin:** ✅

| Field | Status |
|-------|--------|
| `margin_balance` (融资余额) | Present |
| `margin_buy_amount` (融资买入额) | Present |
| `margin_repay_amount` (融资偿还额) | Present |
| `margin_total_balance` (融资融券余额) | Present |
| `lend_volume` (融券余量) | Present |
| `lend_sell_volume` (融券卖出量) | Present |
| `lend_repay_volume` (融券偿还量) | Present |

**PIT:** Daily data from exchange. High PIT safety — no revision, no estimation. The margin_balance is an end-of-day snapshot.

**Coverage:** Only stocks designated as 两融标的. Coverage has expanded over time but never covers the full A-share market (~2,000 stocks at peak). For CSI800 universe, coverage is ~70-80%. Stocks not in the margin pool get NaN.

**History:** Available from ~2018 onward (varies by stock). Sufficient for 2019-2025 validation.

**For 180d horizon:** Margin balance change over 20-60d is a medium-frequency signal. Lending volume (融券) is directional in A-share (only available for some stocks).

### B. Northbound / Stock Connect (陆股通)

**Not in qlib bin:** ❌

| Candidate Field | Availability |
|----------------|-------------|
| `northbound_holding_shares` | Tushare `hk_hold` API |
| `northbound_holding_ratio` | Derived |
| `northbound_holding_change_20d` | Derived |
| `northbound_net_buy_20d` | Tushare `moneyflow_hsgt` |

**PIT:** Daily data from HK Exchange. High PIT safety. No revision.

**Coverage:** Only stocks eligible for northbound trading (北向通). Coverage includes most CSI800 large/mid-cap stocks. Smaller stocks are excluded.

**History:** Available from ~2017. Sufficient length.

**Concerns:** 北向资金 is heavily watched by retail investors — high correlation with short-term sentiment. For 180d, the signal may be weaker. Also, the net buy data can be noisy day-to-day.

### C. Shareholder Count / Holder Concentration

**Not in qlib bin:** ❌

| Candidate Field | Source |
|----------------|--------|
| `holder_num` (股东户数) | Tushare `stk_holdernumber` |
| `holder_num_change_qoq` | Derived |
| `avg_shares_per_holder` | Derived |
| `top10_holder_ratio` (前十大股东占比) | Tushare `top10_holders` |

**PIT:** **Medium.** These fields use `ann_date` (announcement date) in Tushare, but:
- Holder number is disclosed quarterly in annual/semi-annual reports
- Some companies disclose monthly holder numbers (not all)
- The data is NOT real-time — disclosed ~1 month after period end
- PIT merge must use ann_date, not end_date

**Coverage:** High. All listed companies disclose shareholder information.

**History:** Available from ~2010. Sufficient for full validation.

**For 180d horizon:** **Good fit.** Holder number changes are medium-frequency signals. {"holder_num decreasing + price not falling = smart accumulation"} is a classic signal. The quarterly frequency matches 180d horizon.

### D. Institution / Fund Holdings

**Not in qlib bin:** ❌

| Candidate Field | Source |
|----------------|--------|
| `fund_holding_ratio` | Tushare `fund_portfolio` |
| `institution_holding_ratio` | Tushare `top10_holders` (institution part) |
| `top_fund_count` (基金家数) | Tushare `fund_portfolio` |
| `fund_crowding_score` | Derived |

**PIT:** **Low-Medium.** Fund holdings are disclosed quarterly with ~1-month lag. The data is:
- Only disclosed in quarterly reports (not continuous)
- Subject to survivorship bias (dead funds disappear)
- Only reflects **top-10 holders**, so concentrated holdings may not represent total fund ownership
- Different fund types (public, private, insurance) have different disclosure schedules

**Coverage:** High for large-cap CSI800 stocks. Lower for small/medium caps.

**History:** Available from ~2005.

**Concern:** Heavy look-ahead risk if PIT is not strictly enforced. Fund holdings change intra-quarter. Quarterly snapshots may miss important entry/exit timing.

### E. Money Flow / Large Order Flow

**Already in qlib bin:** ✅ (partially)

| Field | Status |
|-------|--------|
| `net_inflow` (净流入) | Present |
| `big_inflow` (大单净流入) | Present |
| `l1_buy_amount` (level-1 buy) | Present |
| `l1_sell_amount` (level-1 sell) | Present |
| `l1_net_amount` (level-1 net) | Present |

**PIT:** **Medium.** Money flow is **vendor-defined** — it is NOT an official exchange statistic. The calculation methodology (big order threshold, level-1 vs level-2) depends on the data provider (Tushare). This means:
- The definition may change over time
- It is not reproducible without the vendor's algorithm
- "Large order" thresholds vary by stock liquidity
- Hourly/intraday data is not reliably available

**Coverage:** All stocks.

**History:** Available from ~2015 (varies by stock).

**For 180d horizon:** **Debatable.** Money flow is a short-term signal (1-5d). For 180d, the cumulative measures (e.g., `net_inflow_ratio_60d`) may have some predictive value, but the signal-to-noise ratio is likely low.

**Recommendation:** Diagnostics-only. Money flow may help explain short-term rank movement but is unlikely to add marginal IC to a 180d model.

### F. Chip / Cost Distribution

**Not in qlib bin:** ❌

| Candidate Field | Source / Method |
|----------------|----------------|
| `profit_holder_ratio` | Tushare `cyq_chips` |
| `avg_cost_distance` | Derived |
| `cost_concentration_70/90` | Tushare `cyq_chips` |
| `chip_concentration_change` | Derived |

**PIT:** **Low.** Chip distribution is **computed by the vendor** using an estimation model (typically based on average cost method using daily turnover). This means:
- Different vendors produce different results
- The model is not disclosed or auditable
- History is often limited (~3-5 years)
- The "profit holder ratio" is an estimate, not a fact

**Coverage:** All stocks, but data quality varies.

**History:** Short (~3-5 years). Insufficient for 2015-2025 validation.

**Recommendation:** Diagnostics-only at best. v3-c or later if a reliable vendor source is available.

## Audit Criteria Scores

| Category | PIT Safety | Coverage | History Length | Economic Meaning | Implementation Cost | Overfit Risk | Recommended Usage | Priority |
|----------|-----------|----------|---------------|-----------------|-------------------|-------------|-----------------|----------|
| A. Margin Financing | **High** | **High** (CSI800) | **High** (2018+) | **High** | **Low** (exists) | **Low** | **model_feature** | **v3-a** |
| B. Northbound | **High** | **Medium** (CSI800) | **Medium** (2017+) | **Medium** | **Medium** | **Medium** | model_feature | v3-b |
| C. Shareholder Count | **Medium** | **High** | **High** (2010+) | **High** | **Medium** | **Low** | **model_feature** | **v3-a** |
| D. Institution/Fund | **Low-Medium** | **Medium** | **High** | **Medium** | **High** | **Medium** | model_feature | v3-b |
| E. Money Flow | **Medium** | **High** | **Medium** | **Low (for 180d)** | **Low** (exists) | **High** | **diagnostic_only** | v3-c |
| F. Chip/Cost | **Low** | **Medium** | **Low** (3-5yr) | **Medium** | **High** | **High** | **diagnostic_only** | later |

## Recommended v3 Feature Roadmap

### v3-a: Margin Financing + Shareholder Count

**Priority argument:** Both categories have high economic meaning, medium-to-high PIT safety, and good coverage for CSI800. Margin data already exists in qlib bin — no new data source needed. Shareholder count needs a new Tushare fetch but the PIT contract (ann_date merge_asof) is already established in the collector.

**Candidate features:**

| Feature | Source | Type |
|---------|--------|------|
| `margin_balance_change_20d` | Existing qlib field | Derived |
| `margin_balance_change_60d` | Existing qlib field | Derived |
| `margin_balance_to_float_mv` | Existing qlib + circ_mv | Ratio |
| `financing_buy_intensity_20d` | Existing qlib field | Derived |
| `margin_repay_to_buy_ratio_20d` | Existing qlib field | Derived |
| `lend_volume_change_20d` | Existing qlib field | Derived |
| `holder_num_change_qoq` | New source needed | Delta |
| `holder_num_squeeze_score` | New source needed | Composite |
| `top10_holder_ratio_change` | New source needed | Delta |

**Estimated IC gain vs v2 baseline:** Unknown. Requires ablation test after feature implementation.

### v3-b: Institution/Fund Holdings + Northbound

**Priority argument:** These categories have value but require significant data sourcing and PIT verification. Northbound requires less implementation effort (Tushare has a single API). Fund holdings require multiple Tushare APIs and careful quarterly aggregation.

### v3-c: Money Flow Diagnostics Only

**Priority argument:** Money flow data already exists but its value for 180d horizon is questionable. Should be used as a diagnostic/explanation tool, not a model feature. The vendor-defined nature and high overfit risk outweigh the potential marginal IC gain.

### Diagnostics Only: Chip/Cost Distribution

**Priority argument:** Current chip data is vendor-estimated, not factual. The short history and non-reproducibility make it unsuitable for model features. Could be useful for post-hoc candidate explanation ("does this stock have chip support near current price?").

## Rejected / Diagnostics-Only Fields

| Field | Category | Reason |
|-------|----------|--------|
| `moneyflow_persistence_20d` | E | Vendor-defined, high overfit risk |
| `retail_net_inflow_ratio` | E | Vendor algorithm, low signal for 180d |
| `profit_holder_ratio` | F | Vendor model, not auditable |
| `cost_concentration_90` | F | Short history, non-reproducible |
| `super_large_order_net_inflow` | E | Threshold arbitrary, not cross-stock comparable |
| `fund_holding_ratio_change_qoq` | D | Low frequency, look-ahead risk if PIT not strict |

## Risks and Caveats

1. This PR does not validate alpha for any data category.
2. This PR does not add features.
3. PIT safety must be verified at implementation time for all new data sources.
4. Vendor-defined money flow and chip distribution may be non-reproducible.
5. Holder/fund data is low frequency and lagged — PIT merge must use ann_date.
6. Northbound coverage is incomplete for smaller stocks.
7. Any v3 feature must pass ablation against v2 baseline — v2 IC = 0.4085.
8. Margin financing coverage changes over time (new stocks added to program).
9. No generated datasets are committed.

## Decision

**PROCEED_TO_DATA_SOURCE_AUDIT**

The audit identifies margin financing (already in qlib) and shareholder count (new source needed) as the two most promising v3-a categories. However, before feature design can begin:

1. Verify actual NaN coverage for margin fields across CSI800 (by year)
2. Source and inspect shareholder count data from Tushare (`stk_holdernumber`)
3. Confirm PIT ann_date availability for shareholder count
4. Run a quick IC test on margin_balance_change features if feasible

Decision will be updated to PROCEED_TO_V3A_FEATURE_DESIGN after the data source audit confirms coverage and PIT safety.

## Next Steps

1. **Data source audit (this PR):** Verify margin coverage + source shareholder count sample.
2. If pass → **v3-a feature design:** Implement margin + holder features, run ablation vs v2.
3. If fail coverage → **Downgrade to v3-b** and revisit northbound.
4. Money flow and chip diagnostics: can be added to the candidate explanation tooling at any time (no data source needed — already in qlib).
