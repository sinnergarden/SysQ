# Label Maturity Gate

每个预测模型都有各自的 **label horizon**（标签成熟所需交易日数）。

## 核心约束

对于日期 T 的 feature，对应的 label 在 T + horizon 交易日后才完整可观测。

```
5d  maxdd:  label 在 5 个交易日后成熟
60d alpha:  label 在 60 个交易日后成熟
180d alpha: label 在 180 个交易日后成熟
```

### 训练时

窗口中每个样本必须满足：**样本的 feature_date + horizon ≤ 训练截止日**

等价实现：**训练截止日 = predict_start - 1 - horizon**

### 推理时（daily infer）

模型训练标签必须在推理时已成熟。这由训练截止日扣 horizon 自动保证。

P0 script 中：`train_end = cal[td_idx - HORIZON]` ✅

### 三个模型的截止日差异

推理日期 2026-07-01 为例：

| 模型 | 训练可用最晚日期 |
|:----|:--------------:|
| 5d maxdd | 2026-07-01 - 5d ≈ 2026-06-24 |
| 60d | 2026-07-01 - 60d ≈ 2026-04-07 |
| 180d | 2026-07-01 - 180d ≈ 2025-10-21 |

### 验证

```python
train_end_label_mature = train_end - label_horizon
assert train_end_label_mature < predict_start, "Label not mature before predict!"
```

`rolling_window.py` 中的 `label_maturity_lag_trading_days` 已实现此逻辑。
