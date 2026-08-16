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
