# A0–A5 Execution-Policy Ablation + E1 — Final Report

Date: 2026-08-17
Signal: `financial_rc_60d_180d_50_50__daily_zscore` / run `blend__007a93600f45de00`
Window: 2021-01-04 → 2026-07-31, Top5, weekly rebalance, equal-weight entry + hold drift.
Baseline A0 = hold day-1 top-5 forever (no exits). A5 = full 4-rule posterior (canonical reproduce: ret +234.9% vs run2 +235%, same exit counts 129/228/17/6). E1 = pure score-refresh (rank_exit; §4).

---

## 1. A0–A5 Total Table (clean, re-run)

| metric | A0 none | A1 hard_stop | A2 score_delta | A3 winner_trailing | A4 stale | A5 all |
|---|---|---|---|---|---|---|
| Total return | −1.0% | **+159.9%** | +119.2% | +97.9% | +187.7% | **+234.9%** |
| CAGR | −0.2% | +19.5% | +15.8% | +13.6% | +21.8% | **+25.3%** |
| MaxDD | −58.4% | −55.4% | −60.1% | **−40.1%** | −55.6% | −50.6% |
| Calmar | −0.00 | 0.35 | 0.26 | 0.34 | 0.39 | **0.50** |
| Ann vol | +41.9% | +49.3% | +39.8% | +34.3% | +48.4% | +41.4% |
| Avg exposure | 99.9% | 99.5% | 89.5% | **74.9%** | 99.7% | 87.0% |
| Turnover (B) | 0.01 | 0.07 | 1.14 | 0.18 | 0.09 | 2.23 |
| Fills | 5 | 65 | 551 | 57 | 65 | 764 |
| Closed episodes | 0 | 30 | 273 | 26 | 30 | 380 |
| Median holding (d) | — | 22 | 19 | 99 | 43.5 | 13.5 |
| Win rate | — | 0% | 55.3% | 100% | 6.7% | 48.2% |
| Median episode ret | — | −10.4% | +1.1% | +17.5% | −7.9% | −0.8% |
| **Realized PnL** | — | **−4.2M** | +13.5M | **+20.0M** | **−4.0M** | +24.2M |

Yearly return: A0 +44/−28/−19/+49/+3/−22 · A1 +31/−31/+16/+56/+68/−9 · A2 −27/−17/+41/+47/+57/+15 · A3 +52/−16/+3/+16/+62/−19 · A4 −7/+17/+28/+42/+85/−17 · A5 −17/−5/+45/+53/+77/+12 (2021–2026).

## 2. Rule-Effect Table (A1–A4 vs A0)

| metric | A1 hs | A2 sd | A3 wt | A4 stale |
|---|---|---|---|---|
| ΔCAGR | +19.7pp | +15.9pp | +13.8pp | +22.0pp |
| ΔMaxDD | +3.0pp | −1.7pp | +18.3pp | +2.8pp |
| ΔCalmar | +0.36 | +0.27 | +0.34 | +0.40 |
| rule events | 30 | 273 | 26 | 30 |
| verdict (heuristic) | likely helpful | **mixed** | likely helpful | likely helpful |

*Caveat:* ΔMaxDD for A3 is ~74% mechanical cash drag (exposure-adjusted: −53.6% vs A0 −58.4% ≈ only 4.8pp real). Episode-level deltas are undefined vs A0 (A0 has no closed episodes).

## 3. Differential-Return Results (Layer 4) — CORRECTED METHODOLOGY

SwapEdge is now measured with a **common start at the replacement's entry date** for both old and new, over the same +20/+60 market-calendar horizon; the exit→entry cash gap is reported separately; forward returns are **null (never a stale prior close)** when the symbol has no close on the exact reference or horizon-end date. All swap edges show **no statistically clear evidence of a nonzero effect** (paired Wilcoxon / sign test p = 0.16–1.00; n = 26–265). Report as directional point estimates only; do not read them as "proven noise".

