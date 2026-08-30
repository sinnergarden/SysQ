# CSI1800 S180 R3 research and certification report

Date: 2026-08-30

Branch revision: `0bab2f24416441a9a56a91b6eaa453a864de193d`

Baseline: `csi1800_s180_baseline_v1_r3`

Final certification status: **BLOCKED**

## Executive conclusion

The audited financial-unit replay, shareholder v2 sidecar, annual feature cache,
68-window rolling training, frozen SignalRun, and CSI1800 PIT S180 backtest all
completed and passed their computational and lineage checks. The backtest used
the same model, label, signal transform, strategy, execution, and accounting
semantics as R2.

R3 is not a certified baseline. The formal certifier produced 13 blocking
source-capability exceptions: 12 feature-dependency expansions of the missing
shareholder revision history and one financial latest-known revision exception.
The DataPack exporter correctly refused the `BLOCKED` receipt, so no portable
certified DataPack was created.

This distinction is deliberate:

- the R3 research result is complete and reproducible from its frozen inputs;
- its arithmetic, cache, signal, and accounting artifacts passed validation;
- its source history is not strong enough to prove the requested latest-known
  PIT semantics, so it must not be described as certified.

## 1. Frozen source and semantic corrections

The trusted market/base terminal remains
`data_sync_20260829T074228987495Z_aab34d4c`, receipt SHA-256
`6f36e14a82434b42121a210eb79f155fe605c5ac3cfff6df2f3e63689cafe2b6`.
No normal `data_sync` was rerun for R3.

Financial canonical data was replayed offline from frozen raw supplier payloads
under the explicit contract
`tushare_fina_indicator_percent_points_to_ratio_v1`. This removed the previous
per-value `abs(value) > 3` unit guess. The old and corrected canonical values
differed as follows:

| Field | Finite values | Values changed | Changed share |
|---|---:|---:|---:|
| `roe` | 5,618,790 | 2,579,801 | 45.9138% |
| `grossprofit_margin` | 5,429,583 | 104,435 | 1.9234% |
| `debt_to_assets` | 5,622,342 | 15,182 | 0.2700% |

The financial replay terminal is
`financial_replay_20260829T194313978246Z_6eb96e56`, receipt SHA-256
`46a628b6cb2150cefa4b6a657b9efb715d7bdc9c610cb0b18ac822d3c2faa3ee`.
The resulting artifact ID is
`e03395ebdb3ba6fdc69b5d7a4014b537c1d2a4d852a6a4bde142bf3973c169a5`.
Qlib readback checked 36,281,874 values with zero mismatch.

The rebuilt shareholder v2 sidecar is
`0baf4e0fc24e5f95f691fb3f332badb8e8d32ed708118c0f1e019a28ce049800`:

- `holder_num.parquet`: 148,749 rows, SHA-256
  `8bb111f2e9d979bb604c155e4a211dd7087949fa1ecabc317128621c92dc5eb3`;
- `top10_holder_ratio.parquet`: 101,658 rows, SHA-256
  `8690a233c1bb5daeddd54ad88a7c8bdef68be3f1a9dde25e513a517935fc45fb`;
- manifest SHA-256
  `87cdfe2e5300028206cacb2afca19c1985936d698818be53e614deeb38acce03`.

The R3 source manifest is
`data/research/source_manifests/csi1800_s180_baseline_v1_r3.json`, SHA-256
`767870e14368b50aac616a97fc9bb993827e50987168cf6a8b5ba43809e6f352`.

## 2. Feature-cache certification

The annual-cache manifest SHA-256 is
`6af8344dfff0a0531a6131ff8543633235347bacdc882e44f38f49188826fdb7`.
Its nine shard identities were independently checked; they declare 5,767,582
rows in total and end at the frozen research terminal, 2026-07-31.

Two separated middle windows were computed through both direct Qlib reload and
the cache path using the final code:

| Window | Direct rows | PIT-consumed rows | Max feature abs diff | Missing-mask mismatch | Max raw-score diff | Top5 order |
|---|---:|---:|---:|---:|---:|---:|
| 28 | 1,976,539 | 1,268,982 | `4.29385e-10` | 0 | `1.11022e-16` | 20/20 days exact |
| 34 | 2,012,307 | 1,268,930 | `3.23586e-10` | 0 | `2.22045e-16` | 20/20 days exact |

The comparator requires exact missing/non-finite masks, exact tree counts and
RankIC, feature absolute tolerance `1e-9`, prediction absolute tolerance
`1e-12`, and exact complete-order/Top5 equality. The two summaries are:

- window 28 SHA-256
  `6eb81b27bd09d693f19dc216f6b6e419a984ffbe2a9a0c39f789c5be88013157`;
- window 34 SHA-256
  `8ec772b3b59b3a3a526b88036a827dbde9e5ca3585bc8df76b4825ace5d2387a`.

## 3. Rolling training and frozen signal

All 68 rolling windows were committed and independently validated. The
checkpoint-set SHA-256 is
`416414a336f2abbe8ddc10d32cd3be2717bfaee0e9485536289a56c986d45cb7`;
the common base-identity SHA-256 is
`83c033cb8391e5590c79888a864da83304a4999d949fb34277264fb30c9ab258`.

The frozen R3 predictions contain 2,431,524 rows across 1,351 trade dates from
2021-01-04 through 2026-07-31 and 2,676 instruments. Independent checks found:

- duplicate `(trade_date, instrument)` keys: 0;
- null scores: 0;
- rows violating `data_date < trade_date`: 0;
- predictions SHA-256:
  `8cd6a5f233dbc3729e771dc082420a85db7fccdf910e949a372c139149b125b9`;
- signal manifest SHA-256:
  `d5696e19f86ffd9ffb94dcbab80b6f948ca588833bc0d8e10f4db2a5e0a9dc42`.

On the 1,186 dates with matured 180-day labels, R3 produced IC `0.03794`,
ICIR `0.5130`, RankIC `0.05333`, and RankICIR `0.6642`.

## 4. CSI1800 PIT S180 backtest

Backtest ID: `bt_2021-01-04_2026-07-31_4468d036`

Backtest manifest SHA-256:
`577720ca3ecc77797a1ba8cb90b20fdf38fde5b0a5fe662baacf2b8fc3e11c80`

| Metric | R1 | R2 | R3 |
|---|---:|---:|---:|
| Initial capital | 10,000,000 | 10,000,000 | 10,000,000 |
| Final value | 48,257,002.62 | 6,112,942.32 | 5,555,953.54 |
| Total return | 382.57% | -38.87% | -44.44% |
| CAGR | 32.66% | -8.46% | -10.02% |
| Sharpe | 1.1575 | -0.1164 | -0.1797 |
| Max drawdown | -41.49% | -74.67% | -68.82% |

R3 completed all 1,351 trading days with 522 orders: 521 fills and one reject.
All six accounting artifacts matched the hashes and row counts declared by the
manifest. The independently recomputed maximum absolute error in
`cash + market_value = total_value` was `1.86265e-9`; final value agreed among
the daily ledger, metrics, and manifest.

The economic semantics compared equal to R2 for every checked field: strategy
template, top five equal-weight entry, 20-trading-day rebalance cadence,
posterior holding rules, PIT universe and hashes, raw open execution, raw close
mark-to-market, T+1, commission, stamp duty, slippage, minimum commission,
strict-prior 20-day ADV, 10% participation reject gate, corporate actions, and
stale-price valuation.

## 5. Why R2 and R3 differ

R2 and R3 have exactly the same 2,431,524 prediction keys; neither side has a
missing or extra stock-day. The score comparison is nevertheless materially
different:

- mean daily Pearson correlation: `0.8292`;
- mean daily Spearman correlation: `0.8244`;
- mean Top5 overlap: `1.41 / 5`;
- median Top5 overlap: `1 / 5`;
- zero-overlap days: 291;
- days with identical Top5: 0 of 1,351.

