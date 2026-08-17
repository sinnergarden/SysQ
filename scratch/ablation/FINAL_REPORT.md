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

Yearly return (P0.1 convention, 2021 = ye/initial_capital − 1, 2022+ = ye/prev-ye − 1): A0 +46.2/−29.2/−17.2/+48.0/−2.5/−19.9 · A1 +32.6/−33.6/+19.7/+52.5/+73.6/−6.9 · A2 −26.2/−21.4/+45.3/+45.7/+52.4/+17.2 · A3 +54.1/−17.1/+4.3/+15.6/+57.7/−18.5 · A4 −6.3/+10.8/+32.7/+37.2/+86.4/−18.4 · A5 −16.2/−10.8/+49.0/+52.6/+72.4/+14.3 (2021–2026).

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

SwapEdge is now measured with a **common start at the replacement's entry date** for both old and new, over the same +20/+60 market-calendar horizon; the exit→entry cash gap is reported separately; forward returns are **null (never a stale prior close)** when the symbol has no close on the exact reference or horizon-end date. All swap edges show **no statistically clear evidence of a nonzero effect** (paired Wilcoxon / sign test p = 0.16–1.00; n = 26–265). Report as directional point estimates only; do not read them as "proven noise". Strict-close null cost: 23/380 events (6.1%) have a null old-return, concentrated in hard_stop (17/129 = 13.2%) — suspension/delist/beyond-calendar cases are dropped, not stitched with stale closes.

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

Yearly return (P0.1 convention): E1 −3.7/−20.9/+143.5/+55.6/+81.4/+7.7 · RW −12.3/−16.2/+189.1/+59.8/+75.9/−0.5 · A5 −16.2/−10.8/+49.0/+52.6/+72.4/+14.3 (2021–2026).

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
  +12.5/−10.1/**+94.5**/+3.0/+9.0/−6.6pp (2021–2026, P0.1 convention). Zeroing
  2023 in both arms collapses the gap to ~7pp (≈ 0). **Direction (pure refresh >
  4-rule policy) is robust; the magnitude is not a stable structural finding.**
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

---

## 8. E1 Alpha-Stability Diagnostics (frozen baseline, 2026-08-17)

Scope: **本轮只做 diagnostic，不实现 gate。** E1 = pure score refresh（`rank_exit`）
冻结，不修改策略/参数/模型/feature/label；不研究 Top10/20；不做阈值 grid search；
不继续 E2/E3；不再优化 hard_stop。研究目标已从 execution-rule optimization 转为：
**E1 的收益是否存在稳定 alpha，以及什么情况下模型没有 edge、应该降低仓位。**

### 8.1 P0 修正（两个分析口径）

- **P0.1 yearly 口径**：2021 = 年末NAV/初始资金−1；2022+ = 年末/上年末−1（不是
  年内首日锚）。修正后 E1 −3.7/−20.9/+143.5/+55.6/+81.4/+7.7；A5
  −16.2/−10.8/+49.0/+52.6/+72.4/+14.3；RW −12.3/−16.2/+189.1/+59.8/+75.9/−0.5；
  A0–A4 见 §1。旧口径把 2022 熊市损失低估约 5pp（E1 −16→−20.9；A5 −5→−10.8）。
- **P0.2 Layer4 multi-swap**：同日多 exit/entry 不再做任意 FIFO 配对当作精确因果，
  改按 day-level 等权 basket 输出 `old_basket_return_20/60`、`new_basket_return_20/60`、
  `basket_swap_edge_20/60`，并加 `multi_exit_day` / `multi_entry_day` / `pairing_ambiguous`
  标志。E1 的 day-basket 换仓 edge：20d 中位 **+0.35%**（pos 51.7%）、60d 中位
  **+0.42%**（pos 51.0%）——按日换仓的增量价值边际，与 §4 结论一致（refresh 价值在
  slot 周转，不在单笔换仓的定向因果）。

### 8.2 六条 track 汇总

| # | track | 核心发现 |
|---|---|---|
| 1 | Alpha/Beta 归因 (vs CSI800/300/universe-EW) | 2022 亏损≈beta（residual +7.6% 正 alpha）；2021 相对机会集大幅 miss（universe EW +21.4%，residual −24.5%）；2023–25 大额正 residual（+166/+35/+42%） |
| 2 | Active-return 稳定性 | 60/120/250d 滚动超额为正占比 73/84/85%；中位 +7.5/+19.8/+49.7%；但有 401d 连续 active DD、最长连续跑输 72–112 个窗口 |
| 3 | Signal Edge by Year (275 weekly snapshots) | RankIC60 每年正（+0.017/+0.077/+0.121/+0.069/+0.058）；Top5 60d 超额每年正（+1.6/+5.8/+4.7/+3.4/+14.5%）；20d edge 弱/2021 负 |
| 4 | Model Confidence | 反直觉：低信心→更高 60d 超额（top5_min LOW +9.8%/pos90% vs HIGH +2.9%/pos62%）；"低信心=无edge"方向性拒绝 |
| 5 | Market Regime 2×2 | edge 与 regime 无关（四格超额全正 +5.5~+8.9%）；风险与 regime 有关（down+bad-breadth 60d MaxDD −18.2% 最差） |
| 6 | Audit 2023 | 少数赢家驱动：Top1=61% PnL、Top5=100% 净利润、胜率 53%；2023 全年 +143.5% 中 2 月单月 +127.8% |

### 8.3 五个问题的回答

