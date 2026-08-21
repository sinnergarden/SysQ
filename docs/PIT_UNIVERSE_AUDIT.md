# PIT Universe Audit — CSI800 S180 Point-In-Time Correction

- **Date**: 2026-08-21
- **Scope**: Does the 2021-2026 S180 backtest (static-current CSI800 universe) suffer constituent survivorship / selection bias, and how much S180 selection alpha and portfolio alpha survives a true Point-In-Time (PIT) correction?
- **Governing discipline (Section 17)**: this task attacks the existing conclusion, not defends it. If PIT correction drops CAGR from 50% to 20%, accept it honestly. Prohibited: tuning membership lag / constituent source / model to recover CAGR, deleting historical losers, fixing only the prediction universe without fixing training, using current-CSI800 historical union as PIT, presenting a filter-only diagnostic as a corrected backtest.
- **Result**: **PIT correction cuts portfolio CAGR from 57.93% to 14.54% (survival 25.1%)**. 53.0% of the baseline's gross PnL came from positions held at dates when the stock was **not** a CSI800 constituent — the survivorship/selection-bias channel, made concrete and quantified.

---

## 1. Executive summary / verdict

| | Baseline (static-current CSI800) | PIT (union + per-day filter) | Δ |
|---|---|---|---|
| Total return | 11.74× | 1.13× | −10.6× |
| **CAGR** | **57.93%** | **14.54%** | **−43.4pt (survival 25.1%)** |
| MaxDD | −40.41% | −37.50% | +2.9pt (PIT slightly better) |
| Sharpe | 1.33 | 0.58 | −0.75 |
| Signal IC / RankICIR | — | 0.0744 / 0.961 | |
| Instruments in signal | 800 | 1,205 | survivorship-correction visible |
| Non-member buys | — | **0 / 228** | vs Stage-6 diagnostic 7/232 |

**Verdict: the baseline's headline alpha is substantially a static-current-universe artifact.** Under a true PIT correction (training and prediction on the same PIT-correct rows, data fully restored, coverage gate passed), S180 top-5 raw-rank portfolio CAGR collapses from 57.93% to 14.54%. The correction is not a diagnostic artifact (Stage-6 filter-only gave 13.49% with a far worse MaxDD −53.8% and 3% non-member leakage) — it is the honest, full-PIT result.

Selection-level alpha (IC 0.0744, RankICIR 0.961) survives but is much weaker than the baseline's portfolio path implied. The single biggest reason is quantified in §7: **53.0% of baseline PnL came from stocks the PIT strategy could not legally hold** (bought 0–4 years before they entered the index, or after they were dropped).

---

## 2. Stage 1 — Semantics: static-current bias confirmed

`CURRENT_UNIVERSE_SEMANTICS = STATIC_CURRENT`. The baseline backtest materializes today's CSI800 constituent list and applies it over the 2021-2026 lookback. Consequences:

- **Survivorship-in**: today's constituents are, by construction, stocks that survived to 2026. Index entrants after 2021 are treated as if they were investable for their entire 2021-2026 history, including the pre-entrance window.
- **Survivorship-out**: stocks dropped or delisted from the index since 2021 vanish from the backtest entirely (they may not even be in the universe file), even though they were investable in 2021.
- **Re-entry misses**: stocks that left and re-entered the index are held continuously, including the non-member gap.

A true PIT universe must answer *"was this stock a CSI800 constituent on date T?"* from the index's actual monthly snapshots, not from today's membership.

---

## 3. Stage 2 — PIT artifact (`csi800_pit_v1`)

- Artifact: `membership.parquet` — **2,848 spans / 2,013 unique instruments** (2013–present monthly index-weight snapshots).
- Membership hash (provenance binding): `membership_sha256 = 04eabf8064b8def2fcf778d884d3ec2d3f531b99326766978684b2c096668426`.
- Removal semantics (conservative): removal is effective **the day after the last monthly index-weight snapshot** that still contains the stock. This excludes up to ~1 month of genuinely-still-member period — **conservative, never inflating**. Filter and gate use the same artifact, so the 9B experiment is internally self-consistent; the boundary semantics are a recorded limitation (§10.1).
- 9A data restoration: **636 deficient union members backfilled** (2016-01-04 → 2026-07-31) via the canonical `universe_history` catch-up path.

---

## 4. Stage 3 — `PitUniverseStore` API