This locates the R2-to-R3 PnL change before accounting: the corrected financial
units and rebuilt sidecar inputs materially changed cross-sectional ranking,
which changed orders and holdings. It is not caused by different strategy or
accounting parameters. R2 used known-invalid mixed financial units and cannot
be treated as the correct reference merely because its return was higher.

The available artifacts do not support a single-field causal attribution. R3
changes both the financial-unit replay and the shareholder v2 sidecar, and R2
does not retain a complete immutable 96-feature frame that would permit an
exact one-change-at-a-time decomposition. The defensible conclusion is that R3
has stronger data semantics than R2, not that every remaining source-history
risk has been solved.

## 6. Formal certification and DataPack decision

The formal request is
`configs/audit/csi1800_s180_baseline_v1_r3.yaml`, request SHA-256
`19e47eb06b6afc13a80482db5faf0668599235db414343659e0d0e6fb13f10ff`.

Certification output:

- audit ID:
  `15b89cdc875b9563502338a6b4f0b3038e414eded5eddca18ce070623114b84e`;
- status: `BLOCKED`;
- audit receipt SHA-256:
  `298fdaa785d8de131601efba8d424cdccf693bd72fd7b4777551330fbdf55d51`;
- coverage rows: 93,249;
- coverage result: 92,249 `COVERED`, 501 `ACCOUNTED`, 499 `DISJOINT`;
- exceptions: 13, all `BLOCKING`.

Twelve exceptions are expansions of
`SHAREHOLDER_REVISION_CAPABILITY_UNVERIFIED` over the dependent features. The
available bytes contain only one frozen vintage of `stk_holdernumber` and
`top10_holders`; they cannot prove how the supplier revised older rows over
time.

The thirteenth exception is
`FINANCIAL_LATEST_KNOWN_REVISION_CAPABILITY_UNVERIFIED`, affecting 25 financial
features. The current `financial_first_available_v1` projection is
lookahead-safe but permanently keeps the first published value. It does not
switch to a later revision after that revision becomes public, and same-date
`fina_indicator` revisions lack an independently proven effective timestamp.

The exporter was invoked against the frozen certification directory and
rejected it with `only a CERTIFIED baseline can be exported`. No DataPack
directory was created. This is the required fail-closed outcome, not a missing
packaging step.

## 7. What is required for a certified successor

A certified successor needs new source capability or an explicitly approved
semantic change:

1. provide multiple historical shareholder vintages or an authoritative
   supplier revision log with effective times;
2. preserve all financial versions as events and implement
   `financial_latest_known_actual_publication_v1`, including a defensible
   ordering rule for same-date revisions;
3. run data-layer event/as-of acceptance samples and full certification before
   training;
4. because step 2 changes consumed feature values, create a new baseline
   identity and rerun cache checks, 68 windows, signal, and backtest.

Those steps would change the current input semantics and therefore were not
silently introduced into this frozen R3 run.

## 8. Primary local artifacts

- Research config:
  `configs/research/60d/financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_terminal_r3.yaml`
- Certification request:
  `configs/audit/csi1800_s180_baseline_v1_r3.yaml`
- Source manifest:
  `data/research/source_manifests/csi1800_s180_baseline_v1_r3.json`
- Signal directory:
  `data/research/signals/fwd_ret_180d_raw_pit_csi1800__daily_zscore/rolling__financial_rc_180d_rolling_5y_to_202607_v3_pit_csi1800_terminal_r3__v3a_growth_financial_180d_pit_csi1800_terminal_r3__fwd_ret_180d_raw_pit_csi1800__daily_zscore__2021-01-01_2026-07-31/`
- Backtest directory:
  `data/research/backtests/CSI1800_S180_baseline_v1_r3/`
- Certification directory:
  `data/research/certifications/csi1800_s180_baseline_v1_r3/15b89cdc875b9563502338a6b4f0b3038e414eded5eddca18ce070623114b84e/`