**Q1 — E1 跨年是否存在稳定 alpha？**
**是，但结构是"温和稳定的信号层"而非"每年复现的巨额收益"。** 信号层面 edge 每年为正：
RankIC60 每年 +0.017~+0.121；Top5 60d 超额中位每年 +1.6%~+14.5%；滚动 60/120/250d
超额为正占比 73/84/85%。但三个限定：① edge 只在 **label-horizon（60/180d）** 出现，
20d 层面弱（2021 中位 −2.1%）——是模型 horizon 的反映，不是任意周期的 alpha；②
头部年份的巨额收益来自极少数股票（2023 Top5=100% PnL），不是每年重复的宽基 alpha；
③ 相对指数 CSI800 的正 alpha ≠ 相对机会集的 alpha——2021 相对"评分宇宙等权"残差
−24.5%（那一年评分宇宙小盘普涨，E1 只拿 Top5 错过宽度）。稳定性评级：**方向稳定、
幅度分布广**（q10 为负、有 401d 连续 active DD）。

**Q2 — 2021/22 亏损是 beta 拖累还是 alpha 也失效？**
**2022 纯 beta；2021 双因素（beta 小拖累 + 相对机会集真实 miss）。** 2022：CSI800
−21.3%、E1 −20.9%，残差 **+7.6%**（正 alpha）——亏损 ≈ 市场下跌，选股仍贡献正残差。
2021：市场 −0.8%、E1 −3.7%，残差 vs 指数仅 −2.7%，但 vs 机会集残差 **−24.5%**；
这一年 20d RankIC 为负、Top5 超额 20d 中位 −2.1%——是唯一一年信号层 edge 真实偏弱。

**Q3 — 2023 超额是宽基还是少数赢家？**
**少数赢家，决定性。** 2023 共 117 笔退出、净 PnL +10.4M；**Top1（302132.SZ 航空，
75d +370.5%，2022-11 建仓）贡献 +6.34M = 61%**；Top5 = 100% 净利润；胜率仅 53%
（62/117）；Top10% 交易 = 125% 净利，其余 90% 交易合计 −25%。NAV 层面全年 +143.5%
中 **2 月单月 +127.8%**——恰好是那只票的整段行情。行业上航空 n=1 占 61%。与 Track 3
"2023 RankIC 最高 +0.121"并不矛盾：**信号甄别强，但组合收益由单一右尾主导**。
警示：2023 的 +143.5% 是一次集中式尾部兑现，不可外推为稳定 alpha。

**Q4 — 什么信息能提前刻画模型 edge？**
**没有任何现有指标能事前区分"有/无 edge"；能事前度量的是风险环境，不是 alpha。**
- *分数信心（Track 4）——不能，且方向反直觉。* 低信心组未来 60d 超额反而更高
  （`top5_min_score` LOW +9.8%/pos90% vs HIGH +2.9%/pos62%；`cross_section_score_std`
  LOW +15.8%/pos86% vs HIGH +2.0%）。横截面离散度逐年几乎不变（0.873–0.899），
  "信心"主要是时间/regime 代理，不是模型对自身未来 edge 的校准。"低信心→无 edge→
  降仓"被**方向性拒绝**（逐年中位数 split：`top5_min_score` 4/4 年反转、
  `score_std` 3/4 年、`top5_mean` 3/4 年；2021 全部落在 HIGH、无可比 LOW）。
- *市场 regime（Track 5）——不能预测 edge，能预测风险。* 2×2 四格 60d 超额全正
  （+5.5%~+8.9%），edge 与 regime 无关；但 down+bad-breadth 格 60d MaxDD **−18.2%**
  最差、up+good-breadth −12.8% 最好——**风险随 regime 变化**。
- *组合——* 唯一可用的信息是 **horizon 对齐**：edge 只在 60/180d 出现、20d 没有，
  支持"持有期应对齐 label horizon"，不支持更快周期的 alpha 假设。

**Q5 — 证据是否足够支撑 exposure-gate 实验？最多两个假设。**
**足够，但证据方向与初始直觉相反**：足以证明**不该做 edge-timing gate**（Track 4/5
均未发现任何"无 edge"条件，切仓会砍掉好时期）；足以证明**该做 regime-risk gate**
（风险随 regime 变化，且 2023 证明实现收益依赖极右尾）。两个假设，**不调参**：

- **H1 — regime risk gate（推荐）**：当 CSI800 trailing 120d ≤ 0 且 20d breadth ≤
  中位数时，目标仓位按固定比例降档（持有现金/缩减持仓）。依据：该格是 60d MaxDD
  最差格（−18.2%）而超额仍正（+5.5%）——预期显著降 MaxDD，收益牺牲有限；接受的是
  "牺牲相对跑赢"而非"绝对收益"（2021/2022 弱市恰落此格）。
- **H2 — 尾部集中度 cap（可选，证据较弱）**：限制单票/单行业对组合收益的最大贡献
  （如单票仓位上限或同行业暴露上限）。依据：2023 Top1=61% PnL、组合收益极右尾驱动，
  限仓削平尾部依赖、降 active DD；但 2023 正是靠这右尾才 +143.5%，故 H2 证据强度
  低于 H1，实验设计需先明确"保护什么"（削尾部 vs 留右尾）。

两者都不实现 gate 参数；本轮交付到此为止。

### 8.4 口径与可信度备注

- **重叠窗口**：Track 3/4 的 60/180d 前向收益窗口高度重叠，不做 iid p-value 结论；
  只看中位数/分位/年度一致性。
- **2022 年 180d Top5 +61.5%**：该 60 日前的右尾是 2022→2023 反弹跨 horizon 的
  产物，报告时已标注，不作为"熊市仍有 alpha"的证据。
- **Track 6 归因口径**：按"退出年份"归因 realized PnL，不含年末仍持仓的未实现 PnL；
  E1 持有中位 6d、换手高，误差可控。NAV 月度归因（2023-02 +127.8%）为权威视角，与
  episode 归因互相印证。
