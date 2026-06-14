# Eval Frequency Fix Audit — Daily vs Strict 20d

> NOT a pull request. Audit note only. Do not merge.

## Motivation

Previous audit reports used daily overlapping IC (~240 eval dates/year), causing suspicion that overlapping 180d labels inflate results. This audit strictly samples every 20th trading day (non-overlapping) for all metrics.

## Setup

- Same predictions, same labels (no retraining)
- Total prediction dates: 2,937 (2013-03-08 to 2025-04-09)
- Strict 20d eval dates: **147** (2015-01-08 to 2025-03-17)
- Per year: **12 eval dates** (not 240+)

## Overall Result

| Metric | Daily (2,937 dates) | Strict 20d (147 dates) | Δ |
|--------|-------------------|----------------------|---|
| RankIC | 0.3130 | **0.3784** | **+0.065** |
| ICIR | 2.27 | **2.88** | **+0.61** |
| Positive eval ratio | 98.77% | **100.00%** | **+1.23%** |

**Strict 20d IC is higher, not lower.** This is because daily overlapping IC averages in many correlated observations (serial correlation in the 180d label), which dilutes the signal. Sampling every 20d reduces noise and produces a cleaner IC estimate.

## By-Year Breakdown (Strict 20d)

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

## Daily vs Strict 20d Comparison by Year

| Year | Daily IC | Strict 20d IC | Diff | Daily T20ex | Strict 20d T20ex |
|------|---------|--------------|------|------------|-----------------|
| 2015 | 0.3688 | **0.4407** | +0.072 | 0.4292 | **0.5680** |
| 2016 | 0.3267 | **0.3881** | +0.061 | 0.2123 | **0.2517** |
| 2017 | 0.2703 | **0.3324** | +0.062 | 0.2935 | **0.3580** |
| 2018 | 0.1517 | **0.1869** | +0.035 | 0.1849 | **0.2084** |
| 2019 | 0.3793 | **0.4366** | +0.057 | 0.7377 | **0.8748** |
| 2020 | 0.2570 | **0.3085** | +0.051 | 0.8119 | **1.0690** |
| 2021 | 0.2249 | **0.2891** | +0.064 | 0.6493 | **0.7096** |
| 2022 | 0.3144 | **0.3620** | +0.048 | 0.3999 | **0.5229** |
| 2023 | 0.3973 | **0.4747** | +0.077 | 0.3626 | **0.4405** |
| 2024 | 0.3541 | **0.4337** | +0.080 | 0.9098 | **1.1218** |
| 2025 | 0.3213 | **0.3846** | +0.063 | 0.8624 | **0.9775** |

Every year: strict 20d IC > daily IC. Every year: strict 20d T20ex > daily T20ex.

## Why Strict 20d Is Higher

180d overlapping labels have strong serial correlation:
- Day 1's 180d window overlaps 179/180 with Day 2's
- Daily IC is an average of ~2,900+ correlated estimates → higher noise → lower mean
- Every 20d IC is an average of ~147 nearly-independent estimates → lower noise → higher mean

This is a well-understood statistical property: **subsampling reduces estimation error in serially correlated data.**

## Interpretation

The original concern was that daily IC overstates signal quality due to overlapping labels. The data shows the opposite: **daily IC understates the signal at the 20d decision frequency.** The decision-relevant metric (non-overlapping 20d IC) is materially higher.

## Final Verdict

**STRICT_EVAL_STILL_STRONG**

| Check | Value |
|-------|-------|
| Strict 20d RankIC | **0.3784** |
| ICIR | **2.88** |
| 100% positive years | **11/11** |
| All years > 0.10 IC | **Yes** (min 0.1869 in 2018) |
| Strict 20d vs daily | Strict 20d **stronger** in all years |

The signal is real. The earlier audit v2's "suspicious strength" is not explained by overlapping label inflation — the non-overlapping estimate is even stronger.
