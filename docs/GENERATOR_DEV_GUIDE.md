# Generator 开发指南

本文档是 `AGENTS.md` 的子文档，详细说明信号生成器的开发规范。

## 1. 核心边界

```
Generator:   label/task ─→ base signal (单个 score 列)
Combine:     多个 base signal ─→ final composite signal
```

Generator 内部不做最终组合。多 label 模型应该每个 label 输出一个独立 `SignalRun`，通过 `signal_combine.py`（combine 层）组合。只有这样，每个 base signal 才能被单独评估 IC、回测、替换、调节权重。

**例外**：`LightGBMAlphaV1Generator` 和 `DnnMultitaskGenerator` 的 `blend_weights` 参数是 legacy alpha_v1 兼容路径，将多个 label 预测混合成一个 score。新 generator 不应延续这个模式。

## 2. Protocol

所有生成器实现 `qsys/research/generators/base.py` 中的 `RollingSignalGenerator` Protocol：

```python
class RollingSignalGenerator(Protocol):
    def generate(self, *, train_start, train_end, predict_start, predict_end,
                 signal_id, signal_run_id) -> pd.DataFrame:
        ...
```

### 契约

| 项 | 要求 |
|----|------|
| 输入 | `(train_start, train_end, predict_start, predict_end, signal_id, signal_run_id)` — 全是 `str YYYY-MM-DD` |
| 输出列 | `trade_date, data_date, instrument, signal_id, signal_run_id, score`（必选 6 列，顺序不限） |
| `data_date` | 必须是 `previous_trading_day(trade_date)` — `SignalStore.save` 会自动检查 |
| `score` | 值越大越好，可排序，不允许改变含义 |

### 依赖规则

- 如果需要 label，从 `LabelStore` 读（`qsys/label/store.py`），不要自己计算 forward return
- 如果需要特征，从 qlib `D.features()` 或 `QlibAdapter.get_features()` 获取
- 不要引用 `qsys/strategy/` 下的具体策略模块（策略代码不应被 research 反向依赖）
- 输出中不要用 `print()` — 用 `qsys.utils.logger.log`

## 3. 注册

新增 generator 后必须在 `qsys/research/matrix_job.py:_create_generator_from_config()` 注册，才能在 matrix experiment YAML 中按 `type` 引用。

## 4. 共享工具

| 工具 | 位置 | 说明 |
|------|------|------|
| `cs_zscore` | `generators/utils.py` | 横截面 z-score，clip ±3，处理常数列 |
| `build_prev_trading_date_lookup` | `generators/utils.py` | `trade_date → previous_trading_day` 查表 |

## 5. 测试要求

每个 generator 必须有 golden test：**确定性输入 → 断言精确 score 值**。参考 `tests/research/test_generators_golden.py`。

## 6. 现有 Generator 总表

| 类型 | 文件 | 说明 | 测试状态 |
|------|------|------|---------|
| `fixture` | `generators/fixture.py` | 确定性随机信号，测试/CI 用 | ✅ golden |
| `technical_composite` | `generators/technical_composite.py` | OHLCV 横截面复合 | ✅ golden |
| `lightgbm_alpha_v1` | `generators/lightgbm_alpha_v1.py` | 多 label → blend（legacy） | ⚠️ 无独立测试 |
| `lightgbm_single_label` | `generators/lightgbm_single_label.py` | 单 label → 单 SignalRun（推荐） | ✅ contract |
| `dnn_multitask` | `generators/dnn_multitask.py` | 双 task DNN → blend（legacy） | ⚠️ 无独立测试 |
| `alpha_v1_existing` | `generators/alpha_v1_existing.py` | 已有 alpha_v1 适配器 | ⚠️ 无独立测试 |