- **Track 4 的 bucket 时间聚集**：HIGH 多落在 2021–22、LOW 多落在 2024–25；逐年
  中位数 split 在 10/12 个可用"年×指标"格子里反转（`top5_min_score` 4/4、`score_std`
  3/4、`top5_mean` 3/4；2021 全 HIGH 不可比），削弱了纯时间混淆解释，但不消除
  （60d 重叠→有效 n 小）。

Code: `scratch/ablation/diag_common.py` + `diag_track1..6.py`（从 MAIN repo cwd 运行）；
输出 `/tmp/diag_track1..6.json`。P0 修正代码在 `analyze_layers.py` / `analyze_layer4.py`。

---

## 9. Refresh-Cadence + Structural Experiments (stable-alpha skeleton search, 2026-08-17)

Scope: 在 E1（pure score-refresh, rank_exit）骨架上找 stable-alpha production skeleton。
引擎新增两个结构能力（本 PR #242，分支 `research/backtest-execution-ledger`）：
**N-trading-day cadence**（`"5d"/"20d"/"60d"`，engine 级）+ **exposure gate**（PIT schedule
→ 门控日按比例降仓到 `exposure_gate_scale`）。不做 TopN/权重/参数 grid search。

### 9.1 Refresh cadence（E1 blend，weekly / 5d / 20d / 60d）

| metric | weekly | 5d | 20d | **60d** |
|---|---|---|---|---|
| Total return | +464% | +246% | +450% | **+580%** |
| CAGR | 36.4% | 24.9% | 35.8% | **41.1%** |
| MaxDD | −53.3% | −44.9% | −46.4% | **−38.5%** |
| Active vs CSI800 | +4.71 | +2.52 | +4.56 | **+5.87** |
| Turnover / orders | 6.14B / 1321 | 4.89B / 1243 | 2.31B / 471 | **1.39B / 194** |

Yearly (P0.1): 60d **+54.9/−15.8/+149.5/+50.8/+44.3/−4.0**（2021–2026，60d 每项均不差）。

**结论：60d 是"更高收益 + 更低风险 + 更低换手"的三重胜出**，rare。5d 换手是 60d 的 6.4 倍却
收益只有一半。60d 23 次 rebalance，间隔全部精确 60 交易日（engine 回归测试锁定）。

### 9.2 A. Horizon decomposition（60d cadence，S60 vs S180 vs Blend 50/50）

| metric | S60 (60d label) | **S180 (180d label)** | Blend 50/50 |
|---|---|---|---|
| Total return | +718% | **+1103%** | +580% |
| CAGR | 45.8% | **56.3%** | 41.1% |
| MaxDD | **−34.8%** | −42.5% | −38.5% |
| Active vs CSI800 | +7.24 | **+11.10** | +5.87 |
| Turnover / orders | 1.57B / 211 | 1.48B / 173 | 1.39B / 194 |
| RankIC@label | +0.060 | **+0.093** | +0.073@60 / +0.088@180 |
| Top5 fwd excess@label | +0.134 | **+0.394** | +0.091@60 |

Yearly (P0.1): S60 +48.1/+4.6/+155.7/+22.6/+61.3/+4.4 · **S180 +85.2/+5.8/+79.2/+32.1/+104.2/+27.0** ·
Blend +54.9/−15.8/+149.5/+50.8/+44.3/−4.0（2021–2026）。

- **S180 是 A 组胜者**：每年全正（含 2022 熊市 +5.8%），CAGR 56.3%，RankIC@180 最高 +0.093。
- **50/50 z-score blend 被两个纯 horizon 同时支配（dominated）**：CAGR 最低且 2022 为负
  （−15.8%，而 S60/S180 均正）。原因是 z-score 平均改变了 top-5 选择——blend 的 top-5
  既不是 S60 的也不是 S180 的，2022 选出更差组合。naive 平均稀释是死路。
- **S180 的收益高度右尾集中：002281.SZ（光模块）2025-09 以 64.36 建仓、2026-06 以 241.75
  平仓（+275%，持 9 个月），单票贡献 realized +47.6M = 全组合 +110.3M NAV 增益的 43.1%**。
  avg-cost 重建验证（`compare_structure.py`，recon gap ≈0.2M）top1 集中度 43.1% / top5 84.4%
  （blend top1 25.6% / top5 85.4%）。→ S180 的 CAGR 头部脆弱；**去掉 002281 后总回报
  +1103% → +627.5%**（仍 > blend +580%，方向不变但幅度大幅缩水）。

### 9.3 B. Exposure gate（gate_scale=0.5，PIT schedule）

schedule 定义（复用现有 coarse regime，不调 threshold；PR #242 已修正两个 correctness 问题：
breadth median 改 **strictly-prior**（expanding median over positions 20..pos−1，无 lookahead）；
S180 的 model-health 用 **S180 自己的 cohorts + 180d horizon**，不再复用 blend 派生 schedule）：
- **market_risk_bad** = CSI800 trailing 120d ≤ 0 AND breadth_20d ≤ **strictly-prior** median。
  门控日 401/1351（29.7%），2022/2023/2024 集中。（修正前后仅 399→401 天，收益差 <1pp——
  修正原则正确但实证影响可忽略，全样本 median 本就接近 strictly-prior median。）
- **model_health_bad（blend）** = 60d cadence cohorts 的 realized Top5-60d excess trailing 均值
  ≤ 0（严格 PIT：cohort 完全兑现后才可用，窗口 12 个、min 4）。门控日 300/1351（22.2%），
  几乎全在 2022（+2023 初），2024+ 恢复后全关。
- **model_health_bad（S180）** = S180 60d cohorts 的 realized Top5-**180d** excess trailing 均值
  ≤ 0。**门控日 0/1351（0.0%）**——S180 自己的 model-health 从未恶化：23 个 realized cohort 的
  180d top5-excess 仅少数为负（2021×2、2021-12、2023-06..2024-03 各 −0.05..−0.11），trailing-12
  均值始终 > 0（被 +0.37/+0.68/+0.99/+0.36/+1.69/+0.82/+0.77 主导）。

