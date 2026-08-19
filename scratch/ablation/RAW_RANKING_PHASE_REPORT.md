# S180 Raw-Ranking Top5 — Selection-Alpha + Start-Phase Robustness — Final Report

Date: 2026-08-18 · **Corrected 2026-08-20 (evidence void + canonical baseline)**
Signal: `fwd_ret_180d_raw__daily_zscore` (fresh S180, 504-trading-day train, 20d retrain cadence)
Universe: CSI800 · Top5 · equal-weight entry + hold drift · `posterior_confirmed_top5_financial_rc_50_50_v1`
Study scope: on merged #242 main — does the production skeleton converge to **fresh S180 → raw-ranking Top5 → retrain-triggered rebalance**?
Window: 2021-01-04 → 2026-07-31 (P0) / 2026-08-07-08-14 (shifted phases), fwd @60/@180.

> Evidence standard: **retrain-cohort-level forward selection edge @60/@180** (excess vs same-day scored-universe EW = edgeA; vs CSI800 = edgeB). CAGR is report-only for the portfolio path, never the alpha verdict.

---

## ⚠️ CORRECTION NOTICE (2026-08-20) — old P5/P10/P15 evidence is VOID

**The original P5/P10/P15 runs (`rr_{p5,p10,p15}__rawrank__…`) were trained with BARE LightGBM defaults, not the tuned `_DEFAULT_LGB_PARAMS`.**

- **Why**: the params-fallback regression `dict(gen.lgb_params or {})` (commit `db539d6a`) was active when those runs were built (2026-08-18 01:11–01:20 +0800). It silently replaced the tuned defaults (`num_leaves 210, lr 0.0421, colsample 0.8879, subsample 0.8789, lambda_l1 205.7, lambda_l2 581.0, max_depth 8`) with LightGBM native defaults (`num_leaves 31, lr 0.1`, no bagging / feature sampling).
- **Empirical proof**: Spearman(old `rr_p5/p10/p15__rawrank`, `rr_{p5,p10,p15}__rawrank_correct`) **median ≈ 0.80** (==1.0 on only 20/1351 days).
- **P0 is NOT affected**: `rr_p0__rawrank` was rebuilt post-fix (2026-08-19) with tuned defaults and reproduces the seed-bank ground truth at rho 1.0; every P0 number in this report reproduces on the current canonical run.
- **The manifests are misleading**: the old runs recorded `git_commit: 8b422da1` (a 2026-08-16 merge) even though they were built after the bug landed — a stale manifest SHA that also misled the #244 audit ("stored runs NOT affected" — corrected in `ENSEMBLE_MAP_AUDIT.md`).
- **Canonical corrected baseline** (use ONLY these): `rr_p0__rawrank` (rebuilt) + `rr_{p5,p10,p15}__rawrank_correct`.
- **Verdict impact**: the selection-level conclusion **survives** on the corrected baseline (all 4 phases positive edge, rate-flat, spread ≪ within-phase noise, edgeB q25 > 0 — E1 still not triggered). The **original portfolio-path** rows for P5/P10/P15 are **VOID**, but **corrected portfolio backtests DO exist** — `RR_p{5,10,15}_single_correct` (`848f2b47`/`13892a75`/`7a73b7fa`, same config as the voided runs, on `rr_{p5,p10,p15}__rawrank_correct` signals). On them the corrected CAGRs are **P5 +44.2% · P10 +55.0% · P15 +29.3%** vs P0 +57.9% (see Sec 5) — the old "57.9% → 15.8–22.4%" entry-timing *magnitude* claim is retracted.
- **Not affected**: PR #242 (`FINAL_REPORT.md`) is a backtest-level `rebalance_offset` study on the stored capped S180 signal (no retrain, no phase runs) — none of its numbers touch the voided evidence.
- Old numbers below are kept in the **Superseded evidence** section with `[VOID]` tags so they cannot re-pollute; they must not be cited.

