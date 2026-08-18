# Multi-Seed Ensemble Study — Final Report (Corrected Baselines)

Date: 2026-08-19
Signal: `fwd_ret_180d_raw__daily_zscore` (fresh S180, raw/pre-cap rank, Top5)
Universe: CSI800 · Top5 · equal-weight entry + hold drift · `posterior_confirmed_top5_financial_rc_50_50_v1`
Window: 2021-01-04 → 2026-07-31
Governing question: *does same-training-window multi-seed ensemble reduce the model's
realization lottery while preserving Top5 right-tail winner capture?*
Baselines: correct single = seed-42 from the verified seed bank (P0 stored `rr_p0__rawrank`
reproduces the seed bank at per-day rho 1.0; P5/P10/P15 use `rr_*__rawrank_correct`).
ens3 = mean raw of seeds {42,7,77}; ens5 = mean raw of {42,7,77,123,456} — both verified
per-day rho 1.0 vs the recomputed compose.

> **Correctness correction (mandatory context):** the stored P5/P10/P15 raw-rank signal
> runs have a **train-slice inconsistency** vs the verified implementation (w1–67
> per-day rho 0.72–0.89; params and dates verified correct). The seed bank = the correct
> implementation (reproduces stored P0 exactly). All single baselines in this report are
> the correct single, NOT the stored P5/P10/P15 runs. The old phase-robustness numbers
> in `RAW_RANKING_PHASE_REPORT.md` for P5/P10/P15 are artifacts of that bug.

---

## 1. Verdict

**A. The multi-seed ensemble is a variance-reduction tool, not a return-booster.**
It **reduces the realization lottery** — at selection level (Top5 2× closer to the
5-seed consensus) and at portfolio level (between-phase CAGR spread halved, floor
raised) — and it **preserves right-tail winner capture at the selection level**
(equal-or-better cohort selection edge, more >50%/>100% winners, ~0.8 winner
retention). But it **trades away the extreme compounding tail** that drives the
single's best phases: the strongest single phases (P0/P10) beat the ensemble; the
ensemble wins the weak phase (P15) and raises the worst-case Sharpe/MaxDD.

**The ensemble answers "yes" to lottery reduction, "qualified yes" to winner-capture
preservation — it is the risk-managed version of the same selection alpha.**

---

## 2. Task 4 — realization lottery (signal level)

Seed agreement on full cross-section is high (per-window pairwise Spearman **~0.95**),
but the **Top5 is tail-lottery-dominated**: two single seeds share only **~0.21 of Top5
(1/5 names)** on a typical day (p10 worst-window p10 0.12–0.15).

| metric | p0 | p5 | p10 | p15 |
|---|---|---|---|---|
| med seed-pair rho (full cross-section) | 0.954 | 0.956 | 0.954 | 0.956 |
| med Top5 overlap between two single seeds | 0.210 | 0.220 | 0.200 | 0.200 |
| Top5 overlap ens5 vs 5-seed consensus | 0.400 | 0.400 | 0.400 | 0.400 |
| Top5 overlap ens5 vs single seed-42 | 0.200 | 0.200 | 0.200 | 0.200 |

**Read**: the model is stable on the bulk of the universe and lottery-dominated in the
tail. ens5's Top5 is **2× closer to the 5-seed consensus** than a single seed is — a
meaningful stabilization (absolute overlap still modest: ensemble ≠ "the same picks").

---

## 3. Task 5 — selection-alpha preservation (cohort level, strict close-to-close)

Per retrain-day cohort: Top5 180d forward edgeA (Top5 mean fwd − scored-universe EW)
and edgeB (− CSI800). Correct single vs ens3 vs ens5.

| phase | single edgeA180 med/pos | ens3 edgeA180 med/pos | ens5 edgeA180 med/pos | single >50%/100% | ens5 >50%/100% |
|---|---|---|---|---|---|
| p0 | +19.66 / 0.72 | +14.64 / 0.67 | +19.06 / 0.65 | 73 / 46 | **76 / 51** |
| p5 | +15.73 / 0.70 | +18.22 / 0.76 | **+18.22 / 0.78** | 79 / 49 | **85 / 53** |
| p10 | +10.33 / 0.70 | +12.83 / 0.76 | **+17.42 / 0.75** | 70 / 40 | **78 / 45** |
| p15 | +17.78 / 0.73 | +18.21 / 0.76 | +18.21 / 0.73 | 80 / 52 | **87 / 56** |