**hard_stop** (A5 n=129; A1-pure n=30)
- 94% (121/129) fire on **score-OK** stocks — price fell, long-term score did NOT deteriorate. ✓
- Post-exit (common start): old next60 mean +17.4% / median +7.0%; >+5% recovery 54%, continues <-5% 28%.
- swap_edge60: A5 mean +1.4% / median +0.0%; A1-pure mean −1.5% / median +0.0%. (Old misaligned window overstated this: A5 mean +2.3% / A1-pure median +4.4%.)
- **Conditional structure (verified):** when the stopped name **keeps falling** (old next60 < −5%, n=31) the stop adds swap60 mean **+20.6%** / median +10.8% (Wilcoxon p=0.0007); when it **recovers** (>+5%, n=61) it costs **−11.1%** / −8.8% (p=0.048). Unconditional mean-zero (p=0.98) because benefit and cost offset → a **real tail-protection mechanism**, not noise, and not a value-add on average.
- 16% (21/128) of "replacements" are re-buys of the same stopped stock; median exit→entry gap = 2 trading days.
- Realized PnL on stops: always negative (mean −12%); 17 events (exit ≥ 2026-05-22) right-censored out of 60d stats.
- A1's portfolio return is **not** from the stops: realized PnL −4.3M, the +160% is one **open** position (300570.SZ ≈ +11.6M, ~72% of the portfolio gain).

**score_delta** (A5 n=228; A2-pure n=273)
- 72% (165/228) of exits close at a **positive** return; 64% (145/228) at >+2% (identical under realized_return and old_return_at_exit); buckets profit/flat/loss → old next60 mean +16.6/+14.3/+8.3%.
- Sold stocks recover; swap60 mean by bucket −1.1/−4.0/+2.4%; all A5 mean **−1.0% / median +0.1%**; A2-pure mean −2.7% / median +0.0%. (Old misaligned window reported all-runs mean −3.3% — most of that negative skew was window misalignment.)
- A2-pure has the **worst MaxDD of any run (−60.1%, below A0)** and highest single-rule turnover (1.14B).
- → score_delta has **not demonstrated expectation-deterioration value**; churn value lives in redeployment, not the exit signal.

**winner_trailing** (A5 n=17; A3-pure n=26; combined 43)
- Realized median +15.2% vs MFE median +36.8% → ~22–25pp giveback by construction (combined giveback mean +23.7%).
- Combined old next60 median −3.8% (pos 43%) → sold winners did **not** continue rising; the "lost right tail" is largely a strawman.
- **New finding:** the *replacement* underperforms the sold winner short-term — swap20 mean −7.8% / median −1.6% / pos 24% (A5) and mean −4.9% / median −2.7% (A3-pure); swap60 mean −6.5% (A5) / −6.9% (A3-pure), medians ~0.
- A3 locks in **+20.0M realized** (0 losing symbols — least concentrated) but total return lowest (+97.9%): 25% cash drag + open losers held forever.
- n < 30 → **insufficient sample** for the A5-specific call.

**stale_replacement** (A4-pure n=30)
- swap_edge60 mean +5.8% / median +1.4% (unchanged vs old method: stale swaps have gap = 0 days, so exit/entry windows were already aligned).
- Median replacement (+10.6%) **underperforms** holding the sold stock (+15.5%) on next60. "Opportunity-cost exit" not established.
- A4's +188% is 62% one **open** stock (300548.SZ +11.65M); realized PnL −4.1M.
- In A5 only 6/380 fires → near-zero marginal value in the full policy.

## 4. E1 — pure score-refresh baseline (rank_exit)

E1 = "hold what's in the current top-5, and only what's in it": on each weekly
rebalance, **sell any held name that has dropped out of the current top-5**,
refill vacancies **equal-weight 1/5 from the current top-5**, retained positions
**hold-drift** (no reweight), and **no** hard_stop / score_delta /
winner_trailing / stale / periodic reweight. One variant only, no threshold
tuning. backtest_id `bt_2021-01-04_2026-07-31_f08dc9cf` (A5 = `3d695c20`).

### 4.1 Three-way table — E1 vs rank_weight_top5 vs A5 (full posterior)

