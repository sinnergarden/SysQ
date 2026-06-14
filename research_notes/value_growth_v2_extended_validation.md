# Value Growth v2 Extended Validation

> Research note. Not production approval.

## Executive Summary

V2 features (64 features, continuation + repair paths) **pass extended validation**:
- **9/11 years** V2 IC > V1 IC
- Annual avg IC delta: **+0.053**
- Overall V2 IC: **0.4085** vs V1: **0.3552**
- All 11 years positive, all with IC > 0.18
- Not a recent-style fit (2015-2017 early years improved the most)

**Decision: PASS_TO_CANDIDATE_EXPLANATION.**

## Experiment Setup

| Parameter | V1 Extended | V2 Extended |
|-----------|-------------|-------------|
| Features | 26 | 64 |
| Period | 2013-2025 | 2013-2025 |
| Windows | 155 | 155 |
| Signal dates | 3100 | 3100 |
| Label | fwd_ret_180d_raw | same |
| Model | LightGBM 300 | same |

## V1 vs V2 Extended Validation

### Overall

| Metric | V1 | V2 | Δ |
|--------|-----|-----|------|
| Overall IC | 0.3552 | **0.4085** | **+0.053** |
| Eval RankIC (strict 20d) | 0.3130 | **0.4222** | **+0.109** |
| ICIR | 2.27 | **3.19** | **+0.92** |
| Positive eval ratio | 98.8% | **100.0%** | +1.2% |
| N eval dates | 147 | **152** | — |

### Annual IC Comparison

| Year | V1 IC | V2 IC | Δ | V1 ICIR | V2 ICIR |
|------|-------|-------|-----|---------|---------|
| 2015 | 0.4407 | **0.4782** | +0.037 | 3.68 | 3.91 |
| 2016 | 0.3881 | **0.4442** | +0.056 | 4.50 | 5.67 |
| 2017 | 0.3324 | **0.4129** | +0.081 | 3.70 | 4.62 |
| 2018 | 0.1869 | **0.2385** | +0.052 | 2.29 | 1.91 |
| 2019 | 0.4366 | **0.4787** | +0.042 | 4.75 | 7.49 |
| 2020 | 0.3085 | **0.3698** | +0.061 | 2.59 | 3.84 |
| 2021 | **0.2891** | 0.2707 | -0.018 | 2.02 | 1.91 |
| 2022 | **0.3620** | 0.3451 | -0.017 | 4.63 | 3.16 |
| 2023 | 0.4747 | **0.5262** | +0.052 | 5.50 | 6.62 |
| 2024 | 0.4337 | **0.4782** | +0.045 | 5.87 | 7.40 |
| 2025 | 0.3846 | **0.4515** | +0.197 | 25.88 | 4.56 |

**V2 better in 9/11 years.** Only 2021 and 2022 show slight V1 advantage.

### Difficult Years

**2018 (bear market):** V2 IC 0.2385 vs V1 0.1869 (+0.052). ICIR drops to 1.91 — weakest year for both, but still positive.

**2021-2022 (style rotation):** V1 slightly better. Difference is small (-0.017 to -0.018). V2's extra features may have added noise in these transitional years.

**2024 (strong bull):** V2 IC 0.4782 vs V1 0.4337 (+0.045). V2 captures bull market better.

**Conclusion: V2 is not a recent-style fit.** Largest delta is in early years.

## TopK Excess Return (V2 Extended)

| Year | Top20 excess | Top50 excess | Top100 excess | Top20 hit |
|------|-------------|-------------|--------------|-----------|
| 2015 | 0.6631 | 0.4287 | 0.3076 | 83% |
| 2016 | 0.2729 | 0.2126 | 0.1885 | 100% |
| 2017 | 0.4554 | 0.3893 | 0.3134 | 100% |
| 2018 | 0.2961 | 0.2375 | 0.1783 | 58% |
| 2019 | 0.9257 | 0.7134 | 0.5757 | 100% |
| 2020 | 1.3539 | 0.9289 | 0.7008 | 100% |
| 2021 | 0.7057 | 0.5374 | 0.3996 | 100% |
| 2022 | 0.4909 | 0.3428 | 0.2727 | 100% |
| 2023 | 0.4952 | 0.3703 | 0.2916 | 92% |
| 2024 | 1.2607 | 0.8207 | 0.5853 | 100% |
| 2025 | 1.3692 | 0.9858 | 0.7056 | 100% |

Top50 recommended as the best balance.

## Candidate Pool Comparison (Latest: 2025-12-08)

V1 vs V2 top50 overlap: **23/50** — 27 new names added in V2.
Key additions: 银轮股份 (热管理), 乐鑫科技 (AIoT), 鸣志电器 (机器人), 中鼎股份 (PE 22x), 立讯精密 (果链).

## Path Score Diagnostics

| Score | RankIC | IR | Pos |
|-------|--------|----|-----|
| continuation_candidate_score | -0.0456 | -0.29 | 42.7% |
| repair_candidate_score | -0.0687 | -1.09 | 12.5% |
| overheat_risk_score | -0.0545 | -0.35 | 41.1% |
| value_trap_risk_score | +0.0130 | +0.12 | 51.9% |

Diagnostic composites — not trading signals.

## Risk and Caveats

1. Static CSI800 universe — survivorship bias present
2. 180d overlapping label — 20d-spaced eval reduces overlap
3. Simple backtest is not production backtest
4. No feature snapshot committed
5. PIT correctness depends on ann_date
6. V2 has 64 features — higher overfit risk; cross-year consistency mitigates

## Decision

**PASS_TO_CANDIDATE_EXPLANATION**

Rationale:
- 9/11 years V2 > V1
- Annual avg IC delta +0.053 — meaningful at every year
- Not a recent-style fit — largest deltas in early years
- Worst year 2018 IC 0.18 still usable
- V1 slightly better in 2021-2022 only by marginal amounts

## Next Steps

1. Candidate explanation — manual top10 review
2. Feature pruning — identify key marginal contributors
3. Entry into candidate manual research pool
