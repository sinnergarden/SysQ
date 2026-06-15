# Value Growth v2 Candidate Explanation

> Research note. Not production approval. Not investment advice.

## Executive Summary

The v2 extended model (64 features, 180d horizon, csi800) was evaluated for candidate pool explainability on its latest signal date (2025-12-08). Key findings:

- **Rank stability: STABLE** — top20 overlap 19/20, top50 overlap 44/50, avg abs rank delta 7.0
- **Path distribution:** continuation 8, repair 4, overheat 5, value_trap 3, unclear 30
- **Top10 candidates** are predominantly semiconductor/electronics sector with strong recent momentum
- **46% overlap with V1** — 22/50 same names, 28 new
- **Decision: PASS_TO_MANUAL_REVIEW, with provisional path labels and limited feature attribution.**

**Path labels are provisional** because builder-derived path scores (continuation_candidate_score, repair_candidate_score, etc.) require a full semantic pipeline run and were not directly available from the prediction artifact. The composite scores used here approximate their intent. They are used for manual review prioritization, **not as standalone alpha or final classification**.

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
| 1 | 688322 | 奥比中光-UW | 3.16 | +68% | N/A | 11.4 | research |
| 2 | 688297 | 中无人机 | 3.16 | -10% | N/A | 5.3 | research |
| 3 | 688183 | 生益电子 | 3.16 | +123% | 243.6 | 15.7 | low (overheat) |
| 4 | 603893 | 瑞芯微 | 3.16 | +27% | 127.7 | 17.9 | research |
| 5 | 688019 | 安集科技 | 3.16 | +45% | 64.5 | 10.4 | research |
| 6 | 600602 | 云赛智联 | 3.16 | -7% | 123.5 | 5.2 | monitor |
| 7 | 002335 | 科华数据 | 3.16 | +33% | 92.1 | 4.6 | research |
| 8 | 300432 | 富临精工 | 3.16 | +29% | 69.1 | 6.0 | research |
| 9 | 300735 | 光弘科技 | 3.16 | +4% | 70.0 | 3.9 | research |
| 10 | 600435 | 北方导航 | 3.16 | +24% | 360.4 | 7.3 | monitor |

**Important caveats on top10 scores:**
- **Top10 internal ranking should not be over-interpreted.** All top10 stocks have a score of 3.16 because the daily_zscore transform produces tied values at high percentiles. The ranking within top10 is noisy.
- Manual review should rely on rank, path proxy, valuation metrics, momentum consistency, and feature snapshot when available — not the score alone.
- **raw_score is unavailable** from the predictions.parquet output (only daily_zscore-transformed score is saved).

**Notes on individual candidates:**
- 688322 奥比中光-UW: AI vision sensor, strong continuation prox, ret120 +68%. Continuation path but requires valuation/earnings-growth verification (PE N/A).
- 688297 中无人机: repair/provisional: weak price momentum (ret120 -10%), valuation not directly comparable due to PE N/A; requires manual fundamental review.
- 688183 生益电子: overheat risk — PE 243x, ret120 +123%. Value-trap proxy low but extreme momentum warrants caution.
- 002335 科华数据: UPS/data center power, continuation path, moderate PE 92x. Requires earnings-growth verification.

## High Priority Manual Research

High priority means worth manual research, **not high-conviction buy candidate**.

| Code | Name | Path | Industry | Risk Note |
|:---:|:---|---:|:---|:---|
| 688322 | 奥比中光-UW | continuation | 元器件 | requires valuation/earnings-growth verification |
| 688297 | 中无人机 | repair | 专用机械 | requires manual fundamental review |
| 603893 | 瑞芯微 | continuation | 半导体 | requires valuation/earnings-growth verification |
| 688019 | 安集科技 | continuation | 半导体 | requires valuation/earnings-growth verification |
| 002335 | 科华数据 | continuation | 电气设备 | requires valuation/earnings-growth verification |
| 300432 | 富临精工 | continuation | 汽车配件 | requires valuation/earnings-growth verification |
| 300735 | 光弘科技 | continuation | 元器件 | requires valuation/earnings-growth verification |

## Low Priority / Exclude

