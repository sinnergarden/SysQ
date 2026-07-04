# UC_UI_ANALYSIS: UI Analysis

## Status
draft

## User Goal
通过只读 UI 查看策略状态、回测结果、信号指标、账户摘要。不通过 UI 发起交易或修改状态。

## Scope
包含：
- 策略看板（最新信号、持仓、PnL）
- 回测结果查看与对比
- 信号 IC/RankIC 可视化
- 账户/ledger 只读查询
- 数据 readiness 查看

不包含：
- 通过 UI 下单
- 修改策略参数
- 修改 ledger/账户状态
- 生产级 dashboard（Phase 3+）

## Inputs
- daily read model（`qsys/ops_api/`）
- research index / experiment manifest
- ledger query API

## Outputs
- UI 页面（只读，不产生持久化 artifact）
- Research UI API（`scripts/run_research_ui_api.py`）

## Canonical Entrypoints
- `scripts/run_research_ui_api.py`

## Key Artifacts
- 无持久化 artifact（UI 是读取层）

## Required Checks
- TBD: UI read-only check（禁止 UI 代码写 ledger/broker）

## Owner Agent
ui_agent

## Allowed Paths
- `qsys/research_ui/`
- `qsys/ops_api/`
- `scripts/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`
- `deploy/`

## Open Questions
- Phase 1 ROADMAP 中"最小 Ops UI"的具体交付标准是什么？