**B 组（blend baseline，G0 = E1_refresh_60d）：**（修正后重跑）

| metric | G0 | G1 market_risk | G2 model_health | G3 either |
|---|---|---|---|---|
| Total return | 5.80× | 2.63× | 3.46× | 2.48× |
| CAGR | 41.1% | 26.0% | 30.8% | 25.1% |
| MaxDD | −38.5% | **−27.5%** | −37.6% | **−27.5%** |
| Active | +5.87 | +2.70 | +3.53 | +2.55 |
| Turnover / orders | 1.39B / 194 | 0.81B / 513 | 0.91B / 388 | 0.75B / 610 |

Yearly (P0.1): G0 +54.9/−15.8/+149.5/+50.8/+44.3/−4.0 · G1 +39.0/−12.4/+74.5/+26.5/+38.8/−2.7 ·
G2 +52.7/−7.5/+51.1/+50.8/+44.3/−4.0 · G3 +37.0/−7.5/+60.8/+26.5/+38.8/−2.7。

**B 组（S180 baseline）——修正后：G1 复用同一 market-risk schedule（signal-common）；
G2 用 S180-specific model-health schedule（本 PR 修复 #3）。**

| metric | A_S180 | G1_S180 | G2_S180 |
|---|---|---|---|
| Total return | 11.03× | 3.93× | 11.03× |
| CAGR | 56.3% | 33.2% | 56.3% |
| MaxDD | −42.5% | **−28.2%** | −42.5% |
| Active | +11.10 | +4.00 | +11.10 |
| Turnover / orders | 1.48B / 173 | 0.74B / 511 | 1.48B / 173 |

Yearly (P0.1): A_S180 +85.2/+5.8/+79.2/+32.1/+104.2/+27.0 · G1_S180 +48.6/+2.9/+36.2/+13.8/+82.2/+14.1 ·
**G2_S180 与 baseline 逐日相同**（+85.2/+5.8/+79.2/+32.1/+104.2/+27.0）。

**结论：gate 仍然全部失败（未达目标），但 S180 的 G2 结论被修正。**

- **market-risk gate（G1）**：MaxDD 显著下降（blend −38.5→−27.5，S180 −42.5→−28.2），
  但**收益损失不成比例**——CAGR 砍 37%（blend）~41%（S180），active alpha 腰斩以上。
  2023 right-tail 只保留 ~50%（blend 2023 +149.5→+74.5；S180 2023 +79.2→+36.2）。
  机制不变：模型在**所有** regime 都有 edge（§8 Track 5 四格 60d 超额全正），regime 降仓
  是纯成本；de-risk 日级快、re-lever 只在 rebalance 日（不对称）。
- **model-health gate（G2，blend）**：仍只 gate 2022（+2023 初），MaxDD 仍几乎不降（−0.9pp，
  最深回撤 2021-07→10 时 model-health 未够 4 个 realized cohort、来不及开），2023 right-tail
  仍被砍（+149.5→+51.1）。结论不变。
- **model-health gate（G2，S180）——修正后为 no-op**：S180-specific schedule 门控 0 天，
  G2_S180 与 baseline 完全相同。**旧报告"G2_S180 改善 2022 / 砍 2023 right-tail"是
  blend-derived schedule 的错误读out**——S180 的模型在 2022/2023 从未恶化（其 2022 本来就
  +5.8% 正收益），blend 的 2022 cohort 表现不佳与 S180 无关。修复 #3 删除的是幻影 de-risk。
- **G3（either）** ≈ G1 的表现（market-risk 主导）。

### 9.4 Stable-alpha skeleton 判断

- **胜出骨架 = S180@60d pure rank-refresh**（rank_exit，4 规则全禁用，Top5 等权 entry +
  hold-drift）。唯一每年全正、CAGR 56.3%、RankIC@180 最高、MaxDD −42.5% 且无杠杆/负现金。
- **但该骨架两个结构风险**：① right-tail 脆弱——002281 单票 = 43% NAV 增益；② MaxDD
  −42.5% 主要落在 2023-04→2024-02（180d momentum 在 2024-02 量化/小微盘 crash 中回撤），
  **regime gate 无法在不破坏 alpha 的前提下降低它**（edge 在所有 regime 存在）。
- **决策**：**不采用 exposure gate**（G0–G3 全否；S180 的 G2 修正后为 no-op，同样不采用）。
  S180 替换 blend 仍是唯一被验证的骨架改进（+580%→+1103%，且 2022 由负转正）。
  下一步（若继续）：① 验证 S180 的 002281 是否可归因于 180d label 的稳定选股（而非单票）；
  ② 尾部集中度 cap 作为 MaxDD 的替代手段（证据 §8 Q5 较弱，需先明确"削尾部 vs 留右尾"）。
  ③ ~~label-horizon 对齐的 180d cadence~~ **已被 §9.5 实验证伪**（S180_180d CAGR 32.1%，
  MaxDD −44.5%，2022 −18.4%，明显差于 60d）。

### 9.5 Structural experiments：S180 cadence robustness + rank-hysteresis band（2026-08-17）

引擎新增两个结构能力（本 PR #242）：**rebalance phase offset**（`<n>d` cadence 网格整体
相位平移，offset=0 = 历史网格，hash 只在非 0 时纳入）与 **rank-hysteresis band**
（`rank_exit + rank_exit_hold_top`：每周评估，当前 Top5 入、rank ≤ 10 保持、rank > 10 退出、
从当前 Top5 补到恰好 5 仓、hold drift、四个 exit 规则全禁用）。

**Q1: S180@60d 是否对 rebalance phase 稳健？——NO。**（同 60d cadence，只平移网格相位）