| metric | E1 rank_exit | rank_weight_top5* | A5 all |
|---|---|---|---|
| Total return | **+464.0%** | +494.0% | +234.9% |
| CAGR | +38.1% | +39.4% | +25.3% |
| MaxDD | −53.3% | −60.2% | **−50.6%** |
| Calmar | 0.71 | 0.66 | 0.50 |
| Avg exposure | 97.1% | 99.0% | 87.0% |
| Turnover (B) | 6.14 | 7.38 | 2.23 |
| Fees comm+stamp (M) | 4.91 | 5.90 | 1.78 |
| Fills | 1311 | 2056 | 764 |
| Median holding (d) | 6.0 | 6.0 | 13.5 |
| Closed episodes | 653 | 657 | 380 |
| Net episode PnL (M) | +49.3 | +50.4 | +22.4 |
| Top1 share of NET PnL | 26.0% | 27.9% | 43.3% |
| Top5 share of NET PnL | 59.5% | 61.3% | 78.7% |
| Top1 share of POSITIVE PnL | 9.0% | — | 12.8% |
| Top5 share of POSITIVE PnL | 20.5% | — | 23.2% |
| 41d+ winners (n / median ret) | 18 / +7.3% | 18 / +4.6% | 23 / +11.5% |

Yearly return: E1 −5/−16/+136/+57/+88/+6 · RW −13/−11/+182/+61/+82/−2 · A5 −17/−5/+45/+53/+77/+12 (2021–2026).