---

## Verdict (Sec 8)

**A. FREEZE_FRESH_S180 — raw(pre-cap) ranking becomes the new selection baseline; the alpha is selection-level and start-phase robust; E1 ensemble NOT triggered.** *(Re-affirmed 2026-08-20 on the corrected baseline; phase-level medians updated.)*

---

## 1. Sec 1 — Ranking-fix regression: stored(capped) vs P0 raw (same 68 retrain days)

Cap-tie mechanism (pre-study, verified): production chain `raw → clip(zscore(raw),±3) → score_raw → per-day zscore → score`; on **52/68** retrain days ≥5 names sit at the +3.0 cap, so engine top-5 was a tiebreak lottery (instrument-code order). Fix: `score = zscore(raw)` (order-preserving, no cap ties), `score_raw = clip(score,±3)` display-only. Ordering identity validated: Spearman(score_raw_stored, score_raw_mine) = 1.00000 per day; top5 identical on every non-capped day. **P0-only — unaffected by the params bug.**

| metric | stored (capped) | P0 raw | Δ |
|---|---|---|---|
| top5 differs vs tiebreak lottery | — | 47/68 days, 226/340 slots (66%) | fix only fires where cap tied |
| @60 edgeA med | +4.29pp | **+7.02pp** | **+2.73pp** |
| @60 edgeA pos_rate | 0.65 | 0.70 | +0.05 |
| @60 edgeB med | +7.20pp | +8.28pp | +1.08pp |
| @180 edgeA med | +19.22pp | **+19.66pp** | +0.44pp |
| @180 edgeA pos_rate | 0.70 | 0.72 | +0.02 |
| @180 edgeB med | +27.69pp | +28.02pp | +0.33pp |

**Table 1 read**: removing the cap never hurts selection; @60 median edge improves +2.7pp and pos-rate improves 0.65→0.70. The cap was pure noise — raw ranking is the strictly-better baseline.

> *Cohort note (2026-08-20)*: Sec 1's edge rows are computed by `sec1_regression()` — it merges stored(capped) with P0 raw on `trade_date` and drops rows with NaN `edgeA_180`, so it measures a **smaller subset** than the phase cohort in Table 2c (n=66 @60). That is why Sec 1's P0 raw @60 (+7.02 / +8.28pp) differs from Table 2c's P0 row (+8.15 / +8.43pp). Both are valid; they answer different questions (same-schedule stored-vs-raw on matched dates vs full phase cohort). Sec 1 is P0-only and unaffected by the params bug; its values are carried from the original 2026-08-18 analysis.

## 2. Sec 3-4 — Cohort selection edge, 4 INDEPENDENT start phases (Q2)

P5/P10/P15 = whole rolling schedule shifted +5/+10/+15 trading days (independent retraining, same def/features/label/window/universe/raw-rank/Top5). Shift verified: predict_starts start 2021-01-04/11/18/25; phases independent, 68/68 retrain grids (P15 builds 67 effective windows — calendar-edge drop, see hygiene notes).

> **Corrected 2026-08-20**: the following tables use the **canonical corrected baseline** — `rr_p0__rawrank` (rebuilt, tuned defaults) + `rr_{p5,p10,p15}__rawrank_correct`. The original table values (bare-params) are [VOID]; see Superseded evidence §S2.

### Table 2a — @180 edgeA (vs scored-universe EW) — CORRECTED

| phase | n | med | mean | q25 | q75 | pos_rate | worst | p90 | max |
|---|---|---|---|---|---|---|---|---|---|
| P0 | 60 | **+19.66** | +36.37 | −1.00 | +60.51 | 0.72 | −23.0 | +100.1 | +234.4 |
| P5 | 59 | +15.73 | +41.54 | −3.26 | +64.13 | 0.69 | −23.5 | +119.3 | +269.5 |
| P10 | 59 | +10.33 | +28.77 | −3.49 | +49.63 | 0.69 | −33.3 | +108.5 | +138.6 |
| P15 | 59 | +17.78 | +39.95 | −1.32 | +72.27 | 0.73 | −23.6 | +104.1 | +249.6 |