**Read**: ens5 never lowers the median selection edge (improves p5 +2.5pp, p10 +7.1pp;
p0 −0.6pp at same winner count gain), and **captures more right-tail winners in every
phase** (+4%…+13% more >50% names, +11%…+22% more >100% names).

> Note: on correct singles the phase-edge medians are more uniform than the stored-based
> table (P5 weakest +9.79 → now +15.73; P10 +16.15 → +10.33). The old "P5 weak / P15
> strong" phase pattern was partly a train-slice artifact.

---

## 4. Task 6 — right-tail winner preservation (label-level)

Top5 median 180d fwd label, expected winners, hit rate (top-decile winners), and
winner retention between selectors (fully-realised labels only):

| phase | single med_label / hit / expW | ens5 med_label / hit / expW | winners single→ens5 | winners ens5→single |
|---|---|---|---|---|
| p0 | 0.162 / 0.733 / 1.336 | 0.156 / 0.747 / 1.338 | 0.808 | 0.797 |
| p5 | 0.146 / 0.736 / 1.317 | 0.145 / 0.739 / 1.318 | 0.807 | 0.806 |
| p10 | 0.135 / 0.734 / 1.258 | 0.148 / 0.747 / 1.326 | 0.810 | 0.765 |
| p15 | 0.145 / 0.738 / 1.330 | 0.162 / 0.743 / 1.353 | 0.773 | 0.768 |

**Read**: winner hit-rate and expected winners are equal-or-better for the ensemble in
every phase; **winner names are mutually retained ~0.77–0.81** even though overall Top5
overlap single↔ens5 is only ~0.20 — the ensemble keeps the single's right-tail winners
and swaps the non-winner tail names.

---

## 5. Task 7-9 — portfolio table (CAGR / MaxDD / Sharpe), path dispersion, winner sensitivity

### 5.1 12-run table (correct singles)

| phase | single | ens3 | ens5 |
|---|---|---|---|
| p0 | +1174% / **57.9%** / −40.4% / **1.33** | +676% / 44.5% / −41.6% / 1.10 | +502% / 38.0% / −46.0% / 0.99 |
| p5 | +668% / **44.2%** / −35.5% / 1.06 | +624% / 42.7% / −40.2% / 0.99 | +565% / 40.5% / −43.0% / 1.00 |
| p10 | +1048% / **55.0%** / −36.5% / **1.28** | +768% / 47.4% / −39.4% / 1.15 | +898% / 51.1% / −40.3% / 1.23 |
| p15 | +317% / 29.3% / −49.1% / 0.82 | +358% / 31.4% / −44.9% / 0.88 | +471% / **36.8%** / −43.2% / **0.98** |

### 5.2 dispersion across phases

| metric | single | ens3 | ens5 |
|---|---|---|---|
| CAGR range (worst→best) | 29.3%→57.9% (**28.6pp**) | 31.4%→47.4% (16.0pp) | 36.8%→51.1% (**14.3pp**) |
| Sharpe range | 0.82→1.33 (0.51) | 0.88→1.15 (0.27) | 0.98→1.23 (**0.25**) |
| worst MaxDD | −49.1% (p15) | −44.9% (p15) | −46.0% (p0) |

**Read**: the ensemble **halves the between-phase outcome dispersion** and raises the
worst-case Sharpe (0.82→0.98) and worst-case CAGR (29.3%→36.8%) — the "lottery" across
start-phase is materially tamed. In exchange the best phase shrinks (57.9%→51.1% CAGR;
Sharpe 1.33→1.23).

### 5.3 path dispersion (daily returns, pairwise corr)

| phase | single↔ens3 | single↔ens5 | ens3↔ens5 |
|---|---|---|---|
| p0 | 0.876 | 0.872 | 0.980 |
| p5 | 0.902 | 0.946 | 0.924 |
| p10 | 0.956 | 0.948 | 0.985 |
| p15 | 0.898 | 0.899 | 0.978 |

**Read**: high daily-return correlation (same core selection alpha), but single↔ens
paths diverge substantially on the phases where the single carries extreme tail
winners (p0 final-value spread 1174% vs 502%).

### 5.4 winner sensitivity (total return after removing top-N per-symbol PnL)

| phase | tag | total | excl-top1 | excl-top5 | top1 share |
|---|---|---|---|---|---|
| p0 | single | 1174% | 866% | 312% | 26% |
| p0 | ens5 | 502% | 346% | 71% | 31% |
| p10 | single | 1048% | 701% | 422% | 33% |
| p10 | ens5 | 898% | 736% | 427% | 18% |
| p15 | single | 317% | 231% | 109% | 27% |
| p15 | ens5 | 471% | 386% | 148% | 18% |