| metric | 60d off0 | 60d off20 | 60d off40 | 20d | 180d |
|---|---|---|---|---|---|
| Total return | +1103% | **+1691%** | +270% | +853% | +271% |
| CAGR | 56.3% | **69.5%** | 32.0% | 52.6% | 32.1% |
| MaxDD | −42.5% | −41.2% | −42.2% | −43.5% | −44.5% |
| Active | +11.10 | +17.98 | +3.76 | +9.60 | +3.78 |
| Turnover / orders | 1.48B / 173 | 2.49B / 189 | 0.57B / 171 | 3.58B / 442 | 0.26B / 73 |
| 每年全正 | **yes** | no (−2.3/−0.8) | yes | no (−11.5) | no (−18.4) |
| 002281 占 NAV 增益 | 43.1% | 20.8% | 44.6% | 14.4%* | 21.8% |

Yearly (P0.1)：off0 +85.2/+5.8/+79.2/+32.1/+104.2/+27.0 · off20 +123.4/−2.3/+103.8/+29.3/+231.6/−0.8 ·
off40 +3.2/+0.8/+17.5/+25.5/+130.7/+32.7 · 20d +98.5/−11.5/+73.8/+44.1/+125.3/+6.2 ·
180d +36.7/−18.4/+6.1/+46.2/+141.0/+13.0。（*20d 的 top1 是 302132.SZ）

- **CAGR 跨相位摆动 32.0%→69.5%（2.17×）**：只平移网格 20/40 个交易日，收益就翻倍或腰斩。
  相位撞上 2025-09 002281 的入场点决定是否吃到这只 10 倍股的主升段——off20 在 2025-08/09
  恰好在低位轮入，off40 的网格错过并晚 40 天才换手。
- **"每年全正（含 2022）"是 phase-lucky 属性**：off20 的 2022 −2.3%、2026 −0.8% 即告破。
  60d cadence 本身不带来这个性质，是 offset-0 网格的运气。
- 20d / 180d 均明显差于 60d（CAGR 52.6%/32.1% vs 56.3%，且 2022 转负）。180d cadence 证伪
  label-horizon 对齐假设。
- **→ 固定 cadence（无论相位）不是稳健结构**：单点胜出不可复制为"60d 更好"的普适结论。

**Q2: event-driven Top5-entry/Top10-hold 是否比固定 cadence 更稳定？——NO。**

| metric | A_S180_60d (fixed) | S180_band_weekly |
|---|---|---|
| Total return | **+1103%** | +119% |
| CAGR | **56.3%** | 23.2% |
| MaxDD | −42.5% | **−40.2%** |
| Active | **+11.10** | +2.26 |
| Turnover / orders | **1.48B / 173** | 3.43B / 858 |
| 每年全正 | **yes** | no (−14.4 / −19.0) |

Yearly (P0.1)：fixed +85.2/+5.8/+79.2/+32.1/+104.2/+27.0 · band +2.4/−14.4/+98.9/+36.9/+65.2/−19.0。

- **band 换手 5×（858 vs 173 orders）却只赚 1/5 的收益**：每周评估 + 当前 Top5 补仓 =
  高频追 180d-momentum 的 weekly 噪声，top-5 每周大换，hysteresis band 只保住旧名、却用
  新名替换出局者，净效果是频繁轮动但选错时点。CAGR 23.2% vs 56.3%。
- MaxDD 仅改善 2.3pp（−42.5→−40.2），代价是收益腰斩以上——不符合"更稳定"的判据。
- **→ band 结构直接淘汰**（按用户指令：明显失败结构不追加细调）。

**综合判断**：S180@60d off0 仍是 12 个 run 里的 best，但其相位敏感性 + 单票集中度使其
**不具备生产级稳健性**。两个结构假说（phase 对齐 / 事件驱动 band）均被证伪。下一步只考虑
不依赖 rebalance 网格的结构：尾部集中度 cap、或 002281 式单票归因后重新验证 S180 骨架。

Code: `scratch/ablation/run_ablation.py`（S180 cadence/band/gate runs）、`build_gate_schedule.py`
（strictly-prior PIT schedules）、`compare_structure.py`（avg-cost PnL 重建 + 浓度 + excl-top1）、
`qsys/backtest/daily_kernel.py`（N-day cadence + phase offset）、`posterior_policy.py` +
`strategy_runner.py` + `scripts/research/backtest_from_signal.py`（band + offset 引擎）。
Engine 回归：`tests/backtest/test_rebalance_cadence.py`（offset + cadence + flags，13）+ 
`test_rank_band.py`（5），全量 `tests/backtest` 107 passed。
backtest_ids: A_S180_60d `78804e7a` · E1_refresh_60d `4dc5a6b6` · S180_20d `ba710797` ·
S180_60d_off20 `3bcbff40` · S180_60d_off40 `8af9677d` · S180_180d `e0806046` ·
S180_band_weekly `23055a59` · G1 `1f2e6d0d` · G2 `35c3c3a0` · G3 `b2f6b925` ·
G1_S180 `227c0f7e` · G2_S180 `31fe1781`。
artifacts: `execution_policy/{S180_20d,A_S180_60d,S180_60d_off20,S180_60d_off40,S180_180d,
S180_band_weekly,G1_market_risk,G2_model_health,G3_either,G1_S180_market_risk,
G2_S180_model_health}` + `gate_schedules/*.json`。

### 9.6 S180@20d phase robustness（2026-08-17）

把 20d cadence 网格相位平移 0/5/10/15 个交易日，其余**完全冻结**（S180 信号、E1 skeleton、
top-5、hold drift、dead rules、同一 1351 天窗口）。目的：判断 20d skeleton 是否对 phase 稳健
——**不是找最佳 offset**（不做 offset 调参）。offset 0 == §9.5 的 `S180_20d`。

**Q3: S180@20d 是否对 rebalance phase 稳健？——NO。**