### Table 2b — @180 edgeB (vs CSI800) — CORRECTED

| phase | n | med | q25 | q75 | pos_rate |
|---|---|---|---|---|---|
| P0 | 60 | +28.02 | +8.03 | +68.2 | 0.85 |
| P5 | 59 | +23.33 | +3.25 | +73.2 | 0.80 |
| P10 | 59 | +20.06 | +4.49 | +57.4 | 0.78 |
| P15 | 59 | +26.06 | +4.06 | +83.1 | 0.81 |

### Table 2c — @60 edgeA / edgeB (secondary horizon) — CORRECTED

| phase | edgeA med | edgeA pos | edgeB med | edgeB pos |
|---|---|---|---|---|
| P0 | +8.15 | 0.70 | +8.43 | 0.74 |
| P5 | +5.62 | 0.65 | +6.53 | 0.75 |
| P10 | +5.83 | 0.72 | +7.58 | 0.75 |
| P15 | +6.97 | 0.69 | +9.74 | 0.77 |

### Phase-robustness judgment (Sec 4 criteria) — CORRECTED

| criterion | @180 edgeA | read |
|---|---|---|
| median direction consistency | all 4 phases positive | ✓ consistent |
| positive-rate consistency | 0.69–0.73 (flat) | ✓ robust |
| q25 vs 0 | edgeA q25 −3.5…−1.0; **edgeB q25 +3.25…+8.03** | q25>0 vs benchmark |
| per-year median direction | positive-median 2021/2022/2024/2025 in all phases; 2023 weak in P10 (−0.11pp), positive elsewhere; P0 2023 +2.39 | mostly consistent, 2023 tail-year weak |
| within-phase dispersion vs between-phase median | IQR 53.1–73.6pp vs median spread 9.33pp | **phases within each other's noise** |
| single-cohort dependence (Sec 6) | drop 5 biggest-180 cohorts → med +9.8…+12.4pp | alpha broad-based |

**Table 2 read (corrected)**: the selection alpha's **direction and rate are start-phase robust**; the median *level* moves with phase (P10 weakest +10.3, P0 strongest +19.7) but the between-phase spread (9.3pp @180, 2.5pp @60) is **small relative to within-phase IQR** (IQR/spread = 5.7–7.9× @180, 6.4–9.3× @60; IQR 53–74pp @180, 16–24pp @60), and edgeB q25 stays positive in every phase. On the corrected baseline the cross-phase read is **unchanged in kind** from the original — only the phase-level *level* shifts (the old "P15 strongest / P5 weakest" ordering was an artifact of bare params).

## 3. Sec 5 — Portfolio path (secondary)

> **Corrected 2026-08-20**: `RR_P5_raw / RR_P10_raw / RR_P15_raw` consumed the **VOID** bare-params signals — their CAGR / MaxDD / turnover rows are **NOT evidence** (kept in §S3 for reconciliation). **Corrected portfolio backtests DO exist** on the canonical signals: `RR_p{5,10,15}_single_correct` = `848f2b47` / `13892a75` / `7a73b7fa` (`data/research/ablation/ensemble_pf/`, built 2026-08-18 17:44–17:59 UTC, `git_commit dd317694`) — config **field-identical** to the voided runs (same `posterior_confirmed_top5_financial_rc_50_50_v1` template, `rebalance_offset 5/10/15`, `rebalance_freq 20d`, top5, open/preopen, fees), only the signal differs (`rr_{p5,p10,p15}__rawrank_correct`). CAGRs below are recomputed from those manifests (P0 verified against the original: 57.9%, MaxDD −40.4%).