| Code | Name | Industry | Path | Main Issue |
|:---:|:---:|:---|:---|:---|
| 688183 | 生益电子 | 元器件 | overheat | PE 243x, ret120 +123% — extreme momentum, reversal risk |
| 603728 | 鸣志电器 | 电气设备 | overheat | ret120 strong, score high — monitor for reversal |
| 688778 | 厦钨新能 | 电气设备 | overheat | score high, recent price surge |
| 600580 | 卧龙电驱 | 电气设备 | overheat | ret120 strong, extended rally |
| — | (remaining overheat) | — | overheat | high score + high ret120, monitor |
| 000887 | 中鼎股份 | 汽车配件 | value_trap | Low PE (22x) but low score percentile — fundamentals may be deteriorating |
| 002056 | 横店东磁 | 电气设备 | value_trap | Low score despite low PE — check fundamentals |
| — | (remaining value_trap) | — | value_trap | Low PE + low score, fundamental check required |

## Rank Stability Diagnostics

| Metric | Value |
|--------|-------|
| Adjacent dates | 2025-12-05 vs 2025-12-08 |
| Top20 overlap | 19/20 (95%) |
| Top50 overlap | 44/50 (88%) |
| Avg abs rank delta | 7.0 |
| P90 abs rank delta | 11.2 |
| Max abs rank delta | 124 (new entry from outside top200) |
| New entries top50 | 6 |
| **Verdict** | **STABLE** |

### Rank Stability Alerts

The 6 new Top50 entries (from outside top200 in previous date) have rank_delta > 100. These entries represent stocks that were ranked below 200 on 2025-12-05 and jumped into top50 by 2025-12-08.

| Code | Name | Prev Rank | Curr Rank | Rank Delta | Probable Reason |
|:---:|:---:|:---:|:---:|:---|:---|
| 688301 | 奕瑞科技 | >200 | 24 | >+176 | Reason unavailable with current feature snapshot |
| 600977 | 中国电影 | >200 | 42 | >+158 | Reason unavailable with current feature snapshot |
| 301536 | — | >200 | 43 | >+157 | Reason unavailable with current feature snapshot |
| 688213 | — | >200 | 44 | >+156 | Reason unavailable with current feature snapshot |
| 688037 | — | >200 | 48 | >+152 | Reason unavailable with current feature snapshot |
| 300757 | — | >200 | 50 | >+150 | Reason unavailable with current feature snapshot |

**Note:** Explaining rank jumps requires per-inference feature snapshots, which are not currently saved by the inference pipeline. This is a known framework gap (documented in Phase 1 code review). These jumps may be driven by normal model behavior (stock re-enters top50 after being ranked 51-200) or by sharp feature movements. Without feature attribution, root cause is indeterminate.

## Risks and Caveats

1. This is candidate explanation, not production trading approval.
2. This is not investment advice.
3. **Path classifier scores are approximate composites**, not builder-derived scores. Path labels are provisional.
4. Top50 classification depends on available feature snapshot (PE/PB/ret120 only).
5. **Top10 ranking is noisy** — all scores tied at 3.16 due to daily_zscore transform.
6. No external company research is used.
7. No limit-up/down, liquidity, suspension, or execution feasibility checked.
8. Static CSI800 universe caveat remains.
9. Final manual review is still required.
10. Rank jump alerts cannot be attributed without per-inference feature snapshots.

## Decision

**PASS_TO_MANUAL_REVIEW, with provisional path labels and limited feature attribution.**

Rationale:
- Rank stability: **STABLE** (top20 overlap 95%)
- Top10 candidates are explainable with available feature data, though path labels are provisional
- No dominant overheat/value_trap concentration in top20
- 46% V1-V2 overlap — meaningful candidate pool shift
- 21 industries in top50 — sufficient diversification
- Path labels are provisional but sufficient for manual review prioritization

Limitations:
- Builder-derived path scores unavailable
- Top10 score ties limit ranking precision
- Rank jump alerts un-explained without feature snapshot tooling

## Next Step

1. Proceed to manual candidate research on the High Priority list. For each stock, check:
   - Recent financial reports — does revenue/profit trend support model scoring?
   - Industry positioning — competitive advantage?
   - Price trend — does technical setup confirm model's path classification?
   - Risk factors — any company-specific concerns not captured by features?
2. **Next PR should add candidate-level feature snapshot / attribution tooling** if raw feature values and builder-derived path scores continue to be unavailable.
