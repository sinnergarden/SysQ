# A0–A5 Execution-Policy Ablation — Final Report

Date: 2026-08-16
Signal: `financial_rc_60d_180d_50_50__daily_zscore` / run `blend__007a93600f45de00`
Window: 2021-01-04 → 2026-07-31, Top5, weekly rebalance, equal-weight entry + hold drift, rank_exit **disabled**
Baseline A0 = hold day-1 top-5 forever (no exits). A5 = full 4-rule posterior (canonical reproduce: ret +234.9% vs run2 +235%, same exit counts 129/228/17/6).

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

## 3. Differential-Return Results (Layer 4)

All swap edges are **statistically indistinguishable from zero** (paired Wilcoxon / sign test p = 0.16–1.00; n = 26–265). Report as directional point estimates only.

**hard_stop** (A5 n=129; A1-pure n=30)
- 94% (121/129) fire on **score-OK** stocks — price fell, long-term score did NOT deteriorate. ✓
- Post-exit: 59% recover >+5% / 60d, only 27% continue falling; old next60 mean +16.5% (median +9.1%).
- swap_edge60: A5 mean +2.3% / median −1.4%; A1-pure median +4.4%. **But 16% (21/129) of "replacements" are re-buys of the same stopped stock — exclude them → median −4.8%.**
- Realized PnL on stops: always negative (mean −12%); 17 events (exit ≥ 2026-05-22) right-censored out of 60d stats.
- A1's portfolio return is **not** from the stops: realized PnL −4.2M, the +160% is one **open** position (300570.SZ ≈ +11.6M, ~72% of the portfolio gain).

**score_delta** (A5 n=228; A2-pure n=273)
- 64% of exits are at profit (>+2%); buckets profit/flat/loss → old next60 +19.7/+14.3/+9.8%.
- Sold stocks recover; replacement swap60 mean −4.2/−4.0/+0.9%; **all-runs mean −3.3% but median +1.6% (negative skew, not negative typical case).**
- A2-pure has the **worst MaxDD of any run (−60.1%, below A0)** and highest single-rule turnover (1.14B).
- → NOT "real expectation deterioration identification"; it flags oversold mean-reversion bounces. Churn value lives in redeployment, not the exit signal.

**winner_trailing** (A5 n=17; A3-pure n=26; combined 43)
- Realized median +15.2% vs MFE median +36.8% → ~22–25pp giveback by construction.
- Combined old next60 median −3.0% (pos 43%) → sold winners did **not** continue rising; the "lost right tail" is largely a strawman.
- swap_edge60 mean −6.3% / median +0.9% (insignificant).
- A3 locks in **+20.0M realized** (0 losing symbols — least concentrated) but total return lowest (+97.9%): 25% cash drag + open losers held forever.
- n < 30 → **insufficient sample** for the A5-specific call.

**stale_replacement** (A4-pure n=30)
- swap_edge60 mean +5.8% / median +1.4% — but mean is **3-outlier-driven** (top-3 swaps = 175% of the sum; drop them → ≈ −4.8%); median +1.4% insignificant (16/30 positive, p=0.72).
- Median replacement (+14.5%) **underperforms** holding the sold stock (+16.3%). "Opportunity-cost exit" not established.
- A4's +188% is 62% one **open** stock (300548.SZ +11.65M); realized PnL −4.0M.
- In A5 only 6/380 fires → near-zero marginal value in the full policy.

## 4. Adversarial verification (4 independent lenses; 4 agents, 298k tokens)

- **Confirmed:** all headline numbers; identical signal/window/top_n across runs (no leakage, no config drift); A5 == run2 canonical.
- **The load-bearing correction:** A0 is a degenerate baseline (5 fills, 0 sells — "hold the day-1 top-5 forever"), so every A1–A4 "vs A0" delta conflates *refresh-into-current-top-5* with *exit-rule quality*. The slot-refresh alpha is real; **the exit-rule differences are sub-noise**.
- **Concentration is the dominant risk:** top-1 stock = 47% (A2/A5, 603256.SH), A1/A4 returns carried by single open multi-baggers with negative realized PnL; ~98% of the A0→A5 gap is ~5 stocks. Rule rankings flip yearly.
- **Every swap-edge ordering (stale>hard_stop>score_delta>trailing) is noise** (all p ≥ 0.16).