| run | signal | CAGR | MaxDD | turnover | orders | status |
|---|---|---|---|---|---|---|
| RR_P0_capped | stored capped S180 | +52.6% | −43.5% | 3.58B | 442 | OK (stored capped) |
| RR_P0_raw | rr_p0__rawrank (tuned) | **+57.9%** | −40.4% | 3.75B | 433 | OK (P0 = tuned) |
| RR_P5_single_correct | rr_p5__rawrank_correct | **+44.2%** | −35.5% | 2.71B | 453 | corrected |
| RR_P10_single_correct | rr_p10__rawrank_correct | **+55.0%** | −36.5% | 4.25B | 468 | corrected |
| RR_P15_single_correct | rr_p15__rawrank_correct | **+29.3%** | −49.1% | 1.88B | 447 | corrected |

**Table 3 read (corrected)**: the P0 rows stand — **CAGR and MaxDD remain better** (57.9% / −40.4% vs 52.6% / −43.5%); other execution metrics are reported unchanged. On the corrected baseline the shifted-phase CAGRs are **far higher than the voided ones** (P5 15.8→44.2%, P10 22.4→55.0%, P15 18.2→29.3%) — the old "57.9% → 15.8–22.4%" spread was **mostly a bare-params artifact**, not an entry-timing effect. Corrected read: P10 ≈ P0 (55.0 vs 57.9), P5 within 13.7pp of P0, and **P15 is the only clear weak phase (+29.3%, MaxDD −49.1% — the only phase with worse drawdown than P0)**. The "entry-path vs selection" split survives but with a **much smaller magnitude**: start-phase shifts are mostly *not* an entry-timing penalty. **P15's weakness is the residual — and its cause is currently unattributed.** P15 also builds 67 rather than 68 effective research windows (calendar edge), but that dropped terminal window is outside the evaluated portfolio-backtest horizon (ends 2026-07-31) and therefore does **not** explain P15's underperformance (see hygiene notes).

## 4. Sec 6 — Right tail @180 (per-cohort, over measured top5 names) — CORRECTED

| phase | >+20% | >+50% | >+100% | <−20% | <−40% | any>+100% cohort | top1 med | excl-top1 med |
|---|---|---|---|---|---|---|---|---|
| P0 | 41.7% | 24.3% | 15.3% | 17.3% | 3.0% | 0.48 | 90.6% | 6.0% |
| P5 | 43.5% | 26.9% | 16.7% | 15.3% | 3.1% | 0.46 | 88.4% | 7.0% |
| P10 | 40.8% | 23.8% | 13.6% | 18.0% | 2.0% | 0.44 | 84.0% | 2.2% |
| P15 | 46.1% | 27.1% | 17.6% | 16.6% | 3.4% | 0.49 | 99.0% | 9.6% |

**Read**: big-winner presence is consistent across phases (>+100% in 13.6–17.6% of names, 44–49% of cohorts contain one). Removing each cohort's largest 180d winner still leaves a positive top5 mean (+2.2–9.6%). Cross-phase big-winner presence is stable — the right tail is structural, not phase-lucky. (P10's excl-top1 median drops to +2.2% but stays positive; single-cohort dependence read +9.8…+12.4pp below.)

Single-cohort dependence (drop 5 largest-180 cohorts, @180 edgeA med): P0 +12.0pp · P5 +10.6pp · P10 +9.8pp · P15 +12.4pp → alpha broad-based.

## 5. Sec 7 — E1 (3-seed mean ensemble): **NOT RUN** (re-confirmed on corrected baseline)

Trigger condition "cohort median/positive-rate clearly splits (not CAGR split)" **not met**: @180 pos_rate is flat across phases (0.69–0.73), median level spread (9.3pp) is well within within-phase IQR (53–74pp), and edgeB q25 is positive in all phases. Per directive, when the raw single-model edge is stable, the ensemble is **not** run.

## 6. Four answers (corrected 2026-08-20)