`qsys/research/pit_universe.py`:
- `spans` — per-span rows (NOT collapsed), preserves leave/re-enter gaps.
- `is_member(instrument, date)` — exact PIT membership test.
- `membership_window(symbol)` / `membership_periods(...)`.
- `to_registry_frame()` — drives the qlib registry (`csi800_pit_union.txt`) and label universe.

Key correctness property: the store answers from per-interval spans, never from the qlib registry's collapsed min/max ranges — a stock that left and re-entered is excluded during its non-member gap.

---

## 5. Stage 4 — Data restoration (9A) + honest coverage

Coverage gate on the full PIT-union member-day set `[2018-03-13, 2026-07-31]`:

| | count | share |
|---|---|---|
| Total member-days | 1,611,920 | 100% |
| Bar-present | 1,611,851 | 99.9957% |
| └ usable (non-NaN close) | 1,603,970 | **99.51%** |
| └ NaN-close suspension placeholders | 7,881 | 0.49% (国海证券 2020-01-06, 康得新 2018×2, 中兴通讯 2018-04, 美的集团 2018-09, 上海莱士 2018-02) |
| Bar-absent | 69 | 0.0043% (all genuine suspensions) |

**Honest decomposition**: the "99.9957%" headline omits 7,881 NaN-close suspension placeholders — usable-data coverage is 99.51%. Zero hidden conversion gaps; every uncovered member-day is a genuine suspension. Stage-7 pre-9A baseline was 72.6%; the gate passed at ≥99.9%.

---

## 6. Stage 5 — Diagnostic contamination root cause (Stage 6 bisect)

The Stage-6 filter-only diagnostic (CAGR 13.49%, MaxDD **−53.8%**) is **not** the corrected result, per Section 17. Its 14.1%-classic contamination root cause was found by adversarial verification:

`/tmp/pit_filter_signal.py` applied membership via `bisect_right(snap_dates, d)` where `d` is `YYYY-MM-DD` and `snap_dates` are `YYYYMMDD`. Lexicographically `-` (0x2D) < `0` (0x30), so every date selected a **~1-year-stale December snapshot** → 1.19% of the diagnostic cache held non-members at their feature date, and 7/232 (3.0%) of its buys were non-member at entry. The 9B run applies membership on the shared train+predict frame at feature-date semantics with **0 non-member buys** (§7).

---

## 7. Stage 6 — Full PIT P0 retrain + backtest (9B)

### 7.1 PIT filter semantics (proven, not assumed)
- Per-window cache holds the **raw union frame** by design; `_apply_pit_membership` is applied **once, immediately after `_load_data`**, so train and predict subsets of the shared frame see identical PIT rows (Section 17 requirement: PIT applies to training, not just the prediction universe).
- Cache identity binds `pit_membership` + `source_manifest_hash` (fresh `2d8ff143…`, distinct from baseline `9e6148…`), so no stale-cache reuse.
- Verification: verbatim re-run of `_apply_pit_membership` on the latest raw window (575,357 rows) → **100% of post-filter (inst, date) inside spans**; full-cache check → **0/1,069,938 (inst, data_date) non-members** (1,205 instruments, 2021-01-04 → 2026-07-31).
- Known pitfall (recorded): when validating "frame ⊆ spans" with a multi-span merge, you must `groupby(['instrument','trade_date'])['inside'].any()` before counting — otherwise dates matching one span but not another get double-counted as outside (a false 255,625-outside alarm; real count was 0).

### 7.2 Signal identities
- `signal_id = fwd_ret_180d_raw_pit__daily_zscore`
- `signal_run_id = rolling__financial_rc_180d_rolling_5y_to_202607_v3_pit__v3a_growth_financial_180d_pit__fwd_ret_180d_raw_pit__daily_zscore__2021-01-01_2026-07-31`
- Backtest: `bt_2021-01-04_2026-07-31_f6dfbd50` (`data/research/ablation/pit_audit/RR_P0_raw__pitv1_full/`)

### 7.3 Headline result
| | Baseline P0 | PIT P0 | Δ |
|---|---|---|---|
| CAGR | 57.93% | **14.54%** | −43.4pt |
| MaxDD | −40.41% | −37.50% | +2.9pt |
| Sharpe | 1.33 | 0.58 | −0.75 |
| Total return | 11.74× | 1.13× | |

CAGR **survival ratio 25.1%**. Baseline MaxDD −40.41% matches the documented canonical P0 (−40.4%), confirming the reference run is correct. The audit's central finding per Section 17: *"如果 PIT 修正后 CAGR 从 50% 掉到 20%，如实接受"* — it fell to 14.54%, and this report accepts it.