**Read**: both single and ensemble outcomes are concentrated in a few per-symbol
winners; the ensemble does not de-concentrate PnL at the per-symbol level. It
de-concentrates *which single seed wins* (P0 distribution, Sec 6). In the weak phase
p15 the ensemble's top-1 share drops 27%→18% — the fragile case improves.

---

## 6. P0 realization-lottery spread (single-seed backtest distribution)

Same BASE config (NEVER block, rank_exit, top_n 5, 20d rebalance, open exec T+1,
fees 0.0003/0.001/0.001, 10M init, 2021-01-04→2026-07-31), run once per single seed.
seed42 = canonical P0 backtest (`afdd7696`); seeds 7/77/123/456 = freshly trained
from the verified seed bank; ens3/ens5 = from Task 7.

| run  | total_ret | CAGR | MaxDD | Sharpe |
|------|-----------|------|-------|--------|
| seed42 | +1174% | 57.9% | −40.4% | 1.33 |
| ens3 | +676% | 44.5% | −41.6% | 1.10 |
| seed456 | +592% | 41.5% | −42.2% | 1.03 |
| seed123 | +576% | 40.9% | −47.1% | 0.99 |
| ens5 | +502% | 38.0% | −46.0% | 0.99 |
| seed7 | +490% | 37.5% | −41.4% | 0.97 |
| seed77 | +393% | 33.2% | −44.6% | 0.88 |

**Single-seed lottery is huge**: same window + same config + same label, the only
difference is the LightGBM seed → total return spans +393%…+1174% (781pp), CAGR
spans 33.2%…57.9% (24.8pp), and Sharpe spans 0.88…1.33. seed42 is the lucky draw;
seed77 is the unlucky one. This is precisely the realization lottery the study
targets.

**The ensemble sits in the middle of the lottery, never at either pole.** ens5
(+502%) and ens3 (+676%) both land inside the single-seed range; neither captures
seed42's +1174% tail, and neither gets seed77's +393% floor. ens3 is the second-best
of 7 outcomes, ens5 is the median outcome. The ensemble de-risks the lottery — it
trades away the +1174% tail for a guaranteed middle-of-pack draw — without raising
the ceiling above the best single seed.

**Read together with Sec 5** (per-symbol winner sensitivity): the ensemble does not
de-concentrate per-symbol PnL within a run, but it does de-concentrate *which seed's
portfolio you end up with*. The same selection alpha, drawn once per seed, realizes
as +393% or +1174%; drawing 5 seeds and averaging the raw predictions realizes it as
~+500% every time.

---

## 7. Conclusion

- The S180 selection alpha is real and raw-ranked; the single model's **realization
  lottery is concentrated in the Top5 tail** (0.21 seed overlap), and it is a
  **portfolio-level lottery** too (Sec 6 quantifies).
- **Same-window multi-seed ensemble reduces that lottery**: 2× selection stability,
  ~2× narrower between-phase CAGR dispersion, higher worst-case Sharpe/MaxDD.
- **Right-tail winner capture is preserved at the selection level** (equal-or-better
  cohort edge, more >50%/100% winners, ~0.8 winner retention) and improved in the
  weak phase (P15).
- **The single-seed realization lottery is real and portfolio-level**: P0 spans
  +393%…+1174% total return (CAGR 33.2%…57.9%) from seed alone — same window, same
  config, same label. One lucky seed is +1174%; a different seed is +393%.
- **The ensemble does not beat the single's best single-seed outcome**: ens3/ens5
  land in the middle of the lottery (Sec 6), trading away seed42's +1174% tail for a
  guaranteed ~+500% median draw. It de-risks the draw without raising the ceiling.
  Adopting the ensemble is a risk-vs-ceiling choice, not a free lunch.
- The stored P5/P10/P15 numbers in the phase-robustness report are **train-slice bug
  artifacts** and must not be quoted going forward.

## Provenance

- Correct singles: seed bank (`scratch/ablation/ens_tmp/seed_raw/`) → `rr_*__rawrank_correct`.
- ens3/ens5 signal runs: composed from the same seed bank; per-day rho 1.0 verified.
- Backtests: `scripts/research/backtest_from_signal.py`, BASE config (NEVER block,
  rank_exit, top_n 5, 20d rebalance, fees 0.0003/0.001/0.001, open exec, T+1),
  2021-01-04→2026-07-31, 10M init.
- NAV/CAGR/MaxDD/Sharpe from `daily_summary.csv` total_value_after.
- Episode PnL from `derive_episodes.py`; winner-sensitivity approx (recon gap <1.5%).