*\*rank_weight_top5 is NOT a strategy-level benchmark:* it uses
`allocation_method=rank_weight` on template `rank_weight_top5_financial_rc_50_50`
(E1/A5 use `equal_weight_entry_hold_drift` on `posterior_confirmed_top5_...`),
and rebalances to target rank-weights, **trimming winners by design** (657/657
exits via rebalance_to_target_weight — e.g. on 603256.SH in the same window RW
captures +31.8% vs E1's +125.4%). RW vs E1/A5 is comparable on
**signal + costs + dates only**, not on allocation. The E1-vs-A5 headline is
unaffected.

### 4.2 Headline — verified, with a magnitude caveat

- **E1 beats A5 by +229pp (+464% vs +235%) after fees**, and wins despite paying
  **2.75× the fee drag** (4.91M vs 1.78M) and **2.75× the turnover** (6.14B vs
  2.23B). Apples-to-apples: identical signal / universe / window / costs /
  equal-weight entry + hold-drift skeleton / weekly rebalance; **only the exit
  policy differs** (E1: rank_exit with all rules disabled; A5: 4 rules).
- **The ~229pp magnitude is 2023-dominated and fragile.** Year gaps E1−A5 =
  +12/−11/**+91**/+4/+11/−6pp (2021–2026). Zeroing 2023 in both arms collapses
  the gap to ~8–10pp. **Direction (pure refresh > 4-rule policy) is robust; the
  magnitude is not a stable structural finding.**
- **Not exposure / not leverage:** exposure-adjusted A5 (+270%) is still ~194pp
  below E1; A4 has the HIGHEST exposure (99.7%) but only +188% → "high exposure
  wins" is refuted; the differentiator is **refresh frequency / turnover**
  (0.01B→−1%, 0.09B→+188%, 2.2B→+235%, 6.1B→+464%). E1 never exceeds 100%
  exposure, zero negative-cash days, accounting identity holds to 1.5e-8.
- **Calmar is fragile:** E1 0.71 → ~0.33 if 2023 dropped; A5 0.50 → ~0.25 if
  2025 dropped. Not robust risk-adjusted figures.
- **Concentration direction real, interpretation softened:** A5 is more
  concentrated than E1 under **every** denominator, but A5's MaxDD (−50.6%) is
  **LOWER** than E1's (−53.3%) → concentration is **not** the drawdown driver.
  Net-PnL shares: A5 top1 43%/top5 79% vs E1 26%/60%; positive-PnL shares:
  A5 13%/24% vs E1 9%/21%.
- **hard_stop is conditionally significant tail protection** (§3): +20.6% mean
  swap edge when the stopped name keeps falling (p=0.0007), −11.1% when it
  recovers (p=0.048), mean-zero unconditional. Consistent with "the rules impose
  staleness that lags the current top-5", not "the rules are noise".
- **A5's top1 winner is the same stock as E1's (603256.SH), entered ~3 weeks
  later** (2025-11-24 vs 2025-11-03): the rule-based policy mis-times entries
  even on its best winner.
- **E1's exits (all `trade_reason='rank_exit'`) are NOT covered by the swap-edge
  Layer4 lens** (0 events): the +464%/+235% headline is a run-metrics result
  (metrics.json `total_return`), orthogonal to §3.
- **Reproducibility:** the `rank_exit` feature is committed on branch
  `research/backtest-execution-ledger` (PR #242); the E1 run regenerates with
  backtest_id `f08dc9cf` and identical metrics.

## 5. Adversarial verification (A0–A5: 4 lenses, 298k tokens · E1: 4 lenses, 375k tokens)

- **Confirmed:** all headline numbers; identical signal/window/top_n across runs (no leakage, no config drift); A5 == run2 canonical.
- **The load-bearing correction:** A0 is a degenerate baseline (5 fills, 0 sells — "hold the day-1 top-5 forever"), so every A1–A4 "vs A0" delta conflates *refresh-into-current-top-5* with *exit-rule quality*. The slot-refresh alpha is real; the exit-rule differences have **no statistically clear evidence** in this sample.
- **Concentration is high and directionally real, but NOT the drawdown driver:** the headline "top-1 = 47%" was an artifact of dividing by NET PnL. As shares of NET episode PnL A5 top1 43%/top5 79% (vs E1 26%/60%); as shares of POSITIVE PnL A5 13%/24% (vs E1 9%/21%). A5's MaxDD (−50.6%) is **LOWER** than E1's (−53.3%), so the more-concentrated portfolio has the smaller drawdown. ~98% of the A0→A5 gap is ~5 stocks; rule rankings flip yearly.
- **Every swap-edge ordering (stale>hard_stop>score_delta>trailing) shows no statistically clear evidence unconditionally** (all p ≥ 0.16; hard_stop is conditionally significant as tail protection, §3).
- **E1 round (4 lenses + synthesis, 375k tokens):** E1 code faithful (653/653 sells are rank_exit on rebalance days only; equal-weight refill median 0.1991 vs 0.20 target; hold-drift, no trims; position_count 5/5 never 0/>5); backtest_id hashes reproduce exactly (A5 `3d695c20`, E1 `f08dc9cf`); headline survives every refute-attempt (no leverage, no signal/cost drift, deterministic). The only load-bearing repro caveat was the uncommitted feature — **now committed** (branch, PR #242).

## 6. Answers to the 6 questions

**Q1 hard_stop: genuinely reduces risk & worth the return cost?**
NO on the "reduces return" read it is usually given; E1 now quantifies it: hard_stop is **conditional tail protection** — it adds +20.6% mean swap edge when the stopped name keeps falling (p=0.0007) but costs −11.1% when it recovers (p=0.048), mean-zero unconditional (p=0.98, §3). MaxDD is only −3pp vs A0 (and A5's MaxDD is *below* E1's), annualized vol UP (41.9→49.3%). It fires 94% on score-OK names. Realized stops are pure losses. Net: a capital-refresh trigger that helps exactly when it "should", but is not a free loss-cutter against pure refresh. Confidence: risk-facts HIGH, refresh-value LOW.

**Q2 score_delta: valid exit information?**
NO. 72% of exits close positive (64% at >+2%); sold stocks recover +8.3–16.6%/60d; replacement swap is negative-skewed; A2-pure has the worst MaxDD. score_delta has **not demonstrated expectation-deterioration value** (swap60 all-year p>0.4, overall p=0.97; behaves like churn, not selection). Confidence: "no demonstrated value" MODERATE (no time-matched control); "harmful" LOW.

**Q3 winner_trailing: protect winners or truncate the right tail?**
Neither, cleanly: it mechanically converts MFE (+37%) to realized (+15%, ~23pp giveback) and improves MaxDD — but ~74% of that MaxDD gain is cash drag. Sold winners did NOT continue (next60 median −3.0%), so "truncated tail" is a strawman; n=17–43 → **insufficient sample**. It is the only rule that locks in realized profit (+20M). Confidence: LOW (underpowered).

**Q4 stale_replacement: valuable?**
Standalone: best single-rule CAGR (+21.8%) at 0.09B turnover, but its swap edge is 3-outlier-driven and median replacement underperforms holding the sold stock → "valuable" UNSUPPORTED. In A5: only 6 fires → negligible. Confidence: LOW.

**Q5 where does the posterior policy's loss come from relative to pure score rebalance?**
E1 answers it: the loss is **staleness** — the rules lag the current top-5, not "rules make bad decisions in isolation". E1 (pure refresh) **+464% vs A5 (4 rules) +235%** after fees, with E1 paying **2.75× fees/turnover** and still winning. Mechanism: median holding 6.0d (E1) vs 13.5d (A5); A5 is sub-90% exposure on 41% of days (E1 4.9%) — rule exits hold idle cash between exit and refill, and dropouts linger. Exposure-adjusted A5 (+270%) is still ~194pp below E1. Caveat: the ~229pp gap is **2023-dominated** (direction robust, magnitude fragile: ~8–10pp ex-2023), and the only rule-level value found is hard_stop's **conditional tail protection** (+20.6% when the stopped name keeps falling) — real but mean-zero overall. Confidence: direction HIGH, magnitude LOW.

**Q6 (structural): where does the value actually come from?**
Robust, directional: **refresh into the current top-5 beats holding the day-1 portfolio** (any exit mechanism > A0), and E1 quantifies the refresh upside: **pure refresh into the current top-5 beats the full 4-rule policy by +229pp** (2023-dominated; ~8–10pp ex-2023). The value is **entry alpha through slot turnover**. Exit-rule differences (which rule, when) show **no statistically clear evidence** of value; the one exception is hard_stop's conditional tail-protection, which is mean-zero overall. A5 catches megawinners later than E1 on the same names. The only defensible production takeaway: **refresh more into the current top-5; treat exit-rule tuning as net-negative until E3 shows otherwise.**

## 7. Next-round experiments (structural; ≤3; no threshold tuning)

- **E1 (DONE, 2026-08-17):** Pure rank-refresh baseline — sell current top-5 dropouts on rebalance, equal-weight 1/5 refill, retained positions hold-drift, all rules disabled. **One variant, no parameter search.** Result: +464% vs A5 +235% (2023-dominated magnitude, direction robust). Established the Q5 comparator: the rules impose staleness; only hard_stop shows conditional tail value. Gates all rule attribution.
- **E2 (concentration / regime stress):** Leave-one-out sensitivity. Recompute A0–A5 with each run's single biggest winner removed (603256.SH / 300548.SZ / 300570.SZ), plus per-year contribution. Tests whether any "rule effect" survives removing one name; directly checks the 98%-of-gap-in-5-stocks risk.
- **E3 (rule-on-common-skeleton):** Hold E1's refresh rate constant and overlay each exit rule (hard_stop / score_delta / stale) on top of rank-refresh. Answers "given identical refresh, does the exit rule matter at all?" — replaces the unidentifiable A1–A4 "vs A0" comparisons; E1 supplies the control. Also the natural place to test whether the 2023-dominated gap persists once refresh rate is matched.

Artifacts: run dirs `SysQ-execution-ledger/data/research/ablation/execution_policy/{A0_none..A5_all,E1_rank_exit}` (metrics.json, daily_summary.csv, executions.csv, predictions), run_spec.json, run_manifest.json, /tmp/ablation_analysis.json, /tmp/ablation_layer4.json, /tmp/ablation_episodes/*.json, /tmp/e1_comparison.json, and the rank_weight_top5 canonical backtest under `SysQ/data/research/backtests/rank_weight_top5_*`. E1/A5 backtest_ids: `f08dc9cf` / `3d695c20`.
