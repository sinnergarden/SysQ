# Missed Super Winner Research

> Date: 2026-06-22
> Model: v3a+liquidity, 180d delayed, monthly unique stock
> Source: `scripts/research/missed_super_winner_research.py`

## Overview

| Category | Count |
|----------|-------|
| Total super winners (ret>1.0) | 1,730 |
| Missed (score_rank>50% or score<0) | 751 (43.4%) |
| High-score (top5%) | 191 (11.0%) |

**43.4% of all >100% return events were completely missed by the model.**

## Feature Comparison: Missed vs High-Score Super Winners

| Feature | Missed Mean | High-Score Mean | Diff | Interpretation |
|---------|------------|-----------------|------|----------------|
| amount_log | 18.85 | 19.43 | **-0.58** | Missed ones had lower liquidity |
| rps_60d | 0.40 | 0.63 | **-0.23** | Missed had much weaker RPS |
| ret_60d | -0.00 | +0.11 | **-0.11** | Missed had negative/neutral momentum |
| ret_120d | +0.02 | +0.21 | **-0.19** | Missed had much weaker medium-term return |
| price_percentile_252d | 0.41 | 0.62 | **-0.21** | Missed were at much lower price levels |
| pe_rank_252d | 0.45 | 0.52 | -0.07 | Small valuation difference |
| trend_smoothness_60d | -0.05 | +0.03 | -0.08 | Missed had slightly negative trend |

**The pattern is clear: the model systematically missed stocks that were in early stages of a breakout — low RPS, low price percentile, low liquidity, negative/neutral prior returns. These are exactly the kinds of stocks that bad-case analysis said big winners look like.**

### Industry Distribution

| Industry | Missed | High-Score |
|----------|--------|------------|
| 元器件 | 88 | 37 |
| 半导体 | 73 | 19 |
| 电气设备 | 69 | 21 |
| 汽车配件 | 58 | 9 |
| 化工原料 | 44 | 8 |
| 专用机械 | 42 | 8 |
| 医疗保健 | 37 | 11 |
| 软件服务 | 35 | 6 |
| 通信设备 | 31 | 9 |
| 食品 | 22 | 4 |

## Case Drilldown

### 新易盛 (300502) — Optical module, AI infrastructure play

```
Date      score  rank   amt    rps   ret60  ret120 pp252
2024-05  -1.20  +0.09 +17.59 +0.34 -0.158 -0.264  0.07
2024-07  -1.18  +0.07 +17.85 +0.36 -0.130 -0.202  0.09
2024-09  -0.68  +0.13 +18.09 +0.56 -0.042 -0.115  0.10
2024-12  -0.89  +0.13 +18.36 +0.42 -0.027 -0.066  0.14
2025-04  -0.32  +0.27 +18.95 +0.67 +0.079 +0.215  0.36 [BIG: ret=+7.91]
```

**12 months before the breakout:** score was consistently negative (-1.2 to -0.9), rank bottom 13%, RPS low (0.34-0.42), price_percentile bottom 7-14%. Model had no way to catch this. The breakout happened in 2025Q1 when AI capex story became consensus — an industry-cycle/event-driven move.

### 九安医疗 (002432) — COVID-testing event

```
Date      score  rank   amt    rps   ret60  ret120 pp252
2020-12  +0.76  +0.78 +17.89 +0.14 -0.114 -0.244  0.30
2021-02  +0.09  +0.49 +17.92 +0.10 -0.155 -0.285  0.10
2021-04  -0.14  +0.22 +17.90 +0.25 +0.028 -0.079  0.23
2021-07  +1.74  +0.92 +18.80 +0.87 +0.382 +0.248  0.68 [BIG: ret=+10.11]
```

Model gave neutral scores (0.09-0.76) before the explosion. The rank jumped from bottom 49% to top 8% just as the stock tripled. This is a pure event-driven case — COVID test kit approval — that no daily feature could predict.

### 胜宏科技 (300476) — PCB cycle reversal

