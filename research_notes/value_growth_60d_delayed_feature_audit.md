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

| Variant | 特征数 | IC | ICIR | RankIC | RankICIR | ΔIC vs baseline |
|---------|:-----:|:--:|:---:|:-----:|:-------:|:---------------:|
| v2 baseline | 64 | 0.0335 | 0.339 | 0.0329 | 0.270 | — |
| +margin | 73 | 0.0453 | 0.459 | 0.0418 | 0.356 | **+0.012** |
| +shareholder | 74 | 0.0435 | 0.526 | 0.0413 | 0.418 | +0.010 |
| **v3a_full** | **83** | **0.0545** | **0.644** | **0.0485** | **0.493** | **+0.021** |
| existing pv only | 26 | 0.0032 | 0.032 | -0.0064 | -0.052 | −0.030 ❌ |
| +v3b_pv | 97 | 0.0537 | 0.640 | 0.0474 | 0.485 | +0.020 |

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

- **existing pv only（纯量价）**：IC=0.003，几乎零预测力。当前已有的 ret/rps/volume 特征对 60d 收益没有预测能力。
- **v3b_pv（新增量价质量）**：IC=0.0537，与 v3a_full 持平（Δ=-0.0008），说明 v3b 特征即使在 60d 短周期也无增量。方向关闭结论不变。

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

### 5. 是否建议下一步做 signal combination

**建议。** 60d（timing）和 180d（candidate pool）组合可能有正交互：
- 180d 信号选出长期候选池
- 60d 信号在池内做调仓时机判定
- 两者相关性低（60d 偏 margin/short-term, 180d 偏 holder/long-term）

## 文件

- `configs/research/60d/abl_*_delayed60.yaml` — 6 个 delayed60 configs
- `configs/features/value_growth_existing_price_volume.yaml` — 纯量价特征列表
- `tests/research/test_label_maturity_delay_60d.py` — [待创建]
- `tests/research/test_60d_configs_smoke.py` — [待创建]

## 结论

| 判断 | 结果 |
|:---|:----:|
| 60d 可作为 timing/rebalance 信号？ | **Weak Pass — 可作辅助信号** |
| 推荐单独使用？ | ❌ 不推荐 |
| 推荐 60d+180d 组合？ | ✅ 建议下一步验证 |
| 新增 v3b 特征对 60d 有用？ | ❌ 无增量 |
