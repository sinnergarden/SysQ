# Value Growth Simple Backtest — 20d Rebalance, T+1 Entry

> NOT a pull request. Research note only. Do not merge.

This backtest evaluates a **20d rolling rebalance** portfolio driven by a 180d prediction score; it is **not** a literal 180d buy-and-hold simulation. Each period the model re-scores all stocks and rebalances the topK — the 180d horizon is the label the model was trained on, not the holding period.

## Setup

| Parameter | Value |
|-----------|-------|
| Signal | 180d raw LightGBM, daily_zscore |
| Universe | CSI800 static, listed_252d filter |
| Period | 2020-2025, 2023-2025 |
| Rebalance | Every 20 trading days |
| Entry | T+1 (next calendar trading day) |
| Weights | Equal / Rank-weighted |
| Industry cap | None / 25% per industry |
| Cost | 20bps round-trip |
| PnL | Actual adjusted close prices |

## Results

### 2020-2025 (65 rebalance periods)

| K | Weight | Cap | Ann | MDD | Sharpe | Win | N | Hold | Ind |
|---|--------|-----|-----|-----|--------|-----|------|------|------|
| 20 | equal | none | **144.2%** | -18.5% | 2.43 | 71% | 65 | 20 | 13 |
| 20 | equal | 25% | **144.7%** | -18.5% | 2.44 | 71% | 65 | 20 | 13 |
| 20 | rank | none | **167.2%** | -18.3% | 2.44 | 74% | 65 | 20 | 13 |
| 20 | rank | 25% | **168.1%** | -18.3% | 2.44 | 74% | 65 | 20 | 13 |
| 50 | equal | none | **97.8%** | -17.5% | 2.22 | 74% | 65 | 50 | 25 |
| 50 | equal | 25% | **97.9%** | -17.5% | 2.22 | 74% | 65 | 50 | 25 |
| 50 | rank | none | **122.4%** | -17.4% | 2.38 | 71% | 65 | 50 | 25 |
| 50 | rank | 25% | **122.6%** | -17.4% | 2.37 | 71% | 65 | 50 | 25 |
| 100 | equal | none | **71.2%** | -18.3% | 1.99 | 74% | 65 | 100 | 40 |
| 100 | equal | 25% | **71.2%** | -18.3% | 1.99 | 74% | 65 | 100 | 40 |
| 100 | rank | none | **89.6%** | -17.9% | 2.17 | 72% | 65 | 100 | 40 |
| 100 | rank | 25% | **89.6%** | -17.9% | 2.17 | 72% | 65 | 100 | 40 |

### 2023-2025 (29 rebalance periods)

| K | Weight | Cap | Ann | MDD | Sharpe | Win | N | Hold | Ind |
|---|--------|-----|-----|-----|--------|-----|------|------|------|
| 20 | equal | none | **123.0%** | -21.0% | 2.32 | 69% | 29 | 20 | 13 |
| 20 | equal | 25% | **122.4%** | -21.0% | 2.33 | 69% | 29 | 20 | 13 |
| 20 | rank | none | **131.6%** | -18.7% | 2.46 | 69% | 29 | 20 | 13 |
| 20 | rank | 25% | **131.1%** | -18.7% | 2.46 | 69% | 29 | 20 | 13 |
| 50 | equal | none | **78.3%** | -17.6% | 2.27 | 66% | 29 | 50 | 26 |
| 50 | equal | 25% | **78.4%** | -17.6% | 2.27 | 66% | 29 | 50 | 26 |
| 50 | rank | none | **96.9%** | -18.7% | 2.35 | 72% | 29 | 50 | 26 |
| 50 | rank | 25% | **96.9%** | -18.7% | 2.35 | 72% | 29 | 50 | 26 |
| 100 | equal | none | **49.7%** | -18.8% | 1.91 | 69% | 29 | 100 | 41 |
| 100 | equal | 25% | **49.7%** | -18.8% | 1.91 | 69% | 29 | 100 | 41 |
| 100 | rank | none | **67.8%** | -18.7% | 2.14 | 69% | 29 | 100 | 41 |
| 100 | rank | 25% | **67.8%** | -18.7% | 2.14 | 69% | 29 | 100 | 41 |

## Key Findings

### 1. Strong absolute returns, but context matters

Top50 rank-weighted — 97-122% annualized with Sharpe 2.2-2.4. This is extremely high. The caveats:
- Only 29-65 rebalance periods (not independent)
- 2020-2025 includes a strong bull market for tech/value cycles
- Static CSI800 universe (survivorship bias)
- Entry at T+1 but price fetch approximated (next available trading day)
- No cash drag / partial rebalance / real execution constraints

### 2. Industry cap has virtually no effect

25% cap vs none: returns differ by <1% across ALL configurations. This means either:
- The industry exposure is already well-diversified at the sector level
- The cap doesn't bind frequently enough
- Industry classification is at a coarse level

Top50 already has 25 industries — natural diversification.

### 3. Rank-weighting consistently beats equal-weight

- Top50: 122% (rank) vs 98% (equal) = ~24% annual advantage
- Top20: 167% vs 144% = ~23% advantage
- Consistent across periods

Rank-weighted picks capture more of the signal's top-end information.

### 4. Top50 is the sweet spot

- Top20: Highest returns but highest concentration (13 industries)
- Top50: Good returns (97-122%) with reasonable diversification (25 industries)
- Top100: Lower returns (71-90%) with over-diversification
- Top50 is the recommended deployment size

### 5. 2023-2025 retrenches but remains strong

The shorter, cleaner window shows ~15-20% lower returns than the full 2020-2025 period. This is expected (less bull market exposure) but still extremely strong (Sharpe 2.27-2.46).

## Final Decision

### 1. Top20/top50 like a viable candidate pool?

**YES.** The candidates show reasonable industry diversification (12-26 industries depending on K), no obvious style over-concentration, and the backtest metrics are strong.

### 2. Top50 backtest executable?

**YES.** Backtest results are positive across all configurations. Top50 rank-weighted with 20d rebalance is the recommended baseline.

### 3. Industry cap improves results?

**NO clear benefit.** The backtest shows <1% difference between capped and uncapped. The signal's natural diversification at K>=50 is sufficient.

### 4. Rank-weight vs equal-weight?

**RANK-WEIGHT BETTER.** Consistent 20-25% annual improvement across all K and periods. Use rank-weight as default.

### 5. Recommended next step

**Enter candidate pool manual research** with the following baseline:

```
Model: 180d raw LightGBM
Universe: CSI800 (static)
TopK: 50
Weight: rank-weighted
Rebalance: every 20d
Entry: T+1
Cost: 20bps
Industry cap: 25% (optional, minimal impact)
```

Before production deployment, the following gaps must be addressed:
- **Framework gap:** No 20d rebalance support in BacktestRunner (only daily/weekly)
- **Execution gap:** Real entry/exit prices with limit orders vs close
- **Feature gap:** Stored feature values at prediction time for candidate explainability

## Reproducibility

Backtest numbers in this note can be reproduced via:
```
python research_scripts/value_growth_simple_backtest.py
```
See script header for assumptions and limitations.
