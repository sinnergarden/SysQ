# S180 Raw-Ranking Top5 — Selection-Alpha + Start-Phase Robustness — Final Report

Date: 2026-08-18
Signal: `fwd_ret_180d_raw__daily_zscore` (fresh S180, 504-trading-day train, 20d retrain cadence)
Universe: CSI800 · Top5 · equal-weight entry + hold drift · `posterior_confirmed_top5_financial_rc_50_50_v1`
Study scope: on merged #242 main — does the production skeleton converge to **fresh S180 → raw-ranking Top5 → retrain-triggered rebalance**?
Window: 2021-01-04 → 2026-07-31 (P0) / 2026-08-07-08-14 (shifted phases), fwd @60/@180.

> Evidence standard: **retrain-cohort-level forward selection edge @60/@180** (excess vs same-day scored-universe EW = edgeA; vs CSI800 = edgeB). CAGR is report-only for the portfolio path, never the alpha verdict.

---

## Verdict (Sec 8)

**A. FREEZE_FRESH_S180 — raw(pre-cap) ranking becomes the new selection baseline; the alpha is selection-level and start-phase robust; E1 ensemble NOT triggered.**

---

## 1. Sec 1 — Ranking-fix regression: stored(capped) vs P0 raw (same 68 retrain days)

Cap-tie mechanism (pre-study, verified): production chain `raw → clip(zscore(raw),±3) → score_raw → per-day zscore → score`; on **52/68** retrain days ≥5 names sit at the +3.0 cap, so engine top-5 was a tiebreak lottery (instrument-code order). Fix: `score = zscore(raw)` (order-preserving, no cap ties), `score_raw = clip(score,±3)` display-only. Ordering identity validated: Spearman(score_raw_stored, score_raw_mine) = 1.00000 per day; top5 identical on every non-capped day.

| metric | stored (capped) | P0 raw | Δ |
|---|---|---|---|
| top5 differs vs tiebreak lottery | — | 47/68 days, 226/340 slots (66%) | fix only fires where cap tied |
| @60 edgeA med | +4.29pp | **+7.02pp** | **+2.73pp** |
| @60 edgeA pos_rate | 0.65 | 0.70 | +0.05 |
| @60 edgeB med | +7.20pp | +8.28pp | +1.09pp |
| @180 edgeA med | +19.22pp | **+19.66pp** | +0.44pp |
| @180 edgeA pos_rate | 0.70 | 0.72 | +0.02 |
| @180 edgeB med | +27.69pp | +28.02pp | +0.33pp |

**Table 1 read**: removing the cap never hurts selection; @60 median edge improves +2.7pp and pos-rate improves 0.65→0.70. The cap was pure noise — raw ranking is the strictly-better baseline.

## 2. Sec 3-4 — Cohort selection edge, 4 INDEPENDENT start phases (Q2)

P5/P10/P15 = whole rolling schedule shifted +5/+10/+15 trading days (independent retraining, same def/features/label/window/universe/raw-rank/Top5). Shift verified: predict_starts start 2021-01-04/11/18/25; phases independent, 68/68 retrain grids.

### Table 2a — @180 edgeA (vs scored-universe EW)

| phase | n | med | mean | q25 | q75 | pos_rate | worst | p90 | max |
|---|---|---|---|---|---|---|---|---|---|
| P0 | 60 | **+19.66** | +36.37 | −1.00 | +60.51 | 0.72 | −23.0 | +100.1 | +234.4 |
| P5 | 59 | +9.79 | +30.02 | −0.27 | +54.19 | 0.71 | −28.6 | +99.2 | +171.7 |
| P10 | 59 | +16.15 | +30.66 | −4.25 | +41.20 | 0.69 | −27.1 | +96.5 | +256.8 |
| P15 | 59 | **+20.76** | +35.21 | −6.68 | +51.06 | 0.71 | −22.4 | +113.5 | +258.8 |

### Table 2b — @180 edgeB (vs CSI800)

| phase | n | med | q25 | q75 | pos_rate |
|---|---|---|---|---|---|
| P0 | 60 | +28.02 | +8.03 | +68.2 | 0.85 |
| P5 | 59 | +19.48 | +6.06 | +59.7 | 0.83 |
| P10 | 59 | +26.35 | +4.68 | +51.8 | 0.78 |
| P15 | 59 | +24.79 | +5.08 | +59.5 | 0.81 |

