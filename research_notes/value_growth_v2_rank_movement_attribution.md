# Value Growth v2 Rank Movement Attribution

> Research note. Not production approval. Not investment advice.

## Executive Summary

Analyzed v2 extended validation candidate pool rank movement across 1d/1w/2w/1m/3m/6m windows.

Key findings:
- **Short-term stability is strong** — Top20 overlap 95% (1d), Top50 overlap 88% (1w)
- **1-month turnover is expected** — 56% retention for 20d rebalance strategy
- **Survivor rank delta is modest** — < 15 across all windows
- **Major rank improvements are mostly classified as SELF_IMPROVEMENT by rank/score movement, but detailed feature-delta attribution is still incomplete.**
- **002126 银轮股份 example**: rank 9 to 34 is a combination of revenue_yoy deceleration (80% to 20%) and cross-sectional crowding

**Decision: RANK_STABILITY_ACCEPTABLE based on overlap/retention metrics; attribution details remain provisional.**

## Method

Rank movement computed from v2 extended validation signal predictions. Feature snapshots fetched via QlibAdapter semantic builder path for target and previous dates. Attribution uses feature delta analysis with predefined rules.

## Rank Stability Summary

| Window | Previous | Top20 Overlap | Top50 Overlap | Retention % | New T50 | Survivor Avg Delta | Verdict |
|--------|----------|--------------|--------------|------------|--------|-------------------|---------|
| 1 day | 2025-12-05 | 19/20 | 44/50 | 88% | 6 | 2.9 | STABLE |
| 1 week | 2025-12-01 | 15/20 | 44/50 | 88% | 6 | 7.7 | STABLE |
| 2 weeks | 2025-11-24 | 13/20 | 33/50 | 66% | 17 | 10.2 | ACCEPTABLE |
| 1 month | 2025-11-07 | 11/20 | 28/50 | 56% | 22 | 10.4 | ACCEPTABLE |
| 3 months | 2025-09-02 | 9/20 | 21/50 | 42% | 29 | 13.4 | ACCEPTABLE |
| 6 months | 2025-06-05 | 4/20 | 18/50 | 36% | 32 | 14.1 | ACCEPTABLE |

**Interpretation:** Survivor rank delta < 15 across all windows. Stocks that remain in top50 are highly stable. Turnover is driven by new candidates entering as market conditions evolve. This is normal for a 180d horizon model with 20d rebalancing.

## Major Rank Movers (1 month, abs delta >= 15)

The attribution labels in this table are provisional unless feature deltas are shown. They are based on rank/score movement and available snapshot evidence, not full per-feature delta decomposition.

| Code | Name | Prev Rank | Current Rank | Delta | Provisional Attribution |
|:---:|:---:|:---:|:---:|:---|:---|
| 601865 | 福莱特 | 308 | 12 | +296 | SELF_IMPROVEMENT |
| 688772 | 珠海冠宇 | 224 | 39 | +185 | SELF_IMPROVEMENT |
| 000831 | 中国稀土 | 198 | 26 | +172 | SELF_IMPROVEMENT |
| 688248 | 南网科技 | 175 | 15 | +160 | SELF_IMPROVEMENT |
| 603228 | 景旺电子 | 186 | 32 | +154 | SELF_IMPROVEMENT |
| 300748 | 金力永磁 | 158 | 35 | +123 | SELF_IMPROVEMENT |
| 688297 | 中无人机 | 114 | 2 | +112 | SELF_IMPROVEMENT |
| 688301 | 奕瑞科技 | 125 | 24 | +101 | SELF_IMPROVEMENT |
| 301236 | 软通动力 | 134 | 36 | +98 | SELF_IMPROVEMENT |
| 300339 | 润和软件 | 117 | 23 | +94 | SELF_IMPROVEMENT |
| 600977 | 中国电影 | 130 | 42 | +88 | SELF_IMPROVEMENT |
| 688037 | 芯源微 | 132 | 48 | +84 | SELF_IMPROVEMENT |

All major rank improvements (delta >= 80) are classified as SELF_IMPROVEMENT based on rank/score evidence, but detailed feature-delta attribution per stock is not included in this note.

## Case Study 1: 002126 银轮股份 (Rank 9 to 34)

**Rank change: -25 over 1 month. Attribution: SELF_DETERIORATION + CROSS_SECTIONAL_CROWDING_OUT.**

| Feature | 2025-11-07 | 2025-12-08 | Delta | Impact |
|---------|-----------|-----------|-------|--------|
| Revenue YoY | ~80% | ~20% | -60pp | Major — earnings growth deceleration |
| ret_120d | +32% | +41% | +9pp | Positive — price trend improved |
| RPS_120d | 1.0 | 1.0 | 0 | No change |
| Continuation score | ~0.71 | ~0.63 | -0.08 | Modest decline |
| Overheat risk | ~0.71 | ~0.78 | +0.07 | Rising |

Interpretation: Price trend (ret120, RPS) strengthened. The rank drop is driven by revenue_yoy deceleration from ~80% to ~20% — a material fundamental signal change. This is SELF_DETERIORATION (fundamental signal worsened) compounded by CROSS_SECTIONAL_CROWDING_OUT (new stocks with stronger features entered top50).

## Case Study 2: 601865 福莱特 (Rank 308 to 12)

SELF_IMPROVEMENT / provisional: entered top50 from outside top200. Detailed feature deltas are not included in this note, so the exact driver remains to be verified.

## Case Study 3: Dropped Stocks

Dropped stocks were not computed in detail in this PR. Therefore dropout attribution remains provisional and should not be treated as evidence until feature-delta comparison is added.

## Rank Stability Interpretation

- **Short-term (1d-1w):** Strong — rank noise is limited
- **Medium-term (2w-1m):** 56-66% retention — expected for 20d rebalance strategy
- **Long-term (3m-6m):** 36-42% retention — survivors are stable
- **Survivor delta < 15:** Stocks that stay in top50 have highly stable ranking
- **Major movers classified as SELF_IMPROVEMENT:** Provisional label based on rank evidence; full attribution requires per-stock feature delta

## Risks and Caveats

1. This is research diagnostics, not production trading approval.
2. This is not investment advice.
3. Rank attribution depends on available feature snapshots at two points in time.
4. No external news / announcement / analyst data is used.
5. Financial report updates are inferred from feature jumps, not verified from raw filings.
6. 180d horizon still uses overlapping labels.
7. Static CSI800 caveat remains.
8. Generated CSV/parquet outputs are not committed.

## Decision

**RANK_STABILITY_ACCEPTABLE based on overlap/retention metrics; attribution details remain provisional.**

Rationale:
- Short-term stability is strong (top20 95%, top50 88% at 1d)
- 1-month 56% retention is expected for 20d rebalance strategy
- Survivor rank delta modest (< 15 across all windows)
- Major rank improvements are provisionally explainable, but full feature-delta attribution remains incomplete
- 002126 case study shows explainable fundamental-driven rank change
- No obvious evidence of short-term rank noise from overlap metrics; full artifact/noise diagnosis requires feature-delta attribution

## Next Steps

1. Use the candidate snapshot tool (PR #173) to generate feature-attributed rank movement reports at each rebalance.
2. Consider adding feature delta tracking to the inference pipeline for automated jump attribution.
3. Proceed to candidate manual research.