| metric | 20d off0 | 20d off5 | 20d off10 | 20d off15 |
|---|---|---|---|---|
| Total return | **+953%** | +401% | +372% | +116% |
| CAGR | **52.6%** | 33.5% | 32.1% | 14.8% |
| MaxDD | −43.5% | **−40.0%** | −45.8% | −50.0% |
| Active vs CSI800 | **+9.60** | +4.07 | +3.79 | +1.23 |
| Turnover / orders | 3.58B / 442 | 2.15B / 452 | 2.62B / 444 | 1.31B / 425 |
| 每年全正 | no | no | no | no |
| Top1 share | 302132.SZ 14.4% | 302132.SZ 28.7% | 302132.SZ 38.0% | 300570.SZ 26.7% |
| Top5 share | 60.4% | 82.4% | 82.7% | 105.5% |
| excl-top1 | +816.1% | +285.4% | +230.8% | +85.0% |

Yearly (P0.1)：off0 +98.5/−11.5/+73.8/+44.1/+125.3/+6.2 · off5 +9.1/+3.5/+105.6/+21.0/+85.6/−4.0 ·
off10 +51.1/+9.9/+74.0/+31.8/+33.1/−6.9 · off15 −3.9/+36.4/+12.7/+21.1/+40.9/−14.4。

- **CAGR 跨相位摆动 14.8%→52.6%（3.55×）**：只把 20d 网格相位平移 5/10/15 个交易日（约 ±3 周），
  总收益 +116%→+953%。off0 是 phase-lucky 最大值——4 个相位里 3 个落在 +116%~+401%，只有 off0
  超过 +500%。相对摆幅比 60d 实验（2.17×）**还大**。
- **off0 的优势不是单票运气**：off0 的 top1 集中度反而是 4 相位里最低（14.4% vs 28.7%/38.0%/26.7%），
  top5 也只有 60.4%（vs 82~83%）；剔除最大赢家后 excl-top1 仍 +816%（vs 下一名 +285%）。edge 是
  广谱的，却被 phase 完全翻盘。
- **相位平移不止损收益，还损风控**：MaxDD 从 −43.5% 恶化到 off15 的 −50.0%；集中度从 60.4% 升到
  82.7%，off15 的 top5 PnL 达 NAV 增益的 105.5%（5 只赢家赚回全部，其余 traded 名净亏）。相位越偏，
  edge 越坍缩进更少的名字。
- **每个相位都有亏损年**：off0 2022 −11.5%、off5 2026 −4.0%、off10 2026 −6.9%、off15 2021 −3.9%
  + 2026 −14.4%。相位不同，亏损的年份与幅度完全不同——20d 无任何相位提供"每年全正"。
- **与 60d 结论收敛**：两个独立 cadence（60d 3 相位、20d 4 相位）在 ±一个相位窗口内都出现多倍
  CAGR 摆动 → 相位敏感性是**固定 cadence skeleton 的结构属性**，不是某次实验的偶然。20d skeleton
  与 60d 一样不具备相位稳健性。两轮实验共同排除固定 cadence 路线（无论相位），剩余候选只剩
  不依赖 rebalance 网格的结构（尾部集中度 cap、单票归因后重验 S180 骨架）。

**机制定位（重要）：off0 == 模型 retrain 激活日，68/68 精确重合。** S180 信号管线
`financial_rc_180d_rolling_5y_to_202607_v3`（`rolling_windows.csv`）每 20 个交易日 retrain 一次
（68 个 window，各在 `predict_start` 启用新模型；每模型训练过去 504 交易日 ≈756 日历天；
train_end→predict_start 间隔 ~273 天 ≈ 180d label horizon + 缓冲，maturity-gate 合规无 lookahead）。
对照各 run 的 rebalance 日期落在 retrain 日的比例：

| run | rebalances | on-retrain-day |
|---|---|---|
| **S180_20d (off0)** | 68 | **68 (100%)** |
| S180_20d_off5 / off10 / off15 | 68/68/67 | **0 (0%)** |
| A_S180_60d · off20 · off40 | 23/23/22 | 23/23/22 (100%) |
| S180_180d | 8 | 8 (100%) |
| band_weekly | 285 | 23 (8%) |

- **off0 的优势是"rebalance 与 retrain 同步"这一机制**，不是任意相位运气：每次 off0 重排序都发生在
  新模型预测生效当天；off5/10/15 落在窗口中间（模型未刷新）即崩塌。20d 骨架的稳健锚点 = **retrain
  日历**——生产中可做成确定性规则：**rebalance 由 retrain 事件触发**（而非独立网格上的自由相位）。
- **两个保留**：(1) 循环性——retrain 网格本身从 2021-01-04（=回测窗口起点）起算，off0 同步是
  "相对于研究管线自己的 rolling 网格"，绝对相位仍由实验配置设定，非内禀日历；(2) 60d 三个 offset
  因 20\|60 全部 100% 落在 retrain 日、CAGR 仍摆 32→69.5% → retrain 对齐是**必要不充分**，残余
  摆动是"在哪些 retrain 日入场"的 entry-timing（002281 主升段），与模型新鲜度无关。

**Verification（对抗复核 workflow，3 路独立）**：offset 网格 firing 精确（off0/5/10/15 首轮 rebalance
位于交易日 idx 0/5/10/15，各 ~68 轮、间距 20）；avg-cost PnL 重建 recon_gap = 买入佣金精确吻合
（0.56%~1.70% of gain，≤1M 绝对）；top1/top5/excl-top1 与订单数全部复现（±1e-3 内）；off15
top5>100% 算术有效（72/141 名亏损对冲）；独立重算指标仅差参考值 2 位小数舍入 → **CONFIRMED_NOT_ROBUST**；
retrain 对齐由 `rolling_windows.csv` predict_start ↔ daily_summary is_rebalance 逐日核对（68/68）。
backtest_ids: S180_20d `ba710797`（=off0）· S180_20d_off5 `146e9661` · S180_20d_off10 `c038a0a2` ·
S180_20d_off15 `0c893584`。
artifacts: `execution_policy/{S180_20d,S180_20d_off5,S180_20d_off10,S180_20d_off15}`。
Code: `run_ablation.py`（D2 组 runs）、`compare_structure.py`（group D2：4 相位对比 + avg-cost 重建）。

