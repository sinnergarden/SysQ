# Value Growth v2 Candidate Snapshot Tooling

> Research note. Not production approval.

## Background

PR #172 attempted to explain the v2 candidate pool but exposed three key gaps:

1. **raw_score unavailable** from predictions.parquet (only daily_zscore-transformed score saved)
2. **Builder-derived path scores** unavailable — had to use approximate composite proxies
3. **Rank jump attribution** impossible — no per-inference feature snapshot saved

## This PR

Adds `scripts/research/export_candidate_snapshot.py` — a lightweight research script that takes an existing signal run and exports a structured candidate feature snapshot.

## Output Schema

The snapshot CSV contains all fields specified in the design. Key findings from dry-run:

| Feature | Available? |
|---------|-----------|
| raw_score | ✅ True (saved as `score_raw` in SignalStore) |
| Builder-derived path scores | ✅ True (continuation_candidate_score, repair_candidate_score, etc.) |
| All 64 v2 features | ✅ 64/64 = 100% coverage |
| Rank stability w/ prev date | ✅ True |

## Current Limitations

1. raw_score values are identical (3.00) for top20 — the daily_zscore transform produces ties at high percentiles. Rank-based interpretation is still needed.
2. Cross-date rank stability requires feature deltas for jump attribution — not yet implemented.
3. No generated CSV/parquet artifacts committed.

## Usage

```bash
python scripts/research/export_candidate_snapshot.py \
    --experiment-id value_growth_v2_extended_validation \
    --date 2025-12-08 \
    --top-k 100 \
    --lookback-dates 5 \
    --output research_outputs/candidate_snapshot.csv
```

## Next Step

Cross-time rank stability analysis (1mo/3mo/6mo windows) to understand candidate persistence.