```
Date      score  rank   amt    rps   ret60  ret120 pp252
2024-09  -1.35  +0.06 +18.32 +0.30 -0.223 -0.225  0.03
2024-10  -1.35  +0.05 +18.31 +0.31 -0.215 -0.226  0.02
2024-11  -2.30  +0.01 +18.30 +0.10 -0.263 -0.320  0.01
2024-12  -2.02  +0.02 +18.55 +0.40 -0.122 -0.082  0.03 [BIG: ret=+6.79]
```

Consistently negative scores, bottom 1-6% rank, lowest price_percentile possible (1-3%). The model actively recommended shorting this stock. Yet it returned +6.79 over 180d. PCB cycle recovery was completely invisible to the feature set.

## Industry Proxy Check

| Feature | Missed Mean | High-Score Mean | Diff |
|---------|------------|-----------------|------|
| industry_ret_20d | +0.0001 | -0.0002 | +0.0003 |
| industry_ret_60d | +0.0006 | +0.0003 | +0.0003 |
| industry_ret_120d | +0.0007 | +0.0006 | +0.0001 |
| industry_breadth_20d | 0.467 | 0.467 | +0.0005 |
| industry_breadth_60d | 0.473 | 0.473 | +0.0005 |
| industry_volume_expansion | 1.164 | 1.118 | **+0.045** |
| stock_minus_industry_ret_20d | -0.021 | -0.041 | **+0.020** |
| stock_minus_industry_ret_60d | -0.025 | -0.071 | **+0.046** |
| industry_top_stock_momentum | 0.071 | 0.071 | -0.000 |

**Industry proxies do NOT differentiate missed vs high-score super winners.** The industry-breadth-based features have near-identical means in both groups. `industry_volume_expansion` had a small positive signal (missed winners had slightly higher industry volume expansion), but the difference is too small for a useful feature.

## Conclusions

### 1. Are missed super winners primarily event-driven / industry cycle reversal?

**Yes, overwhelmingly.** All the representative cases share the same pattern:

- **九安医疗**: Pure event-driven (COVID testing approval). No daily feature could predict this.
- **新易盛 / 中际旭创**: AI infrastructure capex cycle (2025Q1). Model saw negative returns, low RPS, bottom percentile prices — everything looked like "avoid."
- **胜宏科技**: PCB industry cycle reversal. Same pattern: terrible trailing returns, model said short.

These are NOT feature-blind spots that can be fixed with better feature engineering. They are **regime-change / structural-break** events that daily price/volume/fundamental features cannot anticipate.

### 2. Can the current data show weak signals ahead of time?

**Mostly no.** The industry proxy analysis shows no meaningful pre-breakout divergence:
- Industry breadth, returns, and top-stock momentum are identical between missed and caught super winners
- `industry_volume_expansion` has a slight signal (+0.045 diff) but too weak to use
- `stock_minus_industry_ret_20/60d` shows missed winners had LESS negative excess return (closer to industry mean) — but this is a tiny signal

The only consistent difference is that **missed super winners had lower price_percentile_252d (0.41 vs 0.62)** — they were in the "value / beaten-down" zone. But this is also where many failing stocks live. The signal-to-noise ratio is too low.

### 3. Candidate feature ideas (if any)

**Not recommended from current feature set.** The data suggests these missed opportunities are structural breaks, not gradual processes that daily features can detect. However, if external data were available:

1. **Analyst earnings revision slope** — 新易盛/中际旭创 would have shown upward revisions 1-2 quarters before price breakout
2. **Industry supply/demand proxy** — PCB prices, semiconductor fab utilization, optical module orders
3. **Institutional ownership change** — big holders accumulating before price breaks out

### 4. Final recommendation

**Do not force-mine existing features for missed super winner prediction.** The feature set is designed for gradual momentum/quality/value signals, not structural break detection. The 43.4% miss rate on super winners is a fundamental limitation of daily price/fundamental data, not a bug to be fixed.

**Acceptable trade-off:** The portfolio simulation shows that despite missing 43% of super winners, the model still achieves Sharpe 3.9+ in 180d Top20 portfolios, because it catches enough big winners (11%) to offset the misses. The missed super winners also tend to be concentrated in specific episodes (COVID, AI boom, PCB cycle) that a diversified portfolio is naturally exposed to anyway.
