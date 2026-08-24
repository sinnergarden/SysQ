# CSI1800 S180 objective research

## Decision

The preregistered `top_tail_v1` weighted-regression candidate is rejected.  It
made every primary selection metric worse than the same-snapshot unweighted
control.  Consequently LambdaRank was not run: the research contract permits
it only when weighted regression passes all selection gates.

`CSI1800_S180_baseline_v1` remains the sole trusted portfolio benchmark.  No
signal, feature, label, strategy, or accounting input of that baseline was
modified by this experiment.

## Controlled experiment

Both arms used the same Qlib source snapshot, PIT CSI1800 membership, 96-feature
contract, S180 label, rolling dates, LightGBM parameters, validation split, and
daily raw-score ranking.  The only experimental variable was the training-row
weight policy:

- default weight: `1.0`;
- cross-sectional label percentile at least 80%: `2.0`;
- cross-sectional label percentile at least 90%: `3.0`.

Weights were computed independently within each mature `label_date`, after the
train/validation boundary was resolved, and applied only to the training
Dataset.  Validation labels and validation weights cannot affect training-row
weights.  There was one fixed policy and no weight search.

The matched control and candidate each contain 68 atomic rolling-window
checkpoints, 2,431,524 prediction rows, 1,351 trade dates, no duplicate keys,
and no missing raw scores.  Their rolling calendars and eligible evaluation key
sets are identical.

## Preregistered selection result

Evaluation uses the 59 mature `predict_start` dates from 2021-01-04 through
2025-10-21, not all daily predictions.  It ranks the untransformed `score_raw`
and joins the frozen S180 labels on `(trade_date, instrument)`.  Confidence
intervals are paired circular moving-block bootstraps (block 9 cohorts, 10,000
replicates, seed 42).

| Metric | Matched control | Weighted | Delta | Paired 95% CI |
|---|---:|---:|---:|---:|
| NDCG@5 | 0.3054 | 0.2553 | -0.0501 | [-0.0916, -0.0098] |
| Top5 forward-180d excess | 15.64% | 9.86% | -5.78 pct | [-12.35, +1.05] pct |
| RankIC | 0.1165 | 0.0823 | -0.0343 | [-0.0589, -0.0097] |
| RankICIR | 4.889 | 4.189 | -0.700 | gate fail |
| Top5 +100% winner capture | 0.7566% | 0.6818% | -0.0748 pct | [-0.6687, +0.5502] pct |

NDCG improved in only one of five calendar years; Top5 excess improved in zero
of five.  All hard PIT, maturity, prediction-hash, label-hash, key-equality and
lineage checks passed.  The negative result is therefore attributable to the
objective change, not mismatched samples or label leakage.

Interpretation: coarse tail upweighting over-emphasized noisy extreme S180
labels.  It reduced broad rank quality and did not improve extreme-winner
capture.  This policy should not enter production and should not be tuned
post-hoc against CAGR.

## Why no candidate portfolio backtest or LambdaRank

The experiment was gated on genuine Top5 selection alpha before portfolio
metrics.  Since the candidate failed every primary selection gate, running a
portfolio backtest would add a CAGR-shaped opportunity to override a clean
negative result.  LambdaRank was explicitly conditional on weighted regression
showing improvement, so it was not trained.

This is a completed negative experiment, not an unfinished LambdaRank run.

## Snapshot boundary

The same-snapshot matched control is not interchangeable with the older frozen
signal behind `CSI1800_S180_baseline_v1`.  The current Qlib snapshot contains
2,431,524 prediction keys versus 2,431,329 in the frozen signal (195 additional
keys).  On common keys, mean daily Spearman is 0.794 and mean daily Top5 Jaccard
(`|A intersection B| / |A union B|`) is 0.309; the less strict overlap fraction
(`|A intersection B| / 5`) is 0.439.  On the 68 retrain dates these are 0.311
and 0.441 respectively.  Therefore the accounting baseline CAGR must not be
attached to this matched control or candidate.

This does not weaken the weighted A/B result: both experimental arms use the
same current snapshot, exact eligible key set, code path, and calendar.  It does
mean that future portfolio experiments must freeze one source snapshot for both
control and candidate and backtest both arms with the corrected accounting
layer.

## Artifact lineage

- Qlib tree identity: 215,138 files, 1,368,801,264 bytes,
  `a5fdbba216b393ebc2d4bbac148a8e74c13ec1ca0daa11fa85690239800dfcc6`.
- Matched-control predictions:
  `0c65cc282d4e896ea9cd100f9da52730c9326624ef33a60dbf3b8ccc9e00eead`.
- Weighted predictions:
  `2df4f0a6511e66d6ed7ed6631c674a334c39b2897d1b6bc281327cda4990448e`.
- Frozen label artifact:
  `ffaeb877e3d30d44726c12175a2259752a95828acd3b1509fa1b56c2452023d4`.
- Evaluation:
  `data/research/evaluations/csi1800_s180_weighted_top_tail_vs_matched_control_v1`.
- Annual cache: nine hash-bound shards covering 2018-2026; an independent
  356,388-row comparison against a direct Qlib frame was exactly equal.
- Signal manifests record repository HEAD `89b8e40c`, because the research code
  was intentionally uncommitted while the artifacts were produced.  Each
  checkpoint identity separately binds the exact generator, shared training,
  and pipeline code hashes used by the run.  The PR commit therefore postdates
  the artifact-level HEAD field without changing those hash-bound inputs.

## Research direction

Do not tune this weighting schedule and do not run LambdaRank as a rescue.  The
next objective work should first improve the target construction or ranking
loss design on paper, with the same preregistered Top5 gates.  Feature expansion
should remain focused on financial acceleration, revisions, and repricing
events.  The corrected `CSI1800_S180_baseline_v1` remains the production-facing
benchmark until a candidate passes selection gates and a same-snapshot
corrected-accounting backtest.

## Verification

- Objective, evaluator, cache, checkpoint and pipeline tests: 78 passed.
- Corrected accounting tests: 189 passed.
- Model-resolution, script-entrypoint, use-case-registry and agent-doc harnesses:
  passed.
- `py_compile` and `git diff --check`: passed.
- The repository-wide pytest command is not currently collectible because 13
  legacy tests import top-level scripts that were previously moved, and one
  unrelated 60d smoke test expects lag 60 while the checked-in config declares
  61.  These pre-existing migration failures occur before the changed tests run
  and were not altered as part of this research PR.
