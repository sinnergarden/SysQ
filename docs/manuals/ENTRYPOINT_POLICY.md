# 脚本入口策略（主入口与 Legacy 入口）

## 1. 主入口

当前日常交易主入口统一为：

```bash
python scripts/run_daily_trading.py
```

该入口包含数据更新、模型新鲜度检查、影子账户模拟、实盘账户同步与计划生成。

## 2. Legacy 入口

以下脚本保留用于兼容历史流程，但不建议作为新流程入口：

1. `scripts/run_plan.py`
2. `scripts/run_reconcile.py`

这两个脚本在运行时会输出 Legacy 警告，提示迁移到 `run_daily_trading.py`。

## 3. 迁移建议

1. 新功能只加在 `run_daily_trading.py` 及其 `qsys/live` 组件中。
2. Legacy 脚本仅做最小修复，不扩展新业务逻辑。
3. 当历史调用全部迁移后，可在后续版本中移除 Legacy 脚本。
