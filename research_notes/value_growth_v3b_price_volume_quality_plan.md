# v3-b Price-Volume Quality Feature Plan

> 在 delayed 180d label maturity 验证框架下，新增量价质量特征尝试提升 v3a_full 信号。

## 背景

PR #181 确认了 180d label maturity leakage 的问题。修正后 v3a_full delayed IC=0.0777（ICIR=0.837）。本实验目标是将 IC 推向 0.10+，在 delayed 真实口径下验证量价质量特征的增量价值。

## 为什么选量价质量，而不是普通技术指标

普通技术指标（MACD/KDJ/RSI/BOLL）在长周期（120-180d）上有两个问题：
1. 信号频率太高，与 180d 标签对齐差
2. 在截面模型上大量指标互相冗余

v3b 特征设计原则：
- **趋势质量**：不是回归收益，而是收益是如何实现的（低波动上行、一致性）。
- **成交量质量**：不是绝对值，而是量与价的配合质量（缩量整理、温和放量突破）。
- **交互特征**：只有与 v3a 已有信号（筹码集中、杠杆确认）相关的才加入。

## 特征定义

### Trend Quality（8 个）

| 特征 | 定义 | 信号逻辑 |
|------|------|---------|
| `trend_consistency_60d` | 过去 60d 正收益天数占比 | 稳定的上行趋势 |
| `trend_consistency_120d` | 过去 120d 正收益天数占比 | 中期趋势可靠性 |
| `low_vol_uptrend_60d` | ret_60d / realized_vol_60d | 低波动下的平稳上行 |
| `low_vol_uptrend_120d` | ret_120d / realized_vol_120d | 同上，120d 窗口 |
| `return_drawdown_ratio_60d` | ret_60d / abs(max_dd_60d) | 风险调整后的回报效率 |
| `return_drawdown_ratio_120d` | ret_120d / abs(max_dd_120d) | 同上，120d 窗口 |
| `pullback_recovery_speed_60d` | (close - 60d_low) / (60d_high - 60d_low) | 从低点反弹的充分性 |
| `new_high_persistence_120d` | 120d 内 close 在 95% 新高内的天数占比 | 持续贴近新高而非一日游 |

### Volume Quality（6 个）

| 特征 | 定义 | 信号逻辑 |
|------|------|---------|
| `up_volume_down_volume_ratio_60d` | 上涨日成交额 / 下跌日成交额 | 上涨有量下跌缩量 |
| `up_volume_down_volume_ratio_120d` | 同上，120d 窗口 | 中期量价配合 |
| `volume_contraction_after_rise_60d` | 上涨后 10d 成交额趋势下降 | 缩量整理不追高 |
| `quiet_accumulation_60d` | 上涨 + 成交额波动下降 | 静默吸筹 |
| `amount_stability_60d` | -CV(amount)，越高越稳定 | 排除异常尖峰 |
| `breakout_volume_quality_120d` | 接近 120d 新高 & 成交量适中 | 突破质量 |

### Interaction Features（5 个）

| 特征 | 定义 |
|------|------|
| `holder_concentration_trend_confirm` | holder_concentration_score × max(zscore(trend_consistency_120d), 0) |
| `holder_concentration_low_vol_uptrend` | holder_concentration_score × max(zscore(low_vol_uptrend_120d), 0) |
| `holder_concentration_volume_contract` | holder_concentration_score × max(zscore(volume_contraction_after_rise_60d), 0) |
| `margin_holder_trend_confirm` | margin_trend_confirm_score × max(zscore(holder_concentration_score), 0) |
| `margin_pullback_recovery_confirm` | margin_trend_confirm_score × max(zscore(pullback_recovery_speed_60d), 0) |

## PIT / Leakage Policy

所有特征使用 `pct_change(60)`、`rolling(120).max()` 等历史窗口。
无任何 shift(-N) 或 forward-looking 引用。
截面 zscore 只在当日分组内。

## Configurations

| Config ID | Feature Set | Feature Count | Feature Flags |
|-----------|------------|:------------:|---------------|
| `abl_full_v3b_pv_delayed180` | v3a full + v3b pv | 97 | margin + shareholder + pv |
| `abl_full_v3b_pv_interact_delayed180` | v3a full + v3b pv + v3a×v3b | 102 | margin + shareholder + pv + interaction |

## Results (strict delayed-180 after bugfix)

| Variant | Features | Delayed IC | Delayed ICIR | ΔIC vs v3a_full |
|---------|:-------:|:---------:|:-----------:|:---------------:|
| v2 baseline | 64 | 0.0529 | 0.495 | −0.0348 |
| v3a_full | 83 | **0.0877** | 1.009 | — |
| v3b_pv | 97 | **0.0885** | 1.015 | +0.0008 |
| v3b_pv_interact | 102 | **0.0894** | 1.028 | +0.0017 |

Note: initial run had two bugs (cross-stock rolling contamination, Series-as-column in groupby) that caused v3b_pv_interact IC=0.0217 (buggy). After fix, IC recovers to 0.0894 — still flat vs baseline.

### Pass/Fail: FAIL

- v3b_pv: +0.0008 IC — negligible, does not meet Weak Pass (+0.01).
- v3b_pv_interact: +0.0017 IC — negligible after bugfix.
- Direction closed. Code/configs preserved for reproducibility.

## Pass/Fail 标准

### Strong Pass
- v3b_pv delayed IC >= v3a_full delayed + 0.02
- ICIR 不下降
- majority years positive

### Weak Pass
- v3b_pv delayed IC >= v3a_full delayed + 0.01
- top50/top20 明显改善

### Fail
- IC 无提升或 ICIR 明显下降

## 下一步

取决于实验结果：
1. 如果 Pass → 进入 shadow candidate tracking 评估
2. 如果 Fail → 关闭 v3b 方向，专注缩短预测周期