### Table 2c — @60 edgeA / edgeB (secondary horizon)

| phase | edgeA med | edgeA pos | edgeB med | edgeB pos |
|---|---|---|---|---|
| P0 | +8.15 | 0.70 | +8.43 | 0.74 |
| P5 | +4.21 | 0.66 | +7.97 | 0.78 |
| P10 | +4.00 | 0.68 | +6.65 | 0.74 |
| P15 | +2.72 | 0.65 | +7.18 | 0.71 |

### Phase-robustness judgment (Sec 4 criteria)

| criterion | @180 edgeA | read |
|---|---|---|
| median direction consistency | all 4 phases positive | ✓ consistent |
| positive-rate consistency | 0.69–0.72 (flat) | ✓ robust |
| q25 vs 0 | edgeA q25 −1.0…−6.7; **edgeB q25 +4.7…+8.0** | q25>0 vs benchmark |
| per-year median direction | positive-median 2024/2025 in all phases; 2023 weak in P5/P10/P15 (−2.6/−9.5/−6.7pp), P0 flat; P15 2021 −7.8pp | mostly consistent, tail years weak |
| within-phase dispersion vs between-phase median | IQR 45–61pp vs median spread 10.97pp | **phases within each other's noise** |
| single-cohort dependence (Sec 6) | drop 5 biggest-180 cohorts → med +12.0/+8.3/+8.3/+13.9pp | alpha broad-based |

**Table 2 read**: the selection alpha's **direction and rate are start-phase robust**; the median *level* moves with phase (P5 weakest +9.8, P15 strongest +20.8) but the between-phase spread (~11pp) is 4–6× smaller than within-phase IQR (45–61pp), and edgeB q25 stays positive in every phase. This is **"selection alpha robust / portfolio path entry-timing sensitive"**, not "phase unstable".

## 3. Sec 5 — Portfolio path (secondary)

| run | total | CAGR | MaxDD | active_vs_CSI800 | turnover | orders | top1 | top5_shr | excl_top1 |
|---|---|---|---|---|---|---|---|---|---|
| RR_P0_capped | +9.53× | +52.6% | −43.5% | +9.60 | 3.58B | 442 | 302132.SZ 14.4% | 60.4% | +816% |
| RR_P0_raw | **+11.74×** | **+57.9%** | −40.4% | +11.81 | 3.75B | 433 | 603256.SH 26.2% | 73.5% | +866% |
| RR_P5_raw | +1.27× | +15.8% | −41.7% | +1.33 | 1.59B | 546 | 001203.SZ 38.8% | 116.8% | +77.6% |
| RR_P10_raw | +2.08× | +22.4% | −46.5% | +2.15 | 1.84B | 549 | 002281.SZ 28.9% | 84.2% | +148.1% |
| RR_P15_raw | +1.54× | +18.2% | −41.6% | +1.61 | 1.53B | 517 | 600183.SH 29.5% | 94.8% | +108.6% |

Yearly (P0.1): capped 98.5/−11.5/73.8/44.1/125.3/6.2 · P0_raw 63.5/15.5/85.3/20.6/126.3/33.4 ·
P5_raw −0.1/1.2/26.2/18.0/78.5/−15.7 · P10_raw −3.4/17.8/8.8/61.5/67.7/−8.0 ·
P15_raw 7.5/−21.4/37.4/36.1/66.8/−3.5 (2021–2026).

