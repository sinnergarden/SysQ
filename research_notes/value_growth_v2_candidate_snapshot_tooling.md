# Value Growth v2 Candidate Snapshot Tooling

> Research note. Not production approval.

## Background

PR #172 attempted to explain the v2 candidate pool but exposed three key gaps:

1. **raw_score** was thought to be unavailable (actually saved as `score_raw` in SignalStore)
2. **Builder-derived path scores** were thought to be unavailable (actually available via QlibAdapter.get_features with semantic builder)
3. **Rank jump attribution** was impossible due to no per-inference feature snapshot

## This PR

Adds `scripts/research/export_candidate_snapshot.py` — a lightweight research script that takes an existing signal run and exports a structured candidate feature snapshot for manual review.

The v2 extended signal resolves to `fwd_ret_180d_raw__daily_zscore` in SignalStore (SignalStore path: `data/research/signals/<signal_id>/<run_id>/predictions.parquet`).

## Rank Delta Convention

- **Positive rank_delta** = rank improved (moved up, e.g. rank 30 → 10 = +20)
- **Negative rank_delta** = rank worsened (moved down, e.g. rank 10 → 30 = -20)
- **abs_rank_delta** = absolute change (always non-negative)

## Output Schema

| Column Group | Columns | Count |
|-------------|---------|-------|
| Identity | trade_date, instrument, name, industry | 4 |
| Rank/Score | rank, raw_score, score, score_pct | 4 |
| Rank Stability | prev_rank, rank_delta, abs_rank_delta, is_new_entry_top20, is_new_entry_top50, rank_stability_flag | 6 |
| Path Classification | path_type | 1 |
| Raw Features (64) | $roe, $pe, ret_120d, rps_120d, ... | 64 |
| Path Scores (4) | continuation_candidate_score, repair_candidate_score, overheat_risk_score, value_trap_risk_score | 4 |
| Path Score Pct (4) | continuation_candidate_score_pct, repair_candidate_score_pct, overheat_risk_score_pct, value_trap_risk_score_pct | 4 |

## Dry-Run Validation

| Check | Result |
|-------|--------|
| `--help` passes | ✅ |
| Top20 sample run | ✅ Output visible |
| continuation_candidate_score in output | ✅ |
| repair_candidate_score in output | ✅ |
| overheat_risk_score in output | ✅ |
| value_trap_risk_score in output | ✅ |
| Path percentile columns in output | ✅ All 4 `_pct` columns |
| raw_score_available | ✅ True |
| Feature coverage | ✅ 64/64 (100%) |
| Generated artifacts committed | ❌ None (no --output used) |

## Usage

```bash
# Export top100 snapshot for latest date with 5 lookback dates
python scripts/research/export_candidate_snapshot.py \
    --experiment-id value_growth_v2_extended_validation \
    --date 2025-12-08 \
    --top-k 100 \
    --lookback-dates 5

# Write to CSV (NOT committed)
python scripts/research/export_candidate_snapshot.py \
    --experiment-id value_growth_v2_extended_validation \
    --date 2025-12-08 \
    --top-k 100 \
    --lookback-dates 5 \
    --output research_outputs/candidate_snapshot.csv
```

## Cross-Time Rank Stability Analysis

Using the tool to analyze rank persistence across adjacent inference dates:

| Window | Top20 overlap | Top50 overlap | New T50 | Retained % | Survivor Avg Delta |
|--------|-------------|-------------|-------|-----------|-------------------|
| 1 day | 19/20 | 44/50 | 6 | 88% | 2.9 |
| 1 week | 15/20 | 44/50 | 6 | 88% | 7.7 |
| 2 weeks | 13/20 | 33/50 | 17 | 66% | 10.2 |
| 1 month | 11/20 | 28/50 | 22 | 56% | 10.4 |
| 3 months | 9/20 | 21/50 | 29 | 42% | 13.4 |
| 6 months | 4/20 | 18/50 | 32 | 36% | 14.1 |

**Key insight:** Survivor rank delta is < 15 across all windows. Stocks that stay in top50 are highly stable; the turnover is driven by new candidates replacing exited ones — normal for a 180d horizon model with 20d rebalancing.

## Current Limitations

1. raw_score values are identical (3.00) for top20 — the daily_zscore transform produces ties at high percentiles. Rank-based interpretation is still needed.
2. Cross-date rank stability requires feature deltas for jump attribution — not yet implemented.
3. No generated CSV/parquet artifacts committed.
4. Feature snapshot only captures current date, not feature deltas vs previous date.
5. Rank jump alerts cannot be automatically attributed without per-inference feature snapshots.

## Next Step

Use this tool to generate candidate snapshots for manual review. Consider adding feature delta tracking in a future PR to enable automatic rank jump attribution.