## 5. Answers to the 6 questions

**Q1 hard_stop: genuinely reduces risk & worth the return cost?**
NO on risk: MaxDD only −3pp vs A0, annualized vol UP (41.9→49.3%). It fires 94% on score-OK names (price fell, score fine), 59% recover. Realized stops are pure losses. It is **not** a loss-cutter that predicts further falls — it is a capital-refresh trigger whose portfolio value is unidentifiable without a rank-rebalance control (E1). Confidence: risk-facts HIGH, refresh-attribution LOW.

**Q2 score_delta: valid exit information?**
NO. 64% of exits sell at profit/flat; sold stocks recover +9.8–19.7%/60d; replacement swap is negative-skewed; A2-pure has the worst MaxDD. It flags oversold bounces, not deterioration. Confidence: "not deterioration" MODERATE (no time-matched control); "harmful" LOW.

**Q3 winner_trailing: protect winners or truncate the right tail?**
Neither, cleanly: it mechanically converts MFE (+37%) to realized (+15%, ~23pp giveback) and improves MaxDD — but ~74% of that MaxDD gain is cash drag. Sold winners did NOT continue (next60 median −3.0%), so "truncated tail" is a strawman; n=17–43 → **insufficient sample**. It is the only rule that locks in realized profit (+20M). Confidence: LOW (underpowered).

**Q4 stale_replacement: valuable?**
Standalone: best single-rule CAGR (+21.8%) at 0.09B turnover, but its swap edge is 3-outlier-driven and median replacement underperforms holding the sold stock → "valuable" UNSUPPORTED. In A5: only 6 fires → negligible. Confidence: LOW.

**Q5 where does the posterior policy's loss come from relative to pure score rebalance?**
Cannot be answered from A0–A5: the comparator (pure rank-rebalance) was not run. What is clear: the loss/cost center is turnover (2.23B, fees 1.78M ≈ 17.8% of capital) and negative-skew churn from score_delta/trailing; A4 achieves 80% of A5's return with 4% of turnover but worse Calmar. A5's edge over A4 is net-positive after fees (+47pp gross, ~+30pp net) — churn is low-efficiency, **not** a loss center. The refresh-lag component (holding top5-dropouts) is unquantified → E1.

**Q6 (structural): where does the value actually come from?**
Robust, directional: **refresh into the current top-5 beats holding the day-1 portfolio** (any exit mechanism > A0). The value is entry alpha through slot turnover; exit-rule differences (which rule, when) are within noise and dominated by which 1–2 megawinners each run happened to catch. The only defensible production takeaway: refresh more into current top-5; treat exit-rule tuning as sub-noise until E1–E3.

## 6. Next-round experiments (structural; ≤3; no threshold tuning)

- **E1 (PREREQUISITE — must run first):** Pure rank-rebalance baseline. Sell a held name when it drops out of the current top-5; refill on rebalance. Variants (3): (a) full dropout, (b) bottom-2-only dropout, (c) hold top-3 + refresh bottom-2. Isolates "refresh-into-current-top-5" and supplies the Q5 comparator. Gates all rule attribution.
- **E2 (concentration / regime stress):** Leave-one-out sensitivity. Recompute A0–A5 with each run's single biggest winner removed (603256.SH / 300548.SZ / 300570.SZ), plus per-year contribution. Tests whether any "rule effect" survives removing one name; directly checks the 98%-of-gap-in-5-stocks risk.
- **E3 (rule-on-common-skeleton):** Hold E1's refresh rate constant and overlay each exit rule (hard_stop / score_delta / stale) on top of rank-rebalance. Answers "given identical refresh, does the exit rule matter at all?" — replaces the unidentifiable A1–A4 "vs A0" comparisons.

Artifacts: run dirs `SysQ-execution-ledger/data/research/ablation/execution_policy/{A0_none..A5_all}` (metrics.json, daily_summary.csv, executions.csv, predictions), run_spec.json, run_manifest.json, /tmp/ablation_analysis.json, /tmp/ablation_layer4.json, /tmp/ablation_episodes/*.json.
