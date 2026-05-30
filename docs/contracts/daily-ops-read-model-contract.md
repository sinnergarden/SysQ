# Daily Ops Read Model Contract

## Purpose

Ops UI 和 monitoring 应该读什么来展示 daily 状态？如何避免 UI 变成第二套业务逻辑？

UI read model 的目标不是新增一套状态存储，而是定义 UI / monitoring 只读消费 daily ops 产出数据的口径，避免 UI 到处直接读 raw 文件、ledger、shadow、experiments。

## Producers

- DailyRunner
- preopen pipeline
- postclose pipeline
- report generator
- ledger readonly view
- broker reconciliation job

## Consumers

- Ops UI（只读）
- daily monitoring（只读）
- notification（只读）
- human operator
- Candidate/Shadow dashboard（只读）

## Storage / Transport

Read model 可以是：

- daily report JSON（当前已有 `daily_ops_digest_*.json`）
- daily evidence artifact（`daily/{date}/`）
- readonly API（未来）
- generated UI read model（未来）
- ledger readonly view（future）

UI 不直接写任何状态。

## Grain

```
per execution_date
per strategy_id
per account_id
per run_id
```

## Required Fields

| 字段 | 含义 |
|------|------|
| `execution_date` | 执行日期 |
| `strategy_id` | 策略标识 |
| `stage` | candidate_shadow / production |
| `run_id` | 本次运行的 run_id |
| `data_readiness_status` | 数据 readiness（ready / degraded / blocked） |
| `model_freshness_status` | 模型新鲜度 |
| `plan_status` | 盘前计划是否生成 |
| `order_intent_count` | 订单意图数量 |
| `expected_turnover` | 预期换手率 |
| `account_snapshot_status` | 账户快照是否可用 |
| `postclose_status` | 盘后是否完成 |
| `reconciliation_status` | 对账是否完成 |
| `blocking` | 是否有阻塞项 |
| `reason` | 阻塞或降级原因 |
| `artifact_paths` | 关键产物路径 |
| `generated_at` | 生成时间 |

## Optional Fields

| 字段 | 含义 |
|------|------|
| `top_positions` | 前几大持仓 |
| `planned_buys` | 计划买入数 |
| `planned_sells` | 计划卖出数 |
| `cash_usage` | 现金使用率 |
| `mtm_return` | 当日 MTM 收益 |
| `drawdown` | 回撤 |
| `position_gap` | 持仓差异（与 broker） |
| `cash_gap` | 现金差异 |
| `broker_sync_status` | broker 同步状态 |
| `notification_status` | 通知是否发送 |

## Invariants

- UI read model 只读。
- UI 不写 ledger。
- UI 不下单。
- UI 不改策略。
- UI 不绕过 DailyRunner。
- UI 不直接依赖 legacy shadow files 作为长期接口。
- blocking 状态必须清晰，阻止性异常必须显式传递。
- 所有展示项必须能回跳 artifact path 或 run_id。

## UI / Monitoring Usage

Ops UI 展示：

- data readiness
- latest data date
- daily plan
- order intents
- shadow / production status
- ledger snapshot
- postclose report
- reconciliation gap
- blocking reason

Monitoring 用于：

- blocked alert
- stale data alert
- reconciliation gap alert
- model freshness alert
- empty plan alert

## Current Legacy Compatibility

当前 daily ops 产物由旧入口（`run_daily_trading.py`、`run_post_close.py`、`run_alpha_v1_daily.py`）生成，写入 `daily/{date}/` 目录。新入口（`run_daily.py` → `DailyRunner`）尚未接入 systemd，无独立 read model。迁移期间 UI 原型可直接消费旧产物的 JSON/CSV，但不应扩张新依赖。

## Versioning

Read model 字段可随 daily ops 流程演进。新增字段不应破坏旧 report 读取。旧 daily evidence 保留不删，通过 `daily/{date}` 路径按日期归档。
