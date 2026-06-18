# 60d Delayed Feature Audit — Timing / Rebalance Signal

> 评估已有 feature group 对 fwd_ret_60d_raw 在 strict delayed-60 口径下的解释力。
> 目标：确认 60d 是否适合作为中期交易时机 / 双周调仓信号。

## 为什么需要 60d delayed validation

PR #181 已确认 forward-return supervised model 必须使用 label maturity delay。
60d label 的 maturity lag = 60 交易日，比 180d 标签对训练数据新鲜度的损耗小得多。

## 方法论

| 维度 | 设置 |
|------|------|
| 回测区间 | 2020-01-01 ~ 2025-12-31 |
| 评估口径 | Strict 20d vintage eval（每 20 交易日取 1 点） |
| 窗口数 | 67 |
| 训练窗口 | 504 交易日 |
| 步长 | 20 交易日 |
| 模型 | LightGBM, 300 estimators, early stopping 20 |
| 标签 | fwd_ret_60d_raw（前复权，无截面标准化） |
| 延迟 | label_maturity_lag_trading_days: 60 |

## 结果

### 总表

| Variant | 特征数 | IC | ICIR | RankIC | RankICIR | ΔIC vs v3a_full |
|---------|:-----:|:--:|:---:|:-----:|:-------:|:---------------:|
| v2 baseline | 64 | 0.0335 | 0.339 | 0.0329 | 0.270 | −0.021 |
| +margin | 73 | 0.0453 | 0.459 | 0.0418 | 0.356 | −0.009 |
| +shareholder | 74 | 0.0435 | 0.526 | 0.0413 | 0.418 | −0.011 |
| **v3a_full** | **83** | **0.0545** | **0.644** | **0.0485** | **0.493** | **—** |
| existing pv only (26 old) | 26 | 0.0032 | 0.032 | -0.0064 | -0.052 | −0.051 ❌ |
| +v3b_pv | 97 | 0.0537 | 0.640 | 0.0474 | 0.485 | −0.001 |
| full pv (81 broad) | 81 | −0.0100 | −0.077 | −0.0161 | −0.113 | −0.065 ❌ |
| structured pv (35 curated) | 35 | 0.0244 | 0.221 | — | — | −0.030 ❌ |
| v3a_full + structured pv | 98 | 0.0521 | 0.575 | — | — | −0.002 |

### v3a_full 年份表（strict 20d）

| Year | IC | Eval Days |
|:---:|:--:|:---------:|
| 2020 | −0.0165 | 8 |
| 2021 | 0.0339 | 12 |
| 2022 | 0.0437 | 12 |
| 2023 | **0.0879** | 12 |
| 2024 | 0.0436 | 12 |
| 2025 | 0.0618 | 11 |

## 分析

### 1. 哪些 feature group 对 60d 有增益

- **margin 特征**（+0.012 IC, +35% ICIR）：两融杠杆信号对 60d 短期反转有一定捕捉力。
- **shareholder 特征**（+0.010 IC, +55% ICIR）：筹码集中度在短周期信号更稳定（ICIR 是 margin 的 1.15 倍）。
- **v3a_full 综合**（+0.021 IC, +90% ICIR）：两组叠加有正交互作用。

### 2. 哪些 feature group 无效或负贡献

- **existing pv only（26 个旧量价）**：IC=0.003，零预测力。
- **full pv（81 个全量量价）**：IC=-0.010，负贡献。包括 microstructure/liquidity/tradability/relative_strength/v3b 全部 PV 组。
- **structured pv（35 个精选量价）**：IC=0.024，远低于 baseline。
- **v3a_full + structured pv**：IC=0.052，低于 v3a_full 单独（0.055）。PV 特征在 v3a_full 上无增量。
- **+v3b_pv**：IC=0.054，与 v3a_full 持平（Δ=-0.001）。v3b 方向关闭结论不变。

**结论：纯量价特征对 60d 前向收益无预测力。全量 PV（81 feat）、精选 PV（35 feat）、旧量价（26 feat）全部失败。量价 + v3a_full 无增量。60d feature mining 方向关闭。**

### 3. 60d 与 180d 的角色划分

| 维度 | 180d（candidate pool） | 60d（timing / rebalance） |
|------|:---------------------:|:------------------------:|
| IC（delayed） | ~0.08 | ~0.05 |
| ICIR | ~0.84 | ~0.64 |
| 最佳特征 | 全量 v3a + shareholder | 全量 v3a |
| 信号类型 | 筹码+基本面趋势 | 杠杆+筹码 |
| 适用场景 | 月度调仓候选池 | 双周调仓时机 |

### 4. Pass/Fail 判断

- **Weak Pass：已通过**（IC=0.0545 > 0.05, ICIR=0.64 > 0.5, majority years positive）。
- **Strong Pass：未通过**（IC < 0.08, ICIR < 0.8）。
- **结论：60d 可作为 timing/rebalance 的辅助信号，但不单独作为主信号。**

### 5. 下一步建议

1. **暂时关闭 60d feature mining**，不建议继续堆量价 feature。
2. 下一步应转向：
   - 180d candidate pool（已有方向）
   - 60d timing/rebalance（作为辅助信号）
   - signal-combination / TopK strategy validation

## 文件

- `configs/research/60d/abl_v2_baseline_delayed60.yaml` — v2 baseline
- `configs/research/60d/abl_v3a_full_delayed60.yaml` — v3a full
- `configs/research/60d/abl_v3a_margin_delayed60.yaml` — v2 + margin
- `configs/research/60d/abl_v3a_shareholder_delayed60.yaml` — v2 + shareholder
- `configs/research/60d/abl_price_volume_existing_delayed60.yaml` — existing pv only (26 feat)
- `configs/research/60d/abl_v3b_pv_delayed60.yaml` — v3a + v3b (97 feat)
- `configs/research/60d/abl_60d_pure_full_price_volume_delayed60.yaml` — full pv (81 feat)
- `configs/research/60d/abl_60d_pure_structured_price_volume_delayed60.yaml` — structured pv (35 feat)
- `configs/research/60d/abl_60d_v3a_full_plus_structured_pv_delayed60.yaml` — v3a + structured pv (98 feat)
- `configs/features/value_growth_existing_price_volume.yaml` — 旧量价 (26 feat)
- `configs/features/value_growth_60d_full_price_volume_features.yaml` — 全量价 (81 feat)
- `configs/features/value_growth_60d_structured_price_volume_features.yaml` — 精选量价 (35 feat)
- `configs/features/value_growth_60d_v3a_full_plus_structured_pv_features.yaml` — v3a+精选量价 (98 feat)
- `tests/research/test_60d_configs_smoke.py` — 6+4 configs parse test

## 结论

| 判断 | 结果 |
|:---|:----:|
| 60d 可作为 timing/rebalance 信号？ | **Weak Pass — 可作辅助信号** |
| 推荐单独使用？ | ❌ 不推荐 |
| 推荐 60d+180d 组合？ | ✅ 建议下一步验证 |
| 追加量价特征有效？ | ❌ 全部失败 |
| 60d feature mining 继续？ | ❌ 关闭 |
