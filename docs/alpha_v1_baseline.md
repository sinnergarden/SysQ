# Alpha V1 — Baseline Version (Production Candidate)

## Strategy Identity

| Field | Value |
|-------|-------|
| ID | `qsys_alpha_v1_blend20_weekly_top20_buffer` |
| Status | **Production candidate** (shadow trading) |
| Universe | CSI 300 / CSI 800 |
| Horizon | Weekly rebalance |
| Max Positions | 20 |
| Initial Cash | ¥500,000 (shadow) |

---

## Model Ensemble

### Dual LightGBM

| Component | Horizon | Weight | Label |
|-----------|---------|--------|-------|
| `clean_5d` | 5 trading days | 0.8 | zscore(fwd_5d_return) |
| `clean_20d` | 20 trading days | 0.2 | zscore(fwd_20d_return) |

### Scoring

```
blended_score = 0.8 × zscore(pred_5d) + 0.2 × zscore(pred_20d)
```

- Both models are retrained every week on a **rolling 2-year window**
- Training data: all stocks in the universe, features = 132 clean features
- Labels are cross-sectional zscored within each trading date
- No sector neutralization at the model level

### Features

- **Clean features** (~132): All features except Harmful groups (Fundamental, VolumeAmt, Valuation, Margin, PricePattern)
- Harmful groups removed because they showed negative or unstable IC across the 2022-2026 test period
- Robust zscore normalization: `(x - median) / median_abs_dev`, clipped to [-3, 3]

---

## Portfolio Construction Rules

### Selection

1. Score all stocks in the universe with `blended_score`
2. Hold current positions if their rank ≤ 60 (buffer hold)
3. Fill remaining slots from unheld stocks with rank ≤ 40 (buffer buy)
4. Target portfolio size: **20 stocks**

### Weighting

- Linear rank decay: `w_i = (N - rank_i + 1) / sum(1..N)`
- Single stock cap: **7%** of NAV
- Excess from capped positions redistributed proportionally
- Weights normalized to sum to 1.0

### Execution

| Parameter | Value |
|-----------|-------|
| Commission | 0.03% |
| Stamp Duty | 0.1% |
| Slippage | 0.1% |
| Min Commission | ¥5 |
| Execution Price | Open price of rebalance day |
| Limit-up/down | Orders skipped (not cancelled; re-evaluated next bar) |
| Suspension | Orders skipped |

---

## Backtest Performance (2024-01 -- 2026-05, 545 trading days)

### CSI 300

| Metric | Value |
|--------|-------|
| Total Return | +152.04% |
| Annual Return | +53.33% |
| Sharpe | 1.771 |
| Max Drawdown | -16.12% |
| Calmar | 3.309 |
| Annual Turnover | 35.8x |
| Total Fees | ¥1,138,128 |
| Weeks | 118 (win rate 44.1%) |
| Best Week | +14.71% |
| Worst Week | -11.22% |

### CSI 800

| Metric | Value |
|--------|-------|
| Total Return | +257.32% |
| Annual Return | +80.19% |
| Sharpe | 2.207 |
| Max Drawdown | -20.84% |
| Calmar | 3.848 |
| Annual Turnover | 58.9x |
| Total Fees | ¥2,342,017 |
| Weeks | 118 (win rate 54.2%) |
| Best Week | +16.34% |
| Worst Week | -8.44% |

### Signal Quality (CSI 800, test period)

| Metric | Value |
|--------|-------|
| Mean IC | 0.039 |
| Mean RankIC | 0.054 |
| ICIR | 0.305 |
| RankICIR | 0.404 |
| Group 1 NAV (top quintile) | 2.439 |
| Group 5 NAV (bottom quintile) | 1.496 |

*CSI 800 is the primary production universe. CSI 300 is maintained for cross-validation.*

---

## Monitoring Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| 60d Rolling RankIC | < 0.01 | < 0.00 |
| 20d Excess Return | < -5% | — |
| 60d Excess Return | — | < -8% |
| Max Drawdown | < -15% | < -20% |
| Feature Missing Rate | > 5% | — |
| Failed Trade Rate | > 10% | — |

---

## Deployment

### Schedule

| Task | Time | Trigger | Status |
|------|------|---------|--------|
| CSI 800 Data Sync | 21:30 daily | `qsys-csi800-daily-sync.timer` | ✅ Active |
| Alpha V1 Preopen | 08:00 trading day | `qsys-alpha-v1-preopen.timer` | ✅ Active |
| Research UI | 8000/tcp | `uvicorn` (manual) | ✅ Running |

### Status Dashboard

| Check | Value |
|-------|-------|
| Research UI | `http://localhost:8000` — 2 backtest runs available |
| Last CSI800 Sync | 2026-05-15 (success, 49s CPU) |
| Next Sync | Today 21:30 |
| Next Preopen | Tomorrow 08:00 |
| Active Universe | CSI 800 (shadow, ¥500k) |
| Models | `clean_5d` + `clean_20d` dual LightGBM, retrained weekly |
| 0-cost Curve | Available in UI diagnostics (zero_cost_total_assets) |
| Benchmark | CSI 300 avg price (equal-weighted universe) |

### Shadow Trading Flow

1. **21:30** — CSI 800 data sync + readiness audit → Telegram notification
2. **08:00** — Alpha V1 preopen:
   - Load CSI 800 data
   - Train dual models (2yr rolling)
   - Score & blend
   - Build portfolio with alpha_v1 rules
   - Generate shadow orders
   - Send Telegram: prediction summary + buy plan
3. **09:30** — Market open (A-share)
4. **15:00** — Market close
5. **15:30** — Post-close report:
   - Actual positions vs plan
   - P&L summary
   - Signal quality update

---

## Risk Notes

- **Single stock 7% cap** prevents concentrated blowup but may cause ~0.5% tracking error vs strict rank-weight
- **Buffer rules** reduce turnover in non-trending markets; in strong trend regimes the full 20 may lag by ~1 day
- **No short selling** — the strategy is long-only; the zero-cost equity curve is for diagnostics only
- **The 20d model** contribution is regime-dependent; it adds ~0.5% to annual return in normal markets but absorbs volatility