### 9.7 S180 selection alpha: old-vs-new model at retrain day（2026-08-17）

研究问题（沿用上一轮定下的方向）：S180 的**选中 alpha 到底来自哪里**——是 retrain 日"新模型
新提拔的名字"（NEW_IN），还是"新旧模型一致的老名字"（BOTH）？是否应该改用**跨模型共识**
（consensus）来选？本轮冻结所有其他结构（20d off0 retrain-triggered、Top5、等权、hold drift、
dead rules），只允许改 retrain 日的打分口径。

**方法（Section A）**：对每个 retrain 边界 t（68 window → 67 个边界，w0000→w1340），把上一版模型
M_{t-1} 重新训练（seed 42 确定性）并在**同一份 t 日 PIT feature snapshot**（data_date == prev_td(t)
== 旧 window 最后 feature 日）上重新 inference，与已存储的新模型 score 配对 → 得到同一份特征下
score_old / score_new、rank_old / rank_new。唯一变化变量 = model version。重训可复现（per-day
Spearman median 0.99999），M_old 重建可信。

- **Score-cap 顶层退化（机制性发现）**：score_raw 在 ±3.0 winsorize。67 个 retrain 日里 **47 天
  （70%）超过 5 个名字顶在 cap** → 这些天 top-5 是 tiebreak 抽签，不是模型判别；只有 20 天（30%）
  "干净"（≤5 名顶在 cap）。
- **BOTH / NEW_IN / DROPPED 前瞻超额**（vs 同日 scored-universe EW，pp）：

| bucket | ex20 | ex60 | ex180 |
|---|---|---|---|
| both | +3.2 | +21.6 | +54.9 |
| new_in | +2.5 | +5.5 | +24.6 |
| dropped | +1.5 | +5.9 | +19.6 |

replacement_edge_H = mean(R_NEW_IN) − mean(R_DROPPED)：20d **+1.0pp** · 60d **−0.4pp** ·
180d **+5.0pp**。

- **干净日 vs 顶 cap 日（机制拆分）**：

| 场景 | 天数 | NEW_IN ex180 | DROPPED ex180 | edge 180d | NEW_IN>+50% | DROPPED>+50% |
|---|---|---|---|---|---|---|
| 干净 | 20 | +34.7 | +13.3 | **+21.4pp** | 29.1% | 20.0% |
| 顶 cap | 47 | +19.3 | +22.8 | **−3.5pp** | 16.0% | 15.1% |

retrain 只在模型真能区分 top-5 的那 ~30% 天创造选中 alpha（+21pp @180d）；70% 的天是抽签
（−3.5pp）。**选中 alpha 的来源是"新模型把名字顶进 cap/前 5"这个动作，且只在未被 cap 抹平的日子
有效。**

- **年度一致性（Section C）**：NEW_IN−DROPPED 180d edge = 2021 −5.1 · 2022 −1.2 · 2023 −4.9 ·
  2024 −18.2 · 2025 **+66.2** · 2026 n/a。5 个完整年份里 4 年为负、只有 2025 大幅为正 →
  **年际不一致，不稳健**。
- **模型版本稳定性（Section D）**：全市场 Spearman rho 0.91；Top5 Jaccard 0.34（med 0.43）；
  new5_from_old5 0.48、new5_from_old10 0.67、new5_from_old20 0.84；|rank_delta| 中位数 48.4/780。
  每轮 retrain 约 2.4/5 名字保留，但 top 区重排剧烈——churn 主要是 top 的排序噪声。
- **右尾归因（Section E，双向）**：
  - *前瞻收益*：BOTH 的 180d 右尾最肥（>+50% 29.1%、>+100% 20.9% vs NEW_IN 20.5/10.6 vs
    DROPPED 16.8/8.7），但**左尾也肥**（<−40% 4.5% vs 1.9%），180d 中位数 −0.3pp、正收益率仅 49%
    → BOTH 是 p90 +211pp 的高方差分布。
  - *已实现 PnL（first-entry bucket，S180_20d executions）*：top-40 PnL 贡献者里 **26 只（其 PnL
    的 72%）首次入场是 NEW_IN**，12 只（28%）是 BOTH。大赢家（302132 除外：001696、002281、
    000988、688220、300548、603119...）几乎都以 NEW_IN 首次进入、随后持续成为 BOTH。
    **对账**：赢家的 PnL 记在首次入场（NEW_IN 时点买入便宜），前瞻收益统计则把它们在后续窗口记为
    BOTH——两者不矛盾：NEW_IN 是"入场事件"，BOTH 是"持有状态"。

**Section F 反事实**（retrain-triggered、Top5、等权新入、hold drift、同成本；只改 retrain 日打分）：

| metric | C0 新模型 Top5 | C1 mean-rank consensus | C2 confirmed-first |
|---|---|---|---|
| Total return | +953% | +636% | **+953%** |
| CAGR | 52.6% | 43.1% | **52.6%** |
| MaxDD | −43.5% | −44.5% | **−43.5%** |
| Active vs CSI800 | +9.60 | +6.43 | **+9.60** |
| Turnover / orders | 3.58B / 442 | 2.60B / 406 | **3.58B / 442** |
| Top1 share | 302132 14.4% | 603256 31.2% | **302132 14.4%** |
| Top5 share | 60.4% | 81.9% | **60.4%** |
| excl-top1 | +816% | +438% | **+816%** |

