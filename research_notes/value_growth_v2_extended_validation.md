# Value Growth v2 Extended Validation

> Research note. Not production approval.

## Executive Summary

v2 features (64 features, continuation + repair paths) were evaluated against v1 (26 features) across 2023-2025 smoke and a simpler backtest comparison. Results show:

- V2 IC: **0.4132** vs V1: **0.2757** (~50% improvement)
- V2 backtest: ann 127%, MDD -7.0%, Sharpe 2.26 (2023-2025 Top50 rank-weight)
- V1 top50 overlap with V2: **23/50** — meaning 27 new stocks enter top50 under v2
- Candidate pool shifts from pure momentum toward more repair-path names

**Pending: full-period v2 extended validation** (2015-2025, 155 windows, currently running in background) for an apples-to-apples annual IC comparison.

## Experiment Setup

| Parameter | V1 Extended | V2 Smoke | V2 Extended (running) |
|-----------|-------------|----------|----------------------|
| Features | 26 | 64 | 64 |
| Period | 2013-2025 | 2023-2025 | 2013-2025 |
| Windows | 155 | ~30 | 155 |
| Label | fwd_ret_180d_raw | same | same |
| Model | LightGBM 300 | same | same |
| Status | ✅ Done | ✅ Done | 🔄 Background (~2h) |

## V1 vs V2 Candidate Pool Comparison (2025-12-08)

| Metric | Value |
|--------|-------|
| V1 top50 overlap with V2 | **23/50 (46%)** |
| New in V2 | 27 stocks |

**Notable additions in V2:**
- 银轮股份 (热管理龙头), 乐鑫科技 (AIoT), 鸣志电器 (机器人电机)
- 中鼎股份 (PE 22x 最便宜), 立讯精密 (果链龙头)
- 新增股票更偏向估值合理 + 基本面改善，而非纯动量追涨

## Path Score Diagnostics

| Score | RankIC (180d) | IR | Pos Ratio | 说明 |
|-------|--------------|----|-----------|------|
| continuation_candidate_score | -0.0456 | -0.29 | 42.7% | 弱负，续涨标记自身不是信号 |
| repair_candidate_score | -0.0687 | -1.09 | 12.5% | 弱负，纯低估值+挨跌不直接产生收益 |
| overheat_risk_score | -0.0545 | -0.35 | 41.1% | 弱负，过热标记有一定区分度 |
| value_trap_risk_score | +0.0130 | +0.12 | 51.9% | 中性，价值陷阱标记不有效 |

Scores are diagnostic composites, not trading signals. They help interpret the model's output but are not meant to be traded independently.

## Simple Backtest Comparison

> Caveat: V2 data covers 2023-2025 only (smoke range). V1 covers full 2020-2025. The comparison is biased toward V2's stronger subperiod. Fair comparison awaits extended v2 completion.

| Model | Period | Ann | MDD | Sharpe | Win | N periods |
|-------|--------|-----|-----|--------|-----|-----------|
| V1 | 2020-2025 | 123.7% | -17.3% | 2.39 | 71% | 65 |
| V1 | 2023-2025 | 98.0% | -18.6% | 2.37 | 72% | 29 |
| **V2** | **2023-2025** | **127.4%** | **-7.0%** | **2.26** | **74%** | **27** |

V2 shows higher returns and dramatically lower MDD in the same period, but the sample size is limited.

## Risk and Caveats

1. **Static CSI800 universe** — survivorship bias present
2. **180d overlapping label** — 20d-spaced eval reduces but doesn't eliminate overlap
3. **Simple backtest is not production** — no limit order, suspension, partial fill modeling
4. **No feature snapshot committed** — inference-time feature values not saved
5. **PIT correctness depends on ann_date** — verified as safe via `merge_asof(ann_date, backward)`
6. **V2 has higher feature count** — higher overfit risk; extended validation will confirm
7. **Path scores are diagnostic only** — not trading signals
8. **V2 has shorter track record** — extended validation results pending

## Decision

**PASS_TO_CANDIDATE_EXPLANATION** (pending extended confirmation)

Rationale:
- V2 IC 0.4132 vs V1 0.2757 in same period — meaningful improvement
- Candidate pool shifts toward more fundamental/repair names
- Path scores provide interpretability, even if weak as standalone signals
- Backtest shows improved returns with lower drawdown

## Next Steps

1. ⏳ **Wait for v2 extended validation** to confirm annual stability (running in background)
2. Upon confirmation: full period IC table, annual breakdown, v1 vs v2 annual comparison
3. Candidate explanation: manual review of v2 top candidates with feature attribution
4. If extended validation also passes: prune features to reduce overfit risk
