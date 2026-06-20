# v3a Case Mining and Error Analysis

> Feature research phase complete. This document analyzes where v3a_full succeeds,
> where it fails, and what information is missing.

## Executive Summary

1. **v3a_full is most effective in mid-cap value/repair stocks** (2022-2023 vintage).
   IC drops significantly in 2024-2025 as market regime shifted toward theme/momentum.

2. **Primary failure mode: false positives** — model overweights value/repair candidates
   that continue to deteriorate (value traps). These account for ~2.6% of all cases.

3. **Primary missing signal: event-driven catalysts**. False negatives (model misses big
   moves) are dominated by stocks that surged on industry/sector themes, with no
   fundamental catalyst reflected in the v3a features.

4. **Next data sources suggested**: industry momentum proxies, theme/event indicators,
   unlock/reduction data, earnings forecast data (if permissions become available).

## Overall Case Counts

| Horizon | True Positive | False Positive | False Negative | True Negative |
|:-------:|:------------:|:--------------:|:--------------:|:-------------:|
| 180d | 23,016 (2.6%) | 22,986 (2.6%) | 39,384 (4.5%) | 88,236 (10.0%) |

## Segment IC (180d delayed)

| Year | IC | Count |
|:----:|:--:|:-----:|
| 2020 | −0.048 | 16,225 |
| 2021 | +0.002 | 176,389 |
| 2022 | **+0.153** | 183,673 |
| 2023 | **+0.154** | 189,328 |
| 2024 | +0.028 | 191,689 |
| 2025 | +0.016 | 126,516 |
| **Overall** | **+0.076** | 883,820 |

Key finding: v3a_full IC is heavily concentrated in 2022-2023. Performance
deteriorated significantly in 2024-2025, suggesting the model's feature set
captures a value/repair regime that has been out of favor.

## False Positive Taxonomy (Top 5)

| Reason | Description |
|:-------|:------------|
| Value trap | Low valuation combined with deteriorating earnings/cash flow |
| Momentum exhaustion | High ret_60d/ret_120d before entry, near 120d high |
| Fundamental deceleration | Declining profit_yoy / revenue_yoy trends |
| Working capital pressure | Rising inventory_yoy relative to revenue |
| Margin crowding | High margin_crowding_score signals overheated leverage |

## False Negative Taxonomy (Top 5)

| Reason | Description |
|:-------|:------------|
| Industry theme surge | Stock rises as part of sector-wide move, v3a misses catalyst |
| Short-term momentum ignition | ret_20d turns sharply positive but long-term features lag |
| Small-cap reversal | Low valuation + prior weak performance → sharp reversal |
| Liquidity repricing | Sudden volume increase precedes price move |
| Earnings surprise candidate | Financial data not reflecting recent improvement |

## Next Idea List (Priority Ordered)

| Priority | Idea | Data Source | Horizon |
|:--------:|:-----|:------------|:-------:|
| 1 | Industry momentum / sector rotation proxies | Existing price panel | 60d |
| 2 | Unlock / share reduction events | Tushare (if available) | 60d/180d |
| 3 | Earnings forecast (if permissions upgrade) | Tushare forecast | 60d/180d |
| 4 | Northbound holding changes | Tushare (if available) | 60d |
| 5 | Money flow concentration | Tushare (if available) | 60d |

## Files

- `scripts/research/case_mining_v3a_error_analysis.py` — case mining script
- `artifacts/case_mining/v3a_error_analysis/*.csv` — case data and segment IC
- `tests/research/test_v3a_case_mining.py` — synthetic data test
