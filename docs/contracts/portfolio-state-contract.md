# Portfolio State Contract

## Purpose

系统如何表达某个 account / strategy 在某个时间点的组合状态？哪些对象是账户状态 SOT？legacy account store 和 shadow files 在迁移期如何处理？

## Producers

- LedgerService
- DailyRunner postclose
- Execution Backend
- broker reconciliation job
- legacy live/account path
- migration script

## Consumers

- DailyRunner preopen（只读）
- postclose reconciliation
- Ops UI（只读）
- Candidate/Shadow dashboard（只读）
- risk / exposure monitor（只读）
- broker bridge

## Storage / Transport

*"DB"不是架构语义本身。SQLite、JSON、CSV 只是介质。架构上真正重要的是状态语义。*

| 对象 | 介质 | 路径 | 语义 | 当前角色 | 目标角色 |
|------|------|------|------|---------|---------|
| Account State / Execution Ledger | SQLite | `data/trade.db` | 账户、持仓、订单、成交、快照的结构化状态 | 新主线 | 唯一 SOT |
| Legacy Account Store | SQLite | `data/meta/real_account.db` | 旧 live/account 路径使用的账户状态 | active legacy | 迁移后只读/移除 |
| Legacy Shadow Files | JSON/CSV | `shadow/` | 旧 alpha / shadow ops 兼容状态 | active compatibility | 只读或移除 |
| Daily Evidence | files | `daily/{date}/post_close/` | 盘后证据 | 当前有效 | 当前有效 |
| Broker Snapshot | 外部格式 | broker snapshot artifact | 外部状态快照 | 外部源 | 外部源 |

## Grain

```
per account_id
per strategy_id
per execution_date
per snapshot_time
per instrument
per order / fill
```

## Required Concepts

| 概念 | 含义 |
|------|------|
| AccountSnapshot | 账户在某时间点的全量状态 |
| PositionSnapshot | 单个标的的持仓快照 |
| OrderIntent | 订单意图（计划生成，尚未执行）|
| ExecutionFill | 实际或模拟成交 |
| CashEvent | 现金变动（入金、出金、分红、费用）|
| PortfolioSnapshot | 组合快照（持仓 + 现金 + 估值）|
| ReconciliationResult | 对账结果（内部 ledger vs 外部 broker）|

## Required Fields（最小字段级）

| 字段 | 含义 |
|------|------|
| `account_id` | 账户标识 |
| `strategy_id` | 策略标识 |
| `execution_date` | 执行日期 |
| `snapshot_time` | 快照时间 |
| `cash` | 现金余额 |
| `market_value` | 持仓市值 |
| `total_asset` | 总资产 |
| `instrument` | 标的代码 |
| `quantity` | 持仓数量 |
| `available_quantity` | 可用数量 |
| `cost_basis` | 持仓成本 |
| `last_price` | 最新价 |
| `order_id` | 订单标识 |
| `fill_id` | 成交标识 |
| `side` | 买卖方向 |
| `fill_qty` | 成交数量 |
| `fill_price` | 成交价格 |
| `fee` | 费用 |
| `source_run_id` | 来源 run_id |

## Invariants

- `data/trade.db` 是目标账户状态与执行流水 SOT。
- preopen 只能读取已确认的上一状态。
- postclose 才能提交 execution / portfolio snapshot。
- 真实 broker snapshot 与内部 ledger 的差异必须通过 reconciliation 暴露。
- legacy account store 和 shadow files 不得扩展新依赖。
- 删除 legacy store 前必须完成 consumer 切换、数据迁移和回归验证。
- 不允许策略 adapter 直接写生产账户状态。
- 所有状态变更必须可追溯 run_id。

## UI / Monitoring Usage

UI 展示（只读）：

- cash
- positions
- available quantity
- daily return
- turnover
- position gap
- cash gap
- reconciliation result
- account state freshness

Monitoring 用于：

- account stale
- position mismatch
- cash mismatch
- abnormal turnover
- missing fill
- unexpected empty portfolio

## Current Legacy Compatibility

当前三态共存：`data/trade.db`（新主线）、`data/meta/real_account.db`（旧 live/account 路径默认）、`shadow/`（JSON/CSV 文件）。旧入口仍写后两者，新主线写 `data/trade.db`。

迁移完成前的兼容策略：

- 不删除 `shadow/`，不删除 `data/meta/real_account.db`。
- 不扩展新消费者依赖 legacy 存储。
- 迁移前必须完成 schema 对齐、数据迁移、consumer 切换和回归验证。

## Versioning

Ledger schema 可随业务需求演进。新增字段不应破坏旧 ledger 读取。LedgerService 应兼容旧记录格式，不删除历史数据。迁移脚本按时间窗口分批执行，保留回滚能力。