1. **Raw ranking = new baseline?** Yes. Ordering identical on non-capped days, no selection loss at @180, +2.7pp @60 median edge vs capped; portfolio strictly better (CAGR 57.9% vs 52.6%, MaxDD −40.4% vs −43.5%). The cap was pure tiebreak noise — raw(pre-cap) ranking becomes the selection score. *(P0-based; unchanged by the correction.)*
2. **Cross-phase selection alpha?** Yes — robust at the selection level, on the corrected baseline: direction and rate consistent across 4 fully independent start phases (@180 pos_rate 0.69–0.73 flat, edgeB q25 +3.25…+8.03); the median *level* moves (P10 weakest +10.3, P0 strongest +19.7) but the between-phase spread (9.3pp @180) is small relative to within-phase IQR (53–74pp; IQR/spread 5.7–7.9×).
3. **How much CAGR diff is entry-path?** **Mostly not — the old magnitude is retracted.** The original claim ("essentially all of it", 57.9% → 15.8–22.4% from shifting the retrain grid) rested on the [VOID] shifted-phase backtests and is retracted. **Corrected portfolio backtests exist** (`RR_p{5,10,15}_single_correct`, same config on `rawrank_correct`) and give **P5 +44.2% · P10 +55.0% · P15 +29.3%** vs P0 +57.9%. P10 ≈ P0, P5 within 13.7pp — the shifted phases are **not** penalized by entry timing. The only weak phase is P15 (+29.3%, MaxDD −49.1%). **P15's weakness is unattributed**: P15 also builds 67 rather than 68 effective research windows (calendar edge), but that dropped terminal window is outside the evaluated portfolio-backtest horizon (ends 2026-07-31) and therefore does **not** explain P15's underperformance. So the "entry-path vs selection" split survives with a **small magnitude, concentrated in P15** — not the 3–4× swing the voided numbers implied.
4. **Enough to stop alpha-selection research?** Yes for the selection question: fresh-S180 raw-ranking Top5 selection is confirmed and frozen as the baseline (on corrected evidence). No for the portfolio path: concentration/tail risk (top5 PnL concentration, recurring monster winners) remains the open problem — tail-cap / single-name attribution (on the ledger). The shifted-phase portfolio path is now covered by the corrected `single_correct` backtests. **P15 is the only clear weak phase (29.3%, worst MaxDD), but its cause is unattributed — P15-specific attribution is deferred until the PIT audit** (which may itself change each phase's portfolio path, so it should come first).

---

## Superseded evidence — [VOID] do not cite

These were the numbers in the original 2026-08-18 report. They are **invalid** because the P5/P10/P15 runs behind them used bare LightGBM defaults (see Correction Notice). Kept here only so that any older copy of this report can be reconciled against the corrected tables.

### S2 — cohort selection edge (original bare-params values)

| phase | @180 edgeA med | @180 edgeB med | @60 edgeA med | @60 edgeA pos | @60 edgeB med |
|---|---|---|---|---|---|
| P0 | +19.66 | +28.02 | +8.15 | 0.70 | +8.43 | ← OK (tuned) |
| P5 | **+9.79** | +19.48 | +4.21 | 0.66 | +7.97 | **[VOID]** → corrected +15.73 / +23.33 / +5.62 |
| P10 | **+16.15** | +26.35 | +4.00 | 0.68 | +6.65 | **[VOID]** → corrected +10.33 / +20.06 / +5.83 |
| P15 | **+20.76** | +24.79 | +2.72 | 0.65 | +7.18 | **[VOID]** → corrected +17.78 / +26.06 / +6.97 |

Original phase-robustness read stated "P5 weakest +9.8, P15 strongest +20.8pp, spread ~11pp vs IQR 45–61pp". On corrected data the weakest is P10 (+10.3) and strongest P0 (+19.7), spread 9.3pp vs IQR 53–74pp. The qualitative judgment (robust at selection level) is unchanged.

### S3 — portfolio path (original, shifted-phase rows void)

Original Table 3 read claimed the phase-to-phase portfolio diff (57.9% → 15.8–22.4% CAGR) was "entirely entry-path realization" — **retracted** (the shifted signals were bare-params). The 2026 yearly rows for P5/P10/P15 in the original report are likewise void. Corrected portfolio numbers (P5 +44.2% · P10 +55.0% · P15 +29.3%) are in Sec 5, computed from the `RR_p{5,10,15}_single_correct` backtests.

---

## Artifacts

**Canonical (corrected) signal runs** (`data/research/signals/fwd_ret_180d_raw__daily_zscore/`):
- `rr_p0__rawrank__financial_rc_180d_rolling_5y_to_202607_v3` — **rebuilt 2026-08-19**, tuned defaults, seed 42, verified rho 1.0 vs seed-bank ground truth.
- `rr_{p5,p10,p15}__rawrank_correct__financial_rc_180d_rolling_5y_to_202607_v3` — **canonical shifted-phase baseline**, tuned defaults, seed 42.

**Voided runs (do not cite)**: `rr_{p5,p10,p15}__rawrank__financial_rc_180d_rolling_5y_to_202607_v3` — bare params; kept on disk only for provenance/audit.

Backtest ids: RR_P0_capped `ba710797` (= S180_20d, reproduce) · RR_P0_raw `afdd7696` (valid, P0 tuned) · RR_P5_raw `25f9f4cb` / RR_P10_raw `bf2fbcd8` / RR_P15_raw `c91ea5ae` (**VOID** — consumed bare signals) · **corrected** `RR_p5_single_correct` `848f2b47` / `RR_p10_single_correct` `13892a75` / `RR_p15_single_correct` `7a73b7fa` (rawrank_correct signals, `data/research/ablation/ensemble_pf/`).
Code: `scratch/ablation/{build_phase_signals,analyze_phase_cohorts,run_raw_rank_phases}.py` (+ compare_structure group R). Corrected analysis: `analyze_phase_cohorts.py --suffix rawrank_correct` → output archived at `scratch/ablation/analysis_rawrank_correct_2026-08-20.txt` (all corrected Tables 2a/2b/2c, Sec 6, and the phase-robustness judgment trace to it; Sec 1/Table 1 is carried from the original 08-18 analysis per the cohort note above — P0-only, unaffected by the bug).

## Method / hygiene notes

- 4 independent retraining pipelines (no rebalance-offset-on-same-model); feature phase-invariance verified bit-identical on overlap → shifted windows slice original caches, no qlib rebuild.
- F01: signal row `data_date < trade_date` asserted per window; strict close-to-close fwd over exact trading-calendar rows, no stale-price fallback; cohort null if <3/5 top5 measured or universe <30.
- Label truncation (label store ends 2025-11-12) applies identically to all phases; last windows train on truncated labels (existing production behavior).
- Deterministic retraining seed 42 (P0 validated Spearman ≥0.99999 vs stored; corrected runs validated rho 1.0 vs seed bank).
- **Params-fallback bug (2026-08-20 retrospective)**: `dict(gen.lgb_params or {})` in commit `db539d6a` trained shifted phases with bare LightGBM defaults during 2026-08-18 01:11–01:20 +0800; P0 was rebuilt post-fix. Fixed by `is not None` (PR #245, bde374ac) and checkpoint fingerprints now bind effective params (PR #246, 933f74ee) so a rerun can never again reuse a cross-config checkpoint.
- P15 builds 67 effective windows (calendar-edge clamp drops the last shifted window); cohort n=59 vs 60. Backtest end 2026-07-31 unaffected.
- Corrected tables recomputed with the same `analyze_phase_cohorts.py` analysis on the canonical runs; P0 rows reproduce the original report exactly (edgeA @180 +19.66, @60 +8.15; Sec 1: 52/68 cap-tie, 47/68 top5 differ, 226/340 slots).
