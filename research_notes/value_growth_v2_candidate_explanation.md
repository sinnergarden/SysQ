# Value Growth v2 Candidate Explanation

> Research note. Not production approval. Not investment advice.

## Executive Summary

The v2 extended model (64 features, 180d horizon, csi800) was evaluated for candidate pool explainability on its latest signal date (2025-12-08). Key findings:

- **Rank stability: STABLE** — top20 overlap 19/20, top50 overlap 44/50, avg abs rank delta 7.0
- **Path distribution:** continuation 8, repair 4, overheat 5, value_trap 3, unclear 30
- **Top10 candidates** are predominantly semiconductor/electronics sector with strong recent momentum
- **46% overlap with V1** — 22/50 same names, 28 new
- **Decision: PASS_TO_MANUAL_REVIEW**

## Method

Path classification uses approximate composite scores from available signal + feature data:
- **continuation:** high score + high ret120 percentile (score_pct\*0.7 + ret120_pct\*0.3 >= 0.7)
- **repair:** low PE percentile + lower score percentile ((1-pe_pct)\*0.5 + (1-score_pct)\*0.5 >= 0.7)
- **overheat:** high score + high ret120 (>= 0.8 on composite)
- **value_trap:** low PE + low score (>= 0.8 on composite)
- **mixed:** both continuation and repair conditions met

Note: Builder-derived path scores (continuation_candidate_score, repair_candidate_score, etc.) require a full semantic pipeline run and were not directly available. The composite approximates their intent.

## Latest Signal Date

**2025-12-08** (from v2_extended_validation, 155 windows, 3100 signal dates)

## Top50 Overview

| Metric | Value |
|--------|-------|
| Total industries | 21 |
| Top1 industry | 半导体 (8) |
| V1-V2 overlap | 22/50 |
| Added in V2 | 28 |
| Removed from V1 | 28 |

## Path Distribution (Top50)

| Path | Count | Notes |
|------|-------|-------|
| Continuation | 8 | Strong recent uptrend, high score |
| Repair | 4 | Low valuation, moderate score |
| Overheat | 5 | High score + high ret120 — monitor for reversal |
| Value trap | 3 | Low PE + low score — fundamentals may be deteriorating |
| Unclear | 30 | No strong path signal — default observation |

## Industry Distribution (Top50)

| Industry | Count |
|----------|-------|
| 半导体 | 8 |
| 元器件 | 6 |
| 软件服务 | 6 |
| 电气设备 | 5 |
| 汽车配件 | 3 |
| 专用机械 | 3 |
| 通信设备 | 2 |
| 小金属 | 2 |
| 化工原料 | 2 |
| 矿物制品 | 2 |
| Other (11 industries) | 11 |

## Top10 Candidate Explanation

| Rank | Code | Name | Score | ret120 | PE | PB | Priority |
|:---:|:---:|:---|---:|:---:|:---:|:---:|:---:|
| 1 | 688322 | 奥比中光-UW | 3.16 | +68% | N/A | 11.4 | high |
| 2 | 688297 | 中无人机 | 3.16 | -10% | N/A | 5.3 | high |
| 3 | 688183 | 生益电子 | 3.16 | +123% | 243.6 | 15.7 | medium (overheat) |
| 4 | 603893 | 瑞芯微 | 3.16 | +27% | 127.7 | 17.9 | high |
| 5 | 688019 | 安集科技 | 3.16 | +45% | 64.5 | 10.4 | high |
| 6 | 600602 | 云赛智联 | 3.16 | -7% | 123.5 | 5.2 | medium |
| 7 | 002335 | 科华数据 | 3.16 | +33% | 92.1 | 4.6 | high |
| 8 | 300432 | 富临精工 | 3.16 | +29% | 69.1 | 6.0 | high |
| 9 | 300735 | 光弘科技 | 3.16 | +4% | 70.0 | 3.9 | high |
| 10 | 600435 | 北方导航 | 3.16 | +24% | 360.4 | 7.3 | medium |

**Notes:**
- Top10 all have identical score (3.16) due to daily_zscore transform producing ties at high percentiles
- 688322 奥比中光: AI vision sensor, strong continuation path, ret120 +68%
- 688297 中无人机: military drone, ret120 -10% (repair path), PB 5.3x reasonable
- 688183 生益电子: PCB leader, ret120 +123% — continuation but overheat risk (PE 243x)
- 002335 科华数据: UPS/data center power, clean continuation with moderate PE 92x

## High Priority Manual Review

| Code | Name | Path | Industry |
|:---:|:---|---:|:---|
| 688322 | 奥比中光-UW | continuation | 元器件 |
| 688297 | 中无人机 | repair | 专用机械 |
| 603893 | 瑞芯微 | continuation | 半导体 |
| 688019 | 安集科技 | continuation | 半导体 |
| 002335 | 科华数据 | continuation | 电气设备 |
| 300432 | 富临精工 | continuation | 汽车配件 |
| 300735 | 光弘科技 | continuation | 元器件 |

## Low Priority / Exclude

| Code | Name | Path | Issue |
|:---:|:---|---:|:---|
| 688183 | 生益电子 | overheat | PE 243x, ret120 +123% — extreme momentum |
| Various | — | value_trap | Low PE + low score, fundamentals may be weak |
| Various | — | unclear | No clear path signal |

## Rank Stability Diagnostics

| Metric | Value |
|--------|-------|
| Adjacent dates | 2025-12-05 vs 2025-12-08 |
| Top20 overlap | 19/20 (95%) |
| Top50 overlap | 44/50 (88%) |
| Avg abs rank delta | 7.0 |
| P90 abs rank delta | 11.2 |
| Max abs rank delta | 124 (new entry) |
| New entries top50 | 6 |
| **Verdict** | **STABLE** |

## Risks and Caveats

1. This is candidate explanation, not production trading approval.
2. This is not investment advice.
3. Path classifier scores are approximate composites, not builder-derived scores.
4. Top50 classification depends on available feature snapshot (PE/PB/ret120 only).
5. No external company research is used.
6. No limit-up/down, liquidity, suspension, or execution feasibility checked.
7. Static CSI800 universe caveat remains.
8. Final manual review is still required.

## Decision

**PASS_TO_MANUAL_REVIEW**

Rationale:
- Rank stability: **STABLE** (top20 overlap 95%)
- Top10 candidates are explainable with available feature data
- No dominant overheat/value_trap concentration in top20
- 46% V1-V2 overlap — meaningful candidate pool shift
- 21 industries in top50 — sufficient diversification

## Next Step

Proceed to manual candidate research on the High Priority list. For each stock, check:
1. Recent financial reports — does revenue/profit trend support model scoring?
2. Industry positioning — competitive advantage?
3. Price trend — does technical setup confirm model's path classification?
4. Risk factors — any company-specific concerns not captured by features?
