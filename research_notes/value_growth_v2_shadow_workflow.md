# Value Growth v2 Shadow Portfolio Workflow

> Research workflow. Not production trading. Not investment advice.

## Executive Summary

Define a repeatable workflow to transition value-growth v2 from research signal to shadow portfolio. The workflow runs every 20 trading days: generate candidate pool, conduct manual review, construct shadow portfolios (model-only and manually filtered), and track performance. After 3-6 months, evaluate whether the process is reproducible enough to consider a small-capital pilot.

## Purpose

- Systematic manual review of model output, not automated deployment
- Compare model-only vs manually-filtered portfolio decisions
- Produce structured records (retain/exclude/watch) with fixed reason labels
- Build evidence for a go/no-go decision on small-capital pilot after 3-6 months

## Inputs

| Input | Source |
|-------|--------|
| Signal | value-growth v2 (64-feature LightGBM, 180d horizon) |
| Snapshot tool | `scripts/research/export_candidate_snapshot.py` |
| Rank movement note | `research_notes/value_growth_v2_rank_movement_attribution.md` |
| Candidate explanation | `research_notes/value_growth_v2_candidate_explanation.md` |
| Diagnostics | `ResearchDiagnostics` (config-driven) |

## Rebalance Cadence

| Frequency | Action |
|-----------|--------|
| Every 20 trading days | Full rebalance: snapshot, review, construct portfolios |
| Weekly | Monitoring only (no forced rebalance) |
| Monthly | Performance review + process check |
| 3-month checkpoint | Evaluate shadow viability |
| 6-month checkpoint | Consider small-capital pilot |

## Candidate Generation

On each rebalance date:

```bash
# 1. Export candidate snapshot (top100)
python scripts/research/export_candidate_snapshot.py \
    --experiment-id value_growth_v2_extended_validation \
    --date $(latest_trade_date) \
    --top-k 100 \
    --lookback-dates 5 \
    --output research_outputs/snapshot_$(date).csv

# 2. Generate path/industry distribution (from snapshot)
# 3. Check rank movement vs previous rebalance date
```

Review artifacts: top20, top50, top100 lists, industry distribution, path classification, rank stability flags.

## Manual Review Workflow

Review top50 on each rebalance date. Assign each stock a manual decision:

| Decision | Meaning |
|----------|---------|
| **KEEP** | Confident in model's selection; include in manually filtered portfolio |
| **WATCH** | Tentative agree; include but flag for monitoring |
| **EXCLUDE** | Disagree with model selection; exclude from manually filtered portfolio |
| **NEEDS_MORE_RESEARCH** | Cannot decide with current information; defer to next cycle |

### Record Template

| Field | Value |
|-------|-------|
| code | 600519.SH |
| name | 贵州茅台 |
| industry | 白酒 |
| rank | 5 |
| path_type | continuation or repair or overheat or value_trap or unclear |
| rank_stability_flag | stable / watch / alert / new_entry |
| model_reason | continuation_candidate_score high + rps_120d high |
| manual_decision | KEEP / WATCH / EXCLUDE / NEEDS_MORE_RESEARCH |
| manual_reason | (see fixed labels below) |
| risk_tags | comma-separated tags |
| final_shadow_pool | ModelOnly_Top20 / ModelOnly_Top50 / ManualFiltered_Top20 |
| review_date | YYYY-MM-DD |
| reviewer | (name) |

### Fixed Reason Labels

Use these labels for `manual_reason` — no free text:

| Label | When to use |
|-------|-------------|
| valuation_too_high | PE percentile extreme, PEG unreasonable |
| overheat_risk | overheat_risk_score high, recent ret120 extreme |
| value_trap_risk | value_trap_risk_score high, fundamentals deteriorating |
| fundamental_unclear | revenue_yoy, profit_yoy, roe delta all conflicting |
| industry_overcrowded | industry concentration > 25% in shadow pool |
| liquidity_concern | volume_spike high, amount_ratio low, limited free float |
| event_risk | known regulatory, legal, or policy uncertainty |
| financial_quality_concern | ocf_margin negative, revenue_yoy negative, debt_to_assets high |
| technical_breakdown | ret_120d negative, max_pullback deep, price_percentile low |
| good_continuation | continuation_candidate_score high, ret120 positive, smooth trend |
| good_repair | pe_percentile low, distance_to_252d_low near 0, fundamentals stable |
| strong_fundamental_confirmation | revenue_yoy_accel positive, roe_delta positive, ocf_margin positive |
| stable_rank | rank stability stable over 3+ evaluation dates |
| new_entry_need_observation | newly entered top50, no rank history yet |

## Shadow Portfolio Construction

### Portfolio Definitions

| Portfolio | Source | Weight | Rebalance |
|-----------|--------|--------|-----------|
| ModelOnly_Top20 | Top20 by model score | Equal | 20d |
| ModelOnly_Top50 | Top50 by model score | Equal | 20d |
| ManualFiltered_Top20 | Top50 minus EXCLUDE, then select up to 20 | Equal | 20d |

### ManualFiltered_Top20 Construction

1. Start with model top50
2. Remove all EXCLUDE stocks
3. Prioritize KEEP stocks for inclusion
4. If fewer than 20 KEEP, fill with WATCH stocks (skip WATCH if risk_tags contain overheat_risk or value_trap_risk)
5. Cap at 20 names
6. Equal weight all selected names

### Why Equal Weight Initially

