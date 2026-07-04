# UC_UI_ANALYSIS: UI Analysis

## Status
draft

## User Goal
通过只读 UI 查看策略状态、回测结果、信号指标、账户摘要。不通过 UI 发起交易或修改状态。

首批交付两个视图：
1. **回测收益图** — 回测净值曲线、基准对比、关键指标展示
2. **单股视角 review** — 在某个回测中，单只股票的买入/卖出状态、持仓记录、信号得分历史、特征值查看（融合原 UC_SINGLE_STOCK_REVIEW）

## Scope
包含：
- 策略看板（最新信号、持仓、PnL）
- 回测结果查看与对比（含净值曲线、基准对比）
- 单股视角 review — 在某个回测中，单只股票的买入/卖出状态、持仓记录、信号得分历史、特征值查看（融合原 UC_SINGLE_STOCK_REVIEW，不设独立 CLI/notebook 入口）
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

## Canonical Entrypoints

| Entrypoint | 职责 | Inputs | Outputs / Artifacts |
|-----------|------|--------|---------------------|
| `scripts/run_research_ui_api.py` | Research UI API 服务 | daily read model、research index、ledger query | UI 页面（只读） |

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
- （已定）首批交付两个视图：回测收益图 + 单股视角 review。UC_SINGLE_STOCK_REVIEW 融合在此，不设独立入口。