Yearly：C0 **+98.5/−11.5/+73.8/+44.1/+125.3/+6.2** · C1 +38.5/+4.1/+85.4/+31.9/+78.8/+16.8 ·
C2 = C0（逐年逐项一致）。

- **C1（consensus）明显更差**：总收益 9.53x→6.36x（−33%）、MaxDD 没改善（−44.5% vs −43.5%）、
  右尾更集中（top1 31.2%、top5 81.9% vs 14.4/60.4%）。唯一改善是年度波动（std ~30 vs ~49，
  且 2022 由 −11.5% 转 +4.1%、无亏损年）——但这是用 1/3 收益换来的。
- **C1 为何差（机制）**：consensus = mean(pct_rank_old, pct_rank_new) 把高分给"两个模型都在中间
  名次靠前"的名字（如 2021-02-01 的 603920/600600：新模型只给 2.53/2.78，但旧模型 rank 高），
  系统性**牺牲新模型刚提拔的 NEW_IN**——而 Section E 显示大赢家恰恰是 NEW_IN 首次入场。共识把
  右尾源头丢掉了。
- **C2（confirmed-first）的构造陷阱（三层，已逐一修复）**：confirmed-first 想做的只是"组内重排
  （BOTH 优先）、集合与 C0 恒等"。但三版构造都踩坑：(1) 第一版用 `1e6*band + rank` 且 rank 取反
  （ascending=False）→ 非 BOTH 空位填成**新模型 rank 最差**的名字；(2) 第二版 `rank(ascending=True)`
  后仍因 rank Series 是**行整数索引**、`reindex(instrument)` 静默返回 NaN → 每个 override 日分数
  塌成 ~0，存储 parquet 退化为"top-5 保留原始分、其余全 0"；(3) 第三版 forced set 用对 base 信号
  的**朴素排序** head-5，而引擎在 70% 顶 cap 日的实际选择是**另一场 tiebreak 抽签**——与 C0 的
  真实持仓集并不相等。**修复**：forced set 直接从 C0 的 executions 推导（当日再平衡后持仓 ∪ 当日
  买单，含 2025-08-18 被 Limit-Up 拒掉的 603256），band 2.0/1.0 用 instrument 索引的 pct-rank
  打 tiebreak；写盘后对**存储 parquet**（回测真正读的产物）验证 top5 == forced set **67/67**。
- **C2 最终结果 = 结构性 no-op**：重跑后 C2 与 C0 **逐日持仓 67/67 天完全一致**、442 单、全部
  指标逐项相等（上表）。原因：等权 Top5 + rank_exit 引擎持的是 top-5 **集合**，不看组内排序；
  confirmed-first 只是组内重排，**没有任何机制改变持仓或 PnL**。任何"C2 ≠ C0"的数值（早前那版
  CAGR 35%/48%）都是构造 bug 的产物，不是策略差异。

**Q1–Q4 回答**：
- **Q1（retrain 是否创造 alpha，NEW_IN vs DROPPED）**：**微弱且不稳健**。180d edge 全样本 +5pp，
  但 4/5 年为负、只在 30% 干净日 +21pp；60d ≈0。retrain 的"换血"本身不是稳定的 alpha 来源。
- **Q2（BOTH 是否值得当共识依据）**：BOTH 的平均 180d 超额更高（+54.9 vs +24.6），是持久 alpha；
  但**把共识做成选股规则（C1）反而亏**，因为它把 NEW_IN 右尾挤掉了。新模型 Top5 本来就包含 BOTH
  （≈2.4/5），不需要单独的共识重排。
- **Q3（右尾赢家来自 BOTH 还是 NEW_IN）**：**已实现 PnL 层面来自 NEW_IN 首次入场**（top-40 贡献
  者 72% PnL）；前瞻条件层面 BOTH 右尾更肥。对账：赢家以 NEW_IN 便宜入场、随后持续为 BOTH。
- **Q4（C1/C2 能否"收益不显著丢失但更稳"）**：**不能**。C1 收益显著丢失（−33%）且 MaxDD/集中度
  更差，只换来年度平滑；C2 与 C0 逐日路径恒等（67/67 持仓、442 单、指标全同），confirmed-first
  与 trust-new-model 无差别。

**裁决：A. Trust-new-model。** 保留 retrain-triggered + 新模型 Top5（= C0）。共识（C1）被淘汰：
收益 −33% 换来的是年度平滑而非风控/右尾改善。confirmed-first（C2）集合恒等于 C0，不存在独立
价值。真正的机制短板不是选股结构而是 **score cap 顶层退化**（70% 天 top-5 是 tiebreak 抽签）——
那属于数据/打分管线问题，本轮冻结范围之外，留作下轮。

Verification：reinfer 可复现（w0000/w0660/w1320 Spearman ≥0.99999）；panel new5 == base top5
67/67（string 日期逐日核对）；C2 修正后对**存储 parquet** 校验 top5 == C0 forced set **67/67**，
且重跑回测逐日持仓 == C0 **67/67**、指标逐项相等（422/442 单、turnover、top1/top5/excl-top1 全同）；
C1 构造在干净日 2021-02-01/2024-09-19 手工核对（consensus 正确偏向 agreement）。
backtest_ids：C0 = S180_20d `ba710797` · C1 `707b88e7` · C2 `47e5e509`。
artifacts：`execution_policy/{S180_20d,C1_consensus,C2_confirmed}` + signal runs
`…__cf__c1_consensus__…` / `…__cf__c2_confirmed__…`。
Code：`reinfer_old_new.py`（A）、`analyze_old_new.py`（B–E）、`build_counterfactual_signals.py`
（F 信号）、`run_ablation.py`/`compare_structure.py`（F 回测与对比，group F）、
`verify_c2_vs_c0.py`（C2≡C0 逐日路径校验）。
