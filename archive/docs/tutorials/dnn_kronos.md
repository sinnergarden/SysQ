# Tutorial: DNN Kronos Multi-Task Signal

## 目标

新建一个基于 PyTorch DNN 的多任务策略，完整走通 research pipeline。

## 策略描述

- **Feature**: 全部 254 个 alpha_v1 qlib 特征。每个特征同时输入 raw 值和每日横截面 zscore → 508 维
- **Model**: 共享底层 DNN（128→32） + 两个 task tower（5d / 20d）
- **Label**: forward_return_5d + forward_return_20d，zscore 标准化后作为训练 target
- **Loss**: MSE(score_5d, label_5d_z) + MSE(score_20d, label_20d_z)
- **Signal**: predict 后每个 horizon 的 output 做每日横截面 zscore → 等权 blend → 最终 score
- **Universe**: CSI300（可在 config 中修改）

## 文件结构

| 文件 | 用途 |
|------|------|
| `qsys/model/zoo/dnn_kronos.py` | PyTorch DNN 模型定义 |
| `qsys/research/generators/dnn_kronos.py` | RollingSignalGenerator 实现 |
| `configs/research/dnn_kronos_smoke.yaml` | 研究配置（smoke test）|

修改文件：
- `qsys/research/rolling_runner.py` — 在 `_create_generator_from_config` 注册 `dnn_kronos` 类型

## 步骤

### 1. 配置文件

```yaml
# configs/research/dnn_kronos_smoke.yaml
experiment_id: dnn_kronos_smoke_001
title: "DNN Kronos multi-task signal"

calendar:
  start_date: "2026-05-22"
  end_date: "2026-05-26"
  train_window_days: 60
  predict_window_days: 2
  step_days: 2

signal:
  signal_id: dnn_kronos_score
  signal_run_id: rolling_dnn_kronos_202605

generators:
  - generator_id: dnn_kronos
    type: dnn_kronos
    params:
      universe: csi300
      dnn_kwargs:
        epochs: 10
        batch_size: 1024
        lr: 0.001

labels:
  - label_id: forward_return_5d
  - label_id: forward_return_20d
```

> **设置 universe**: `params.universe` 字段。可选 `csi300`、`csi800`、`csi500` 等 qlib 中已安装的 universe。
> **设置 DNN 参数**: `params.dnn_kwargs` 内可设 epochs、batch_size、lr。

### 2. 运行 rolling research

```bash
python scripts/research/run_rolling_research.py \
  --config configs/research/dnn_kronos_smoke.yaml \
  --overwrite-all
```

这一步会：
- 拆 rolling window
- 每个 window 内 fetch 254 features + 计算 zscore
- 训练 DNN
- Predict + 等权 blend → signal CSV

> **注意**：当前 v2 matrix 模式下 signal 不会自动写入 SignalStore。signal 文件在 `data/research/signals/` 目录下。如需要手动保存：

```python
from qsys.signal.store import SignalStore
store = SignalStore()
store.save_signal_run(
    signal_id="dnn_kronos_score",
    signal_run_id="rolling_dnn_kronos_202605",
    predictions=result_df,
    overwrite=True,
)
```

### 3. 评估信号（IC / RankIC / ICIR）

```bash
python scripts/research/evaluate_signal.py \
  --signal-id dnn_kronos_score \
  --signal-run-id rolling_dnn_kronos_202605 \
  --label-id forward_return_5d \
  --overwrite

python scripts/research/evaluate_signal.py \
  --signal-id dnn_kronos_score \
  --signal-run-id rolling_dnn_kronos_202605 \
  --label-id forward_return_20d \
  --overwrite
```

### 4. 回测

```bash
python scripts/research/backtest_from_signal.py \
  --signal-id dnn_kronos_score \
  --signal-run-id rolling_dnn_kronos_202605 \
  --start-date 2026-01-01 --end-date 2026-05-22 \
  --top-n 20 --initial-capital 10000000 \
  --overwrite
```

> 参数 `--top-n`：每期选股数；`--initial-capital`：初始资金；`--rebalance-freq`：调仓频率。

### 5. 查看结果

实验结果在 `data/research/experiments/{experiment_id}/`。使用 ExperimentIndex 汇总：

```bash
python scripts/research/build_experiment_index.py \
  --experiment-id dnn_kronos_run_001 \
  --title "DNN Kronos full run" \
  --signal-run dnn_kronos_score:<run_id> \
  --signal-eval dnn_kronos_score:<run_id>:forward_return_5d \
  --backtest <strategy_run_id>:<backtest_id> \
  --overwrite
```

## 修改 universe

在 config yaml 中修改 `params.universe` 即可。当前支持所有 qlib 已安装的 universe：
- `csi300`
- `csi800`
- `csi500`
- `all_a`

## 运行完整训练（非 smoke）

将 config 中的 calendar 改为：

```yaml
calendar:
  start_date: "2024-01-01"
  end_date: "2026-05-22"
  train_window_days: 252
  predict_window_days: 20
  step_days: 20
```

DNN epochs 建议 50-100，实际运行可能需要数小时（252 天训练窗口 × 3000+ 股票 × 50 epoch）。

## 模型架构参考

```
508 features (254 raw + 254 cs_zscore)
         │
    Linear(128) → ReLU
         │
    Linear(32) → ReLU      ← shared bottom
      ╱        ╲
  Linear(1)   Linear(1)    ← task towers
  score_5d    score_20d
         │         │
     cs_zscore  cs_zscore
         └─── 0.5 blend ───→ final score
```