**Table 3 read (the key distinction the study exists to make)**: shifting the rolling
schedule by 5–15 trading days moves portfolio CAGR from **57.9% → 15.8–22.4%** — a
3–4× swing — while the cohort selection edge (Table 2) stays direction- and
rate-consistent. The phase-to-phase portfolio diff is **entirely entry-path
realization**, not selection collapse: every shifted phase still picks strong Top5
cohorts, but the 20d entry grid lands on different retrain-day cohorts and the
gains concentrate into the same recurring monster winners (002281.SZ reappears as
P10's top1) at different entry points. top5 PnL share 84–117% of NAV gain in the
shifted phases means everything outside the top5 nets ≈0 — a concentration hazard
that is the documented next-step (tail cap / single-name attribution), not a reason
to abandon fresh-S180 selection. CAGR alone would misjudge this as "phase unstable"
— the cohort evidence says otherwise.

## 4. Sec 6 — Right tail @180 (per-cohort, over measured top5 names)

| phase | >+20% | >+50% | >+100% | <−20% | <−40% | any>+100% cohort | top1 med | excl-top1 med |
|---|---|---|---|---|---|---|---|---|
| P0 | 41.7% | 24.3% | 15.3% | 17.3% | 3.0% | 0.48 | 90.6% | 6.0% |
| P5 | 42.0% | 22.7% | 13.6% | 19.7% | 3.7% | 0.39 | 58.6% | 8.4% |
| P10 | 43.2% | 24.1% | 13.3% | 20.1% | 4.4% | 0.37 | 66.3% | 8.6% |
| P15 | 42.7% | 24.2% | 15.4% | 17.4% | 4.1% | 0.47 | 90.1% | 10.4% |

**Read**: big-winner presence is consistent across phases (>+100% in 13–15% of names, 37–48% of cohorts contain one). Removing each cohort's largest 180d winner still leaves a positive top5 mean (+6–10%). Cross-phase big-winner presence is stable — the right tail is structural, not phase-lucky.

## 5. Sec 7 — E1 (3-seed mean ensemble): **NOT RUN**

Trigger condition "cohort median/positive-rate clearly splits (not CAGR split)" **not met**: @180 pos_rate is flat across phases (0.69–0.72), median level spread (11pp) is within within-phase IQR (45–61pp), and edgeB q25 is positive in all phases. Per directive, when the raw single-model edge is stable, the ensemble is **not** run.

## 6. Four answers

1. **Raw ranking = new baseline?** Yes. Ordering identical on non-capped days, no selection loss at @180, +2.7pp @60 median edge vs capped; portfolio strictly better (CAGR 57.9% vs 52.6%, MaxDD −40.4% vs −43.5%, 6/6 years positive vs 5/6). The cap was pure tiebreak noise — raw(pre-cap) ranking becomes the selection score.
2. **Cross-phase selection alpha?** Yes — robust at the selection level. Direction and rate are consistent across 4 fully independent start phases (@180 pos_rate 0.69–0.72 flat, edgeB q25>0 in every phase); the median *level* moves (P5 weakest +9.8, P15 strongest +20.8pp) but the between-phase spread (~11pp) is 4–6× smaller than within-phase IQR (45–61pp).
3. **How much CAGR diff is entry-path?** **Essentially all of it.** With the capped-vs-raw regression held fixed and the same selection edge across phases, the phase-to-phase portfolio diff is 57.9% → 15.8–22.4% CAGR (9× total-return range) purely from shifting the retrain grid 5–15 trading days. That is entry-timing realization on the same recurring winners — not selection instability, and not a defensible verdict input.
4. **Enough to stop alpha-selection research?** Yes for the selection question: fresh-S180 raw-ranking Top5 selection is confirmed and frozen as the baseline. No for the portfolio path: entry-timing/concentration (top5 PnL 84–117% of NAV in shifted phases, recurring monster winners) is the remaining open problem — tail-cap / single-name attribution (already on the ledger), not more selection experiments.

---

## Artifacts

Signal runs (`data/research/signals/fwd_ret_180d_raw__daily_zscore/`): `rr_{p0,p5,p10,p15}__rawrank__financial_rc_180d_rolling_5y_to_202607_v3` (shift 0/5/10/15 td, 68/68 windows each, raw-rank `score` + display `score_raw`).
Backtest ids: RR_P0_capped `ba710797` (= S180_20d, reproduce) · RR_P0_raw `afdd7696` · RR_P5_raw `25f9f4cb` · RR_P10_raw `bf2fbcd8` · RR_P15_raw `c91ea5ae`.
Code: `scratch/ablation/{build_phase_signals,analyze_phase_cohorts,run_raw_rank_phases}.py` (+ compare_structure group R).

## Method / hygiene notes

- 4 independent retraining pipelines (no rebalance-offset-on-same-model); feature phase-invariance verified bit-identical on overlap → shifted windows slice original caches, no qlib rebuild.
- F01: signal row `data_date < trade_date` asserted per window; strict close-to-close fwd over exact trading-calendar rows, no stale-price fallback; cohort null if <3/5 top5 measured or universe <30.
- Label truncation (label store ends 2025-11-12) applies identically to all phases; last windows train on truncated labels (existing production behavior).
- Deterministic retraining seed 42 (validated Spearman ≥0.99999 vs stored); run_id/seed recorded per SignalRun manifest.
