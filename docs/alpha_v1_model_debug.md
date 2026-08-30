# Alpha V1 — Model Debug Conclusions

## Overview

Alpha V1 uses a dual-LightGBM ensemble to produce weekly stock rankings for the CSI 300 / CSI 800 universe. The pipeline went through four phases of signal diagnostics, feature screening, and stress testing before converging on the production configuration.

---

## Phase 1: Feature Quality Audit

### Full Feature Set
- Source: `FeatureLibrary.get_semantic_all_features_config()` (~250 raw features)
- Covers: Size, Valuation, Fundamental, Momentum, Price/Volume, Dollar Volume, Margin, Price Pattern, Correlation, Industry

### Feature Stability
- Many raw features had >30% missing rates in early samples (IPO stub period, suspension)
- Forward-filled and zero-imputed after robust zscore normalization
- Industry assignment via SQLite `stock_basic` table (Tushare classification)

### Harmful Groups Identified

| Group | Reason for Exclusion |
|-------|---------------------|
| **Fundamental** | Cross-sectional IC consistently negative; multi-collinear with Size |
| **VolumeAmt** | Short-term mean-reversion signal; negative IC at 5d horizon |
| **Valuation** | Pe/TTM/PB ratios dominated by outliers; unstable zscore after winsorization |
| **Margin** | Sparse coverage (<40% of universe on any given day); unreliable gradients |
| **PricePattern** | Overfit to historical patterns; out-of-sample IC degradation >60% |

### Clean Feature Set
- After excluding harmful groups: **132 features** retained
- Remaining groups: Size (market-cap terms retained cautiously), Momentum, Price/Volatility (pure std-based), Dollar Volume (signed), Correlation

---

## Phase 2: Horizon Selection

### Tested Horizons
- **1d**: Noise-dominated IC (<0.02), high turnover, not usable
- **5d** (selected as primary): Best IC/ICIR trade-off; RankIC 0.03--0.06 on validation
- **10d**: Comparable IC but lower IR due to fewer independent test periods
- **20d** (selected as secondary): Lower IC but more stable; acts as trend regularizer
- **60d**: IC decays to near-zero; excessive lookahead in regime-change periods

### Dual-Horizon Rationale
- `clean_5d` captures short-term reversal/momentum signals
- `clean_20d` captures medium-term trend structure
- Blend ratio **0.8 : 0.2** (zscore-normalized before blending) was chosen via grid search over {0.6, 0.7, 0.8, 0.9, 1.0} with 20d weight at {0.1, 0.2, 0.3, 0.4}
- 0.8/0.2 maximized out-of-sample RankIC while reducing the 5d model's worst-percentile drawdown by ~40%

---

## Phase 3: Model Architecture

### LightGBM Hyperparameters (Bayesian Optimization Result)

```python
{
    "objective": "regression",
    "metric": "mse",
    "colsample_bytree": 0.8879,
    "learning_rate": 0.0421,
    "subsample": 0.8789,
    "lambda_l1": 205.7,
    "lambda_l2": 580.98,
    "max_depth": 8,
    "num_leaves": 210,
    "num_threads": 8,
    "seed": 42,
}
```

- **Early stopping**: 20 rounds, validation set = last 15% of training data
- **Max trees**: 200 (early stopping typically fires at 120--180)
- **Robust zscore** normalization: median/median-absolute (not mean/std) to reduce outlier leverage

### Label Construction
- Cross-sectional zscore of `fwd_return = close[t+h] / close[t] - 1` within each trade date
- This produces a **rank-normalized** target: top decile ≈ +1.5σ, bottom decile ≈ −1.5σ
- No sector neutralization applied at the label level (sector is a soft constraint in portfolio construction)

---

## Phase 4: Stress Testing

### Parameter Robustness (Random Search)
- Learning rate ±20%: Sharpe variation <0.08
- Num leaves {128--256}: Sharpe variation <0.12
- Subsample {0.7--1.0}: Sharpe variation <0.05
- Conclusion: The model is **not brittle** around the chosen hyperparameters

### Regime Sensitivity
- **2022 bear market**: 5d model RankIC dropped to ~0.01; 20d model maintained ~0.03; blend sustained positive excess
- **2023 recovery**: 5d model RankIC recovered to ~0.04; blend benefited from both signals
- **2024 Q1 micro-crash**: Both models degraded but recovered within 2 windows
- Conclusion: Dual-model blend provides meaningful downside protection vs either model alone

### Feature Importance Stability
- Top-20 features by gain have ~70% overlap between consecutive retraining windows
- `Slope` (momentum), `std` (volatility), and size-related features consistently dominate
- No single feature ever exceeds 8% total gain → well-diversified signal

---

## Key Takeaways

1. **Harmful groups removal** was the single largest improvement (~0.35 Sharpe gain)
2. **Dual-horizon blend** added ~0.15 Sharpe with ~0.02 MaxDD reduction
3. **Buffer rules** (hold60/buy40) reduced turnover by ~40% vs full-rebalance with <0.02 Sharpe cost
4. **No sector neutralization needed** — industry-aware soft-weighting in portfolio construction handles concentration
5. **The 20d model is a volatility dampener**, not a return driver — its contribution is most visible in high-vol regimes

---

## Cross-Universe Validation

| Metric | CSI 300 | CSI 800 |
|--------|---------|---------|
| Total Return | +152% | +257% |
| Sharpe | 1.771 | 2.207 |
| Max Drawdown | -16.1% | -20.8% |
| Calmar | 3.31 | 3.85 |
| Weekly Win Rate | 44.1% | 54.2% |

The CSI 800 outperformance is consistent with its broader coverage (mid-cap exposure premium) and higher diversification. The RankIC is similar across both universes (~0.05), confirming the signal transfers well.

## Production Path

1. ✅ Dual-universe backtest validated (CSI 300 + CSI 800)
2. ✅ CSI 800 daily data pipeline automated
3. ✅ Alpha V1 preopen systemd timer deployed
4. ⏳ Shadow trading starts: next trading day 08:00
5. ⏳ Post-close reconciliation (pending deployment)
6. ⏳ 1-week trial run → full production if stable
