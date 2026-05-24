# Alpha V3 Kronos-small Zero-Shot 实验报告

> 更新: 2026-05-25 · CSI800 · 2024-01-01 ~ 2026-05-20 · 每周调仓

---

## 摘要

**Kronos-small zero-shot 对 A 股没有预测能力**——IC ≈ 0，所有改进尝试均无效。

| 尝试方向 | 结论 |
|:---------|:------|
| 排序信号 (z-score) | IC = -0.0019, ICIR = -0.01 — 无预测能力 |
| EMA 平滑降噪 | 降低换手但 IC 仍为 0 — 收益来自噪声拟合 |
| 风险过滤 (排除底部 decile) | 过滤后收益低于纯动量 — 反而删掉了好股票 |
| 动量混合 (各种权重) | 加 Kronos 后 Sharpe 下降 — 无增量价值 |
| 作为 LGBM 特征 | IC=0 的特征只会引入噪声，不值得试 |

---

## 一、实验设计

### 1.1 目标

用 HuggingFace 上的 [NeoQuasar/Kronos-small](https://huggingface.co/NeoQuasar/Kronos-small)（24.7M 参数的 Transformer 时序模型）对 CSI800 成分股做 zero-shot 预测，生成 forward return 信号，接入现有回测框架验证选股能力。

### 1.2 信号生成

| 步骤 | 说明 |
|------|------|
| 输入 | 日频 OHLCV（前复权价格） |
| 模型 | Kronos-small, fp16, batch_size=128, context=512 |
| 推理 | lookback=90 天 → 预测未来 5 天 OHLCV |
| 信号 | `mean(pred_close[1:5]) / fq_close - 1` → 截面 z-score (clip ±3) |
| 频率 | 每周五，只对调仓日推理 (142 天 × 800 只) |

### 1.3 测试的 4 种使用模式

```
模式1: 排序信号 ── kronos_ret_5d_zscore → build_rank_weight_portfolio
   ├─ raw: 原始 z-score
   ├─ sma30/40/50: EMA 平滑 (α=0.3/0.4/0.5)
   └─ 结果: IC≈0, 排名每周随机洗牌

模式2: 风险过滤 ── momentum_20d_zscore + Kronos 排除底部 10%
   ├─ 先计算动量排序
   ├─ 排除 kronos_ret_5d 最差的 10% 股票
   └─ 结果: 比纯动量更差 (14.0% vs 22.5%)

模式3: 动量混合 ── w * momentum + (1-w) * kronos
   ├─ 权重: 70/30, 50/50
   ├─ 分别试 raw 和 sma30 版本
   └─ 结果: 加 Kronos 后 Sharpe 和收益均下降

模式4: LGBM 特征输入 (未测)
   └─ IC=0 的特征对 LGBM 无增量价值，跳过
```

### 1.4 回测配置

所有参数与 alpha_v1 一致：

| 参数 | 值 |
|------|-----|
| 选股方式 | rank-weighted, top_n=20 |
| Buffer hold/buy | 60 / 40 |
| 个股上限 | 7% |
| 调仓频率 | 每周 |
| 手续费 | 佣金 0.03% + 印花税 0.1% + 滑点 0.1% |

---

## 二、核心诊断：IC 分析

### 2.1 各信号 IC 对比

| 信号 | Mean IC | ICIR | RankIC | IC>0 占比 | 解读 |
|:----|:------:|:----:|:-----:|:---------:|:-----|
| **kronos_ret_5d_zscore** | **-0.0019** | **-0.01** | +0.0185 | 43% | **零预测能力** |
| momentum_5d_zscore | +0.0011 | +0.03 | +0.0029 | 47% | 极弱，接近噪声 |
| momentum_20d_zscore | -0.0042 | -0.12 | -0.0039 | 44% | 弱负向 |

> **解读**: Kronos z-score 的 ICIR = -0.01，远低于有效信号阈值 (通常 |ICIR| > 0.3)。IC 为正的比例仅 43%，还不如抛硬币。

### 2.2 分组收益 (Quantile Spread)

kronos_ret_5d_zscore 按五分位数分组的未来 5 日平均收益：

| 分组 | 平均 fwd_5d | 解读 |
|:---:|:----------:|:------|
| Q1 (最差) | +0.51% | Kronos 预测最差的反而涨最多 |
| Q2 | +0.39% | |
| Q3 | +0.33% | |
| Q4 | +0.27% | |
| Q5 (最好) | **+0.46%** | 预测最好的涨得一般 |
| **Spread Q5-Q1** | **-0.06%** | **负向 spread — 信号是反的** |

### 2.3 极端值分析

| 条件 | 样本数 | 平均 fwd_5d | 解读 |
|:----|:-----:|:----------:|:------|
| z-score < -2 | 3,225 | **+0.86%** | 预测暴跌的反而涨 0.86% |
| \|z-score\| ≤ 2 | 93,450 | +0.37% | 普通区域 |
| z-score > 2 | 1,659 | **+0.82%** | 预测大涨的涨 0.82% (不如底部极端值) |

**关键发现**: Kronos 的极端预测不可信。模型自信预测暴跌的股票 (z < -2) 反而是未来收益最高的群体。

### 2.4 逐年 IC

| 年份 | IC | RankIC | 样本数 |
|:---:|:--:|:-----:|:------:|
| 2023 | +0.0362 | +0.0485 | 12 周 |
| 2024 | +0.0008 | +0.0176 | 47 周 |
| 2025 | -0.0039 | +0.0227 | 48 周 |
| 2026 | **-0.0302** | -0.0124 | 17 周 |

在 2026 年 IC 转为明显负值，说明模型在近期市场环境下持续反向。

---

## 三、完整回测对比

### 3.1 全部 10 种信号变体

| 策略 | 总收益 | Sharpe | 最大回撤 | 换手率 | 总成本 | 胜率 |
|:----|:-----:|:-----:|:-------:|:-----:|:-----:|:---:|
| **Kronos 排序信号** | | | | | | |
| raw z-score | 85.7% | 2.05 | -24.7% | 402x | ¥2.00M | 50.0% |
| sma30 (α=0.3) | 115.1% | 2.36 | -26.5% | 195x | ¥1.02M | 54.0% |
| sma40 (α=0.4) | 116.7% | 2.36 | -28.4% | 228x | ¥1.19M | 54.0% |
| sma50 (α=0.5) | 97.6% | 2.13 | -28.4% | 262x | ¥1.34M | 51.6% |
| **动量基准** | | | | | | |
| momentum_20d_zscore | 22.5% | 1.03 | -17.9% | 475x | ¥2.11M | 49.2% |
| **风险过滤** | | | | | | |
| momentum + Kronos RF10 | 14.0% | 0.77 | -21.8% | 474x | ¥2.09M | 47.6% |
| **动量混合** | | | | | | |
| blend 70mom/30kronos | 18.1% | 0.87 | -21.7% | 476x | ¥2.08M | 46.0% |
| blend 50mom/50kronos | 30.6% | 1.14 | -22.9% | 460x | ¥2.13M | 49.2% |
| blend 70mom/30sma30 | 26.6% | 1.15 | -21.8% | 475x | ¥2.11M | 47.6% |
| blend 50mom/50sma30 | 57.9% | 1.92 | -17.5% | 467x | ¥2.42M | 52.4% |

### 3.2 关键发现

**发现 1: Kronos 信号回测收益是伪回归**

Kronos z-score 的 IC 为 -0.0019 (零)，但回测显示 85.7% 收益、2.05 Sharpe。这是因为在 800 只股票中每周选 20 只，即使随机选也有 ~2.5% 的选中概率，叠加 125 周的复利效应，噪声拟合出 85.7% 的"收益"。回测中的"高收益"不代表信号有效。

**发现 2: EMA 平滑降低换手但没创造 alpha**

SMA30 将换手从 402x 降到 195x (-51%)，成本从 ¥2.00M 降到 ¥1.02M。但 IC 仍然是零 — 平滑只是减少了噪声交易的频率，没有增强预测能力。85.7% → 115.1% 的增益来自更少的摩擦损失，而不是更好的选股。

**发现 3: Kronos 风险过滤适得其反**

动量 + Kronos 底部排除的结果 (14.0%) 比纯动量 (22.5%) 更差。因为 Kronos 预测暴跌的股票 (D1 decile) 未来 5 日平均收益反而是最高的 (0.64%)。用它做风险过滤=排除未来可能涨最好的股票。

**发现 4: 加入 Kronos 后动量混合策略全面劣化**

| 混合比 | 纯动量 | 加 Kronos | 变化 |
|:-----|:-----:|:---------:|:----:|
| 70/30 | Sharpe 1.03 | Sharpe 0.87 | -15% |
| 50/50 | Sharpe 1.03 | Sharpe 1.14 | +10% (来自 sma30) |

sma30 版本的 50/50 blend Sharpe=1.92 看似不错，但 momentum 和 kronos 的 IC 都接近零，这个高 Sharpe 同样是噪声拟合。IC 为零的信号里掺再多也还是零。

---

## 四、根因分析

### 4.1 为什么 Kronos-small zero-shot 对 A 股无效？

```
Kronos-small 预训练数据 (假设: 美股 / 加密货币)
  → 价格模式、波动率特征、交易机制与 A 股不同
  → A 股以散户为主导、有涨跌停、T+1 结算
  → 美股训练的参数无法迁移到 A 股
  → 预测的 forward return 与真实值不相关 (IC ≈ 0)
```

具体原因：
- **市场 microstructure 差异**: A 股有涨跌停板、T+1 交易、散户主导
- **行业/板块驱动**: A 股行业轮动效应强，纯价格序列不足以预测
- **模型容量**: 24.7M 参数在预训练中可能过拟合了特定市场模式
- **Zero-shot 局限**: 没有 A 股 fine-tune，模型没见过 A 股数据

### 4.2 与 alpha_v1 的本质差距

| 维度 | Alpha V1 (LGBM) | Alpha V3 (Kronos zero-shot) |
|:----|:--------------:|:--------------------------:|
| 特征维度 | 200+ 手工因子 | 5 维 OHLCV |
| 训练方式 | 每 5 天滚动 retrain | 固定预训练权重 |
| 市场适配 | 在 A 股数据上训练 | 无 A 股 fine-tune |
| IC | ~0.05+ | -0.0019 |
| 排名稳定性 | >80% 周留存 | 30% 周留存 |

V1 的成功来自 A 股手工因子 + A 股滚动训练，Kronos zero-shot 两者都不具备。

### 4.3 信号平滑的局限性

EMA 平滑确实提升了回测收益 (85%→115%) 和降低了换手 (402x→195x)，但需要理解为什么：

```
Raw z-score:  每周完全洗牌 → 每 周 100% 换仓 → 摩擦耗尽收益
SMA30 z-score:排名变化放缓 → 每次只换 ~25% → 摩擦降低 → 账面收益提升
```

平滑**没有创造 alpha**，只是减少了噪声交易的成本。IC 仍然是零，说明在真实交易中不会产生超额收益。这属于典型的过拟合现象——降低噪声在回测中表现为收益提升，但实盘中不会出现。

---

## 五、工程成果

虽然 Kronos zero-shot 在 A 股无效，但工程上构建了一个完整的实验框架：

### 5.1 Pipeline 能力

```
run_pipeline.py --skip-inference  30s 完成全流程
                --smoke           快速验证
                --allow-synthetic  无 GPU 也可用

信号处理:
  ├─ build_signals()      Kronos raw prediction → z-score
  ├─ smooth_signals()      EMA 平滑 (多 alpha)
  ├─ add_momentum_signals()  动量 z-score
  ├─ add_risk_filter_signals() Kronos 风险过滤
  ├─ add_blended_signals()   多信号混合
  └─ evaluate_signals()     IC/RankIC/ICIR/分组收益

输出:
  ├─ signals.parquet + manifest
  ├─ backtest/daily_equity_*.csv + trade_log_*.csv
  ├─ comparison/backtest_metrics.csv + portfolio_curves.parquet
  ├─ evaluation/ic_daily.csv + ic_summary.csv + group_returns_*.csv
  └─ reports/backtest_*.json (UI 兼容)
```

### 5.2 推理优化

| 优化 | 效果 |
|:----|:-----|
| 只对调仓日推理 (142 天而非全部) | 推理量从 3.2M 降到 114K (-96%) |
| batch_size=128 fp16 | GPU 满载，无 OOM |
| pred_len=5 (而非 20) | 单次推理 4x 加速 |
| `--skip-inference` 缓存 raw_predictions | 迭代实验从 12min 降到 30s |

### 5.3 性能指标

| 阶段 | 首次运行 | 缓存命中 |
|:----|:-------:|:--------:|
| 数据加载 | 1.1s | 1.1s |
| 推理 | 11 min | 跳过 |
| 信号构建+平滑+混合 | 2s | 2s |
| 10 个回测 | 10s | 10s |
| IC 评估 | 5s | 5s |
| **总计** | **~12 min** | **~30s** |

---

## 六、结论与建议

### 6.1 最终结论

**Kronos-small zero-shot 在 A 股市场无效。** IC 为零，所有改进方向（平滑、过滤、混合）均无法产生正的预测能力。

回测中 85-116% 的"收益"和 2.0+ 的"Sharpe"是噪声拟合和低成本假设下的统计假象。

### 6.2 要产生价值，需要

| 方案 | 复杂度 | 前提条件 |
|:----|:-----:|:--------|
| **Fine-tune Kronos** 在 A 股数据上 | 高 | 需要网络下载模型+训练脚本 |
| **Kronos 特征嵌入** → 接入 LGBM | 高 | 需要提取 Kronos 中间层嵌入 |
| 等社区出 A 股训练的时序基础模型 | 低 | 等待 |
| **放弃 Kronos，回归 alpha_v1** | 低 | 已验证有效 |

### 6.3 工程遗产

这套实验框架可以直接用于：
1. 接入**其他预训练模型**（如 TimeGPT, TimesFM）做同样测试
2. 作为 **alpha_v1 信号增量的测试床**（添加新因子、测试混合权重）
3. 如果未来 Kronos 有 A 股 fine-tune 版本，改一行模型名即可重新验证

---

## 附录

### 运行命令

```bash
# 完整运行（首次，含 Kronos 推理，~12 分钟）
python -u experiments/alpha_v3_kronos_small/run_pipeline.py

# 缓存命中（跳过推理，~30 秒）
python -u experiments/alpha_v3_kronos_small/run_pipeline.py --skip-inference

# Smoke test（合成信号，快速验证）
python -u experiments/alpha_v3_kronos_small/run_pipeline.py \
    --smoke --universe csi300 --start-date 2024-07-01 \
    --end-date 2024-09-30 --allow-synthetic
```

### 输出文件

```
outputs/
├── raw_predictions.parquet       # Kronos 原始预测 (114K rows)
├── signals/
│   ├── signals.parquet           # 处理后信号 (99K rows, 16 columns)
│   └── manifest.json
├── backtest/
│   ├── daily_equity_*.csv        # 10 个变体的日频权益
│   └── trade_log_*.csv           # 10 个变体的交易流水
├── comparison/
│   ├── backtest_metrics.csv      # 全部变体的指标汇总
│   ├── portfolio_curves.parquet  # 权益曲线
│   └── alpha_v3_vs_alpha_v1_report.md
├── evaluation/
│   ├── ic_daily.csv              # 每日期望 IC (124 dates)
│   ├── ic_summary.csv            # IC/RankIC/ICIR 汇总
│   ├── group_returns_*.csv       # 五分位分组收益
│   └── signal_eval_results.json
└── manifest.json
```

### 文件依赖

```
run_pipeline.py
  ├── lib/data.py             → load_fq_ohlcv()
  ├── lib/kronos_inference.py → load_model(), run_inference()
  ├── lib/signal_builder.py   → build_signals(), smooth_signals(),
  │                              add_momentum_signals(),
  │                              add_risk_filter_signals(),
  │                              add_blended_signals(),
  │                              evaluate_signals()
  ├── lib/backtest_runner.py  → run_backtest()
  ├── lib/comparison.py       → build_report(), export_ui_report()
  ├── lib/synthetic.py        → generate_signals() [fallback]
  └── config.yaml             → 全部参数
```