### 7.4 Buy gate
`pit_buy_gate.py` (feature-date membership): **0/228 filled buys non-member at data_date**. (Stage-6 diagnostic reproduced 7/232 = 3.0% leakage, validating the gate's sensitivity.)

---

## 8. Stage 7 — Top5 / winner attribution (Stage 10)

Episode attribution (identical `derive_episodes.py`, same posterior-policy params):

| | Baseline | PIT |
|---|---|---|
| Episodes (closed) | 218 (213) | 228 (223) |
| Win rate | 56.3% | 52.9% |
| Avg win / avg loss | +21.7% / −10.2% | +12.5% / −8.8% |
| Payoff ratio | 2.13 | 1.42 |
| Top-5 share of net | 55.9% | 60.0% |
| **Top-10 share of net** | 72.9% | **91.7%** |
| Largest single winner | **302132.SZ +404%** | 002281.SZ +73% |

### 8.1 The mechanism, quantified
Of the baseline's **top-5 winners, 4 were NOT CSI800 members at entry**, and those positions produced **53.0% of the baseline's total PnL** (¥62.27M / ¥117.41M):

| Stock | Held | Return | PnL share | PIT status at hold time |
|---|---|---|---|---|
| 603256.SH | 2025-11→2026-02 | +133% | **20.7%** | dropped 2021-11, re-joined 2026-06 → big run happened OUTSIDE the index |
| 603256.SH | 2025-04→2025-06 | +54% | 5.5% | same gap |
| 302132.SZ | 2022-09→2023-02 | +404% | 12.4% | entered index 2023-12 → held 1.4 years before membership |
| 688313.SH | 2024-09 / 2026-01 | +77% / +56% | 12.3% | entered index 2026-06 |
| 002265.SZ | 2021-11 | +232% | 4.4% | entered index 2025-12 |
| 688114.SH | 2025-01→2025-02 | +77% | — | **only legitimate top-5** (member since 2023-12) |

Verified: 302132.SZ had tradeable bars in 2022-09 (107/117 non-null) — it was a listed stock that simply hadn't entered the index yet. None of the 4 non-member symbols appear **at all** in the PIT run's holdings.

### 8.2 Read
The baseline's alpha is dominated by **buying future-index members** (stocks that would enter CSI800 later) and **re-buying recently-dropped names** during their non-member run — both survivorship/selection-bias channels. The PIT run's residual alpha is a handful of genuine mid-size winners (top-10 = 91.7% of net, largest +73%), with a payoff that collapsed from 2.13 to 1.42.

---

## 9. Stage 8 — Phase robustness (Stage 11)

**Question**: is the PIT-corrected result stable across the 4 independent start phases (the schedule shifted +0/+5/+10/+15 trading days, each fully retrained on its own shifted window grid)? The baseline (uncorrected) phases were P0 57.9% / P5 44.2% / P10 55.0% / P15 29.3% (corrected baseline, `rr_{p5,p10,p15}__rawrank_correct`).

PIT phases are 4 independent retrains via `build_phase_signals_pit.py` (same def/features/label/window/universe/PIT semantics/raw-rank/Top5, shift = +0/+5/+10/+15 td), backtested with `rebalance_offset = shift`, identical frozen Stage-6 flags.

### 9.1 Results

| Phase | Baseline corrected | PIT | Survival | PIT MaxDD | PIT Sharpe | PIT buys | Non-member buys |
|---|---|---|---|---|---|---|---|
| P0 | 57.9% | **14.54%** | 25.1% | −37.50% | 0.58 | 228 | **0** |
| P5 | 44.2% | **16.05%** | 36.3% | −35.55% | 0.59 | 208 | **0** |
| P10 | 55.0% | **7.52%** | 13.7% | −37.83% | 0.39 | 228 | **0** |
| P15 | 29.3% | **9.40%** | 32.1% | −40.53% | 0.44 | 215 | **0** |

Provenance: all four backtests on commit `ef61d870`, `bt_2021-01-04_2026-07-31_{f6dfbd50,2dc26f08,4b9f29e7,eaa22260}`, signals `…__rr_{p0(=canonical),p5,p10,p15}__rawrank__financial_rc_180d_rolling_5y_to_202607_v3_pit` (P15 built 67/68 windows — the 68th's shifted predict span is empty at the calendar edge, identical to the baseline phase build).

### 9.2 Phase independence
Median daily rank-ρ (each phase vs P0 signal): **P5 0.946, P10 0.939, P15 0.944** (min across days 0.78–0.83, 1336–1346 days). High but not ~1.0 — the phases are the same model family retrained on genuinely shifted window grids, so rank structure is largely shared while the shifted schedule still produces distinct per-window models (mirrors the baseline phase study's finding).

### 9.3 Read
- **All four PIT phases collapse to a narrow 7.5–16.1% band**; none retains anything near the baseline's 29–58%. The PIT correction is not a P0 artifact — it is phase-robust in the sense that *every* start phase loses the large majority of its CAGR.
- **The best PIT phase is P5 (16.05%), not P0.** On the uncorrected baseline the phase ordering is P0>P10>P5>P15; under PIT it becomes P5>P0>P15>P10. There is no "correct phase" that restores the headline number — the entry-timing alpha the baseline showed (P0 57.9%) does not survive PIT correction.
- **Survival ratios 13.7–36.3%** — consistently far below parity across all phases, confirming the §1 verdict is not driven by one unlucky calendar alignment.
- **Buy gates pass on all four phases (0 non-member buys, 208–228 fills each)** — the PIT restriction is uniformly enforced across the phase grid; none of these results is contaminated by non-member leakage.
- **Conclusion for the audit**: the survivorship/selection-bias channel is dominant across every start phase. The residual PIT portfolio alpha (7.5–16% CAGR, MaxDD −35 to −40%, Sharpe 0.39–0.59) is modest, concentrated in a handful of names (§8.2), and not recoverable by choosing an entry phase.

---

## 10. Limitations, latent warnings, provenance

### 10.1 Recorded limitations
- **Removal-semantics boundary (conservative)**: span artifact marks removal effective the day after the last monthly snapshot containing the stock. On the Stage-6 diagnostic, 3/7 non-member buys (600754.SH 2022-06-01 / 600256.SH 2022-12-23 / 002812.SZ 2025-06-20) were actually still within the last available snapshot — artifact boundary, conservative direction, never inflated. Filter and gate share the artifact, so 9B is internally self-consistent. Do **not** alter artifact semantics to recover results.
- **Coverage NaN-close placeholders**: usable-data coverage is 99.51% (7,881 NaN-close suspension days), not the bar-presence 99.9957% headline.
- **Label-manifest overstatement**: the PIT label manifest records `prediction_end 2026-08-10` while the frame actually extends only to 2025-11-04 (config `date_range` covers the frame-derived extent; does not affect this experiment's windows).

### 10.2 Latent warnings (non-live, no blocker)
- `single_label_lightgbm_binary` factory (`matrix_job.py:313-321`) has **no unknown-param guard** — it would silently drop `pit_membership` and produce fake-PIT rows. No config currently takes that path. TODO: add the assert guard.
- `_apply_pit_membership` merge depends on instrument symbol format consistency (artifact upper/strip vs frame); mismatch raises fail-loud ("no rows matched"), never silent.
- Buy-gate uniqueness: gate counts distinct buys; a duplicate execution row would not be double-counted.

### 10.3 Provenance binding
- Universe artifact: `csi800_pit_v1`, `membership_sha256 = 04eabf8064b8def2fcf778d884d3ec2d3f531b99326766978684b2c096668426`.
- Feature snapshot (post-9A): `source_manifest_hash = 2d8ff143be01c3a99b44eeffd58706c91c774f93b93c13914f4d88ef355a1e2f` (baseline `9e6148…`).
- Backtest invocation: frozen Stage-6 flags byte-equivalent to `run_raw_rank_phases.py` BASE + never-exits; only `signal_run_id` / output-dir differ.
- All phase/seed checkpoints provenance-bound via `_checkpoint_fingerprint` (feature/label/model/code/calendar/windows hashes).

### 10.4 Remaining TODO
- ~~Stage 11 phase results~~ — **DONE**: §9. All PIT phases collapse (P0 14.54 / P5 16.05 / P10 7.52 / P15 9.40), all buy gates pass.
- Binary-factory unknown-param guard (latent warn above).
- Consider single-name attribution / tail-cap as the residual-alpha hazard (top-10 = 91.7% of PIT net) — a portfolio-path problem, **not** a PIT-correctness one.

---

*Stage 12 (2×2 bias decomposition) explicitly skipped per user decision (2026-08-21). Ensemble (ens3/ens5) untouched until this audit is fully closed.*
