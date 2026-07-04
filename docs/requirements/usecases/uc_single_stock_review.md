# UC_SINGLE_STOCK_REVIEW: Single Stock Review（已融合到 UC_UI_ANALYSIS）

## Status
merged — 此 use case 已融合到 `UC_UI_ANALYSIS` 首批交付，不再作为独立 use case 开发。
详见 `uc_ui_analysis.md` 中"单股视角 review"。

## User Goal
研究员或 operator 可以查看单只股票的 K 线、信号得分、特征值、持仓历史、交易记录，用于 debug 和归因分析。

## 融合说明
本文件保留供历史参考。后续开发请走 UC_UI_ANALYSIS 入口。

## Scope
包含：
- 单票 K 线展示
- 信号得分历史
- 特征值查看
- 持仓与交易记录
- 回测归因（该票在回测中的贡献）

不包含：
- 整体回测分析（见 UC_RESEARCH_BACKTEST、UC_UI_ANALYSIS）
- 基本面研究（见 UC_STOCK_FUNDAMENTAL_RESEARCH）
- 交易操作

## Inputs
- 信号 artifact（`data/research/signals/`）
- 行情数据
- ledger/交易数据
- 特征数据

## Outputs
- 单票综合视图（控制台或 CSV）

## Canonical Entrypoints
TBD — 当前没有独立入口。可从 `scripts/dev/` 或 notebook 实现原型。

## Key Artifacts
- 无独立 artifact（复用已有信号/行情/ledger 数据）

## Required Checks
TBD

## Owner Agent
research_agent

## Allowed Paths
- `scripts/dev/`
- `notebooks/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`

## Open Questions
- 当前是否需要独立 CLI 入口？还是 notebook 就够用？