Equal weight avoids interaction between manual selection and rank-weight logic. If ManualFiltered_Top20 outperforms ModelOnly_Top20, the improvement is attributable to manual review, not to weighting scheme. After 3-6 months, consider rank-weight as an option.

## Shadow Tracking Metrics

| Metric | Calculation |
|--------|-------------|
| Period return | (end_value / start_value) - 1 per rebalance window |
| Cumulative return | (1 + period_return).cumprod() - 1 |
| Max drawdown | min(cumulative_return / cumulative_max - 1) |
| Volatility | std(period_return) * sqrt(252 / 20) |
| Hit rate | (period_return > 0).mean() |
| Turnover | names changed between rebalance / total positions |
| Avg holding count | mean(names held across periods) |
| Industry concentration | top1 / top3 industry weight |
| Model vs manual delta | ManualFiltered_Top20_return - ModelOnly_Top20_return |
| Excluded names future return | mean return of EXCLUDE stocks in subsequent period |
| False positive | KEEP stock that subsequently underperforms top20 median |
| False negative | EXCLUDE stock that subsequently outperforms top20 median |

## Weekly Review Template

```
## Weekly Shadow Review YYYY-MM-DD

### Market Context
- CSI800 trend, sector rotation observations

### Portfolio Status
- ModelOnly_Top20: period_return, cum_return
- ModelOnly_Top50: period_return, cum_return
- ManualFiltered_Top20: period_return, cum_return
- Model vs manual delta

### New Rank Movers (since last review)
- Stocks entering top20
- Stocks entering top50
- Stocks dropping out of top20/50

### Manual Review Changes
- Stocks switched between KEEP/WATCH/EXCLUDE
- Reasons for changes

### Risk Flags
- Stocks flagged overheat or value_trap in top20
- Industry concentration exceeding thresholds

### Actions
- (shadow record actions only, not trading actions)
```

## Monthly Review Template

```
## Monthly Shadow Review YYYY-MM

### Performance Summary
- Month return, cum return, max drawdown, Sharpe (all portfolios)

### ModelOnly vs ManualFiltered
- Return delta this month
- Return delta cumulative
- Decision consistency score

### Best Decisions
- Top 3 KEEP names that outperformed
- Top 3 EXCLUDE names that underperformed

### Worst Decisions
- Top 3 KEEP names that underperformed (false positives)
- Top 3 EXCLUDE names that outperformed (false negatives)

### Excluded Names Review
- Aggregate return of EXCLUDE pool vs KEEP pool

### Missed Opportunities
- Stocks NOT in top50 that the team wishes were considered
- Process change needed? (e.g. adjust feature weights, add external data)

### Rank Stability Review
- Survivor rank delta, new entries, dropouts analysis

### Industry Exposure Review
- Concentration check, any single-industry > 25%?

### Process Changes
- Review manual review consistency
- Update exclusion criteria if systematic pattern emerges
```

## Decision Criteria After 3-6 Months

### Three-Month Checkpoint

Evaluate whether to continue shadow or fix process:

| Condition | Action |
|-----------|--------|
| Manual review reasons recorded consistently for 3+ cycles | ✅ Continue |
| ManualFiltered_Top20 return >= 80% of ModelOnly_Top20 | ✅ Continue |
| ManualFiltered_Top20 drawdown not materially worse than ModelOnly | ✅ Continue |
| EXCLUDE names underperform KEEP names on average | ✅ Manual review adding value |
| Manual review inconsistent (KEEP/EXCLUDE flips without reason) | ❌ Fix review process |
| ManualFiltered_Top20 consistently underperforms ModelOnly by > 20% | ❌ Process review needed |
| Exclusion rate > 40% consistently | ❌ Model trust issue |

### Six-Month Small-Capital Pilot Readiness

All of the following must be true:

- [ ] Shadow process has run for at least 3 months (6+ preferred)
- [ ] At least 3 rebalance cycles completed
- [ ] Manual review reasons recorded consistently with fixed labels
- [ ] No unexplained extreme rank instability
- [ ] EXCLUDE names underperform KEEP names on average
- [ ] ManualFiltered_Top20 return >= 80% of ModelOnly_Top20
- [ ] ManualFiltered_Top20 drawdown not more than 1.5x ModelOnly_Top20 drawdown
- [ ] Candidate explanations usable (feature values + path scores available)
- [ ] Review team has capacity for weekly/monthly monitoring
- [ ] Small-capital pilot account funded (separate from shadow)

If all conditions met: **READY_FOR_SMALL_CAPITAL_PILOT**
If not: **CONTINUE_SHADOW** or **NEEDS_PROCESS_FIX** depending on gap severity

## Risks and Caveats

1. Shadow portfolio is not live trading.
2. No real orders are generated.
3. No brokerage integration.
4. No production approval.
5. No investment advice.
6. Results may differ from real execution due to liquidity, limit-up/down, suspension, slippage, and transaction costs.
7. Manual review may introduce bias (confirmation bias, recency bias, familiarity bias).
8. Static CSI800 and overlapping label caveats remain.
9. ManualFiltered_Top20 may have fewer than 20 names if many EXCLUDE decisions.
10. Weekly review is monitoring only; weekly rebalance would change the signal characteristics.

## Next Steps

1. Create shadow tracking spreadsheet or database (one row per rebalance, one table per portfolio).
2. Start first rebalance cycle: generate snapshot → manual review → construct portfolios → record decisions.
3. Run 3 months minimum before evaluating.
4. If shadow shows consistent manual review value, prepare for small-capital pilot (separate process).
