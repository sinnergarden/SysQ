# Label Maturity Delay Validation — v3a Feature Ablation

> 验证当前 v3a 结果是否存在 "180d label maturity leakage"。

## 为什么 180d label 需要 maturity delay

标准 rolling validation 中：

```
train_start ... train_end | predict_start ... predict_end
```

训练样本的 `fwd_ret_180d_raw` 使用 T+180 日的价格。如果 `train_end = predict_start - 1`，则训练样本中日期靠近 `train_end` 的样本，其 label 在 predict_start 当天还无法完全确定（需要 180 个交易日后的价格）。

这造成了 **label maturity leakage**：训练时使用了在预测时尚未完全成熟的标签，使模型获得了本不应获得的未来信息。

## 修正方法

对于每个 `predict_start = T`：

```
effective_train_end = T - 180 trading days
```

训练集只使用 `sample_date ≤ effective_train_end` 的样本。特征 lookback 保持不变。

## Normal Rolling 与 Delayed Rolling 的区别

| 维度 | Normal | Delayed (180d) |
|------|--------|----------------|
| 窗口数（2020-2025） | 70 | 61 |
| 早窗覆盖 | 2020-03 起 | 2020-12 起（晚 9 个月） |
| train_end = ? | predict_start - 1天 | predict_start前 ~193个交易日 |
| 训练集新鲜度 | 截至预测前一天 | 截至预测前 180 交易日 |

## 结果对比（严格 20d）

| Variant | Normal IC | Normal ICIR | Delayed IC | Delayed ICIR | ΔIC |
|---------|:--------:|:----------:|:---------:|:-----------:|:---:|
| v2 baseline | 0.3980 | 2.971 | 0.0529 | 0.495 | **−0.345** |
| +margin | 0.4158 | 2.908 | 0.0602 | 0.572 | −0.356 |
| +shareholder | 0.4682 | 3.797 | 0.0678 | 0.738 | −0.400 |
| +full | 0.4991 | 4.851 | 0.0777 | 0.837 | −0.421 |

## 分析

1. **Normal 的 IC=0.50 被 label maturity leakage 显著夸大。** 在 delayed 验证下，所有 variants 的 IC 暴跌至 ~0.05-0.08。增量优势（shareholder/full > baseline）仍然存在但极其微小。

2. **为什么 delayed IC 这么低？**
   - 180 天的 label 窗口意味着训练数据比预测日早了近 1 年
   - 模型学的是 2018-2024 中早期标签的关系，去预测 2025 年的选股——市场结构变化太大
   - 特征也是 1 年旧。预测日 T 的模型用的是 T-180 天的特征，远不是最新值

3. **Delayed 下为什么 shareholder/full 还略好于 baseline？** 增量优势从 +0.10 缩小到 +0.025——说明 v3a 特征有真实信号，但远没有 normal 显示的那么强。

## 结论：存在 Label Maturity Leakage ❌

**Strong Pass**: ❌ delayed v3a_full=0.0777 远低于 baseline normal=0.3980
**Weak Pass**: ⚠️ 相对增量存在（full > baseline +0.025）但绝对水平极低

**所以 PR #179 报告中 IC=0.4991 的结论受到了 label maturity leakage 的显著高估。** 修正后的可信结果是 IC≈0.08，信号微弱但方向正确。这不足以支持进入 shadow/candidate pipeline。

## 下一步建议

1. 如果需要准确验证长周期信号（180d），必须使用 delayed validation。之前的 normal rolling 高估了 6 倍。
2. 考虑缩短预测周期：120d 或 60d label 的 maturity delay 影响会更小。
3. 如果坚持 180d 方向，需要确保所有后续验证（包括 v1 vs v2 对比）也在 delayed 框架下重做。

## 实现文件

- `qsys/research/rolling_window.py` — `build_rolling_windows()` 新增 `label_maturity_lag_trading_days` 参数
- `qsys/research/signal_pipeline.py` — 从 config labels[0] 读取 lag 并透传
- `configs/research/abl_*_delayed180.yaml` — 4 个 delayed config
- `tests/features/test_v3a_smoke.py` — 待增加最小 delayed 验证测试
