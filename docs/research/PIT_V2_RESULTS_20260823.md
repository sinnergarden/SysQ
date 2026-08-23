# PIT-v2 research audit results — 2026-08-23

## Conclusion

The strict, hash-bound PIT results do **not** support promoting the tested
AlphaV1 short-horizon signal. The corrected CSI800 180d result remains close
to the earlier 6–7% annualized finding; the earlier implementation was rejected
because its label/universe provenance and leakage controls were defective, not
because its headline return had to be numerically far away.

| experiment | total return | CAGR | max drawdown | daily Sharpe | backtest id |
|---|---:|---:|---:|---:|---|
| CSI800 PIT-v2, financial/price-volume, 180d | +48.579% | 7.369% | -46.233% | 0.382 | `bt_2021-01-04_2026-07-31_a7fa5cb7` |
| CSI1800 PIT-v2, financial/price-volume, 180d | +226.906% | 23.702% | -49.653% | 0.726 | `bt_2021-01-04_2026-07-31_c3620518` |
| CSI800 PIT-v2, AlphaV1 clean227-as-implemented, 20d | -22.676% | -4.513% | -55.184% | 0.003 | `bt_2021-01-04_2026-07-31_4a2bbe03` |

All three rows cover 2021-01-04 through 2026-07-31 and use the same phase-0
portfolio/execution settings except for the stated signal/universe contract.
The static-current CSI800 result is a survivorship-biased control and is not a
strict PIT result or a valid strategy-performance estimate.

## Point-in-time and maturity evidence

- CSI800 universe: `csi800_pit_v2`, membership SHA-256
  `b0c66998f6eb18430a4ae2fc9d518e1c9b79ad19412c477708af45100dbd1e03`.
- CSI1800 universe: `csi1800_pit_v2`, membership SHA-256
  `567137db93fb9b2bbdb9220f6d0ed813fec233da87948a953a255b2e08b386df`.
- CSI800/CSI1800 180d label SHA-256 values are respectively
  `46ad719f143b5c409472d17d8432a49c9aabf7b04f0751306b9f17ea621acab2`
  and `ffaeb877e3d30d44726c12175a2259752a95828acd3b1509fa1b56c2452023d4`.
- AlphaV1 uses the frozen 227-expression content contract
  `37d3a149b4454b63afe953db8ffde3d61ee291b232407049855aaea0bd009cc3`.
  “clean227” means the exact output of the repository's keyword-based
  `alpha_v1.get_clean_features()` implementation, not a claim that every
  economically fundamental or volume-like expression was removed.
- The Alpha20d signal has 68/68 validated rolling checkpoints and 1,080,683
  rows. Every window's last training sample is exactly 22 trading sessions
  before prediction starts, satisfying the delayed 20d-label maturity guard.
- Backtests apply an additional execution-date PIT membership filter. The
  independent Alpha20d audit found all 312 buys were trade-date members and
  reconciled 619 executions, cash, positions and daily books.

## Interpretation and limits

1. The earlier corrected PIT-v1 phase-0 CAGR of 6.39% and the final PIT-v2
   CSI800 CAGR of 7.37% are numerically close. The v1 run was still unsuitable
   as final evidence because the old membership-span construction created
   artificial gaps and its artifacts were not fully hash-bound.
2. CSI1800 breadth materially improved this one 180d phase-0 run, but the
   result has a roughly -50% drawdown and does not establish deployability.
3. AlphaV1 clean227 with a mature 20d label lost money under otherwise matched
   strategy settings. The tested short-horizon feature/label substitution is
   therefore rejected as a research candidate.
4. PIT membership is reconstructed from monthly Tushare `index_weight`
   snapshots using as-of carry-forward intervals. Unobserved mid-month special
   adjustments cannot be reconstructed from this source.
5. The simulator uses raw open execution/raw close marking and does not model
   corporate actions. Results are research-only and must not be promoted to
   shadow or production from this audit.

## Audit correction

`data/research/pit_v2_final_audit_20260823.json` was modified after its
publication receipt and contains three bad copied identifiers: two 180d label
hashes and the CSI1800 universe path/hash. It is superseded, without overwriting
the old evidence, by:

`data/research/pit_v2_final_audit_20260823_r1.json`

Revision SHA-256:
`ec757a0add4a3fb4abee6be67c3fb7beac4e605d9e715da2456ad43d3671e2ef`.
