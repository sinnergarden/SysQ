# ARCHITECTURE

本文档只描述系统的**大结构**与**边界规则**。具体功能细节统一写在 `docs/features/`。

## 架构风格

- 模式：模块化单体。
- 目标：统一代码仓、清晰模块边界、稳定可维护。

## 系统分层

- 入口层：`scripts/`，只做编排。
- 业务层：`qsys/`，承载核心业务逻辑。
- 配置与数据层：`config/` 与 `data/`。
- 验证层：`tests/`。

## 模块职责

- `qsys/data`：数据接入与格式转换。
- `qsys/feature`：特征定义与计算。
- `qsys/model`：训练、保存、加载、推理。
- `qsys/strategy`：分数到目标仓位。
- `qsys/trader`：订单计划与交易约束。
- `qsys/live`：每日交易编排、账户状态、影子模拟。
- `qsys/backtest`：历史回测。
- `qsys/analysis`：绩效分析。

## 依赖规则

- `scripts -> qsys` 允许。
- `qsys -> scripts` 禁止。
- `qsys` 不依赖 `tests`、`notebooks`。
- 禁止跨模块循环依赖。

## 核心流程

1. 训练流程：`run_train -> feature/model -> model artifact`。
2. 回测流程：`run_backtest -> model/strategy/backtest -> report`。
3. 每日流程：`run_daily_trading -> live/trader -> plan/account`。

## 架构不变量

- 每日交易主入口是 `scripts/run_daily_trading.py`。
- 配置统一由 `qsys.config.manager` 读取。
- 交易计划至少包含 `symbol, side, amount`。
- 模型产物至少包含 `model.pkl, meta.yaml`。

## 功能文档入口

- 新功能必须创建：`docs/features/new_feature.md`。
- 功能文档模板见 [new_feature.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/features/new_feature.md)。
- 已落地功能清单见 `docs/features/` 目录。
