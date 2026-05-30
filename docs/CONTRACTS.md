# CONTRACTS

本文档定义 SysQ 中 Research、Daily Ops、Monitoring、UI 共同依赖的数据接口边界。

系统地图见 `docs/ARCHITECTURE.md`，artifact 字段级 schema 见 `docs/schema/`。

---

## 1. 设计原则

- **Contract 描述长期接口边界**，不是阶段性 feature spec。普通字段补充走 PR review；改变接口语义、生命周期或跨模块边界时应补 ADR。
- `docs/ARCHITECTURE.md` 讲系统地图（两条主链路、分层、过渡态）；**本文讲数据如何在模块间流动**（谁生产、谁消费、粒度、不变量）。
- `docs/schema/` 是 artifact 字段级 schema（SignalArtifact、OrderIntentArtifact 等）；本文只讲更高层的 producer / consumer / grain / invariant。
- **UI 和 monitoring 只读** —— 不写 ledger，不下单，不改策略。
- **不把目标态写成当前事实**。当前尚未完成的迁移路径必须在文档中标注。
- **Legacy path 只能作为 compatibility**，不扩展新依赖。
- **字段只写第一版最小稳定字段**，不是全量字段大全。后续逐步演进。

### Write / Read Boundary

| Contract | Producer | Consumer | UI / Monitoring 可写？ |
|----------|----------|----------|----------------------|
| Data Readiness | data sync pipeline、readiness check | DailyRunner、train、backtest、UI、monitoring | ❌ 只读 |
| Signal | Predictor、RollingResearchRunner、DailyRunner | BacktestEngine、SignalEvaluator、ExperimentIndex、UI | ❌ 只读 |
| Research Analytics | RollingResearchRunner、SignalEvaluator、BacktestEngine、ExperimentIndex | Research UI、promotion review、monitoring | ❌ 只读 |
| Daily Ops Read Model | DailyRunner、postclose pipeline、report generator | Ops UI、monitoring、notification | ❌ 只读 |
| Portfolio State | LedgerService、Execution Backend、broker reconciliation | DailyRunner、UI、monitoring、broker bridge | ❌ 只读 |

---



## 2. Contract Index

| Contract | 定位 |
|----------|------|
| Data Readiness Contract | 数据是否可用于 train / backtest / preopen 的判断口径 |
| Signal Contract | signal / prediction 在研究、回测、preopen、UI 之间的基本口径 |
| Research Analytics Contract | Research Analytics 层的边界：IC/RankIC、实验索引、横向比较 |
| Daily Ops Read Model Contract | UI / monitoring 只读 daily ops 状态的 read model |
| Portfolio State Contract | 账户、持仓、成交、组合状态的核心语义 |

---

## 3. Data Readiness Contract

### Purpose

判断某个 target_date 的数据是否可用于 train / backtest / preopen / UI / monitoring。

### Producers / Consumers

- **Producers**：data sync pipeline（`scripts/ops/sync_csi800_daily.py` + `qsys/` 内模块）、readiness check、future monitoring job。
- **Consumers**：DailyRunner preopen、model train / refresh、backtest / rolling research、Ops UI、monitoring、daily report。

### Storage / Transport

JSON report、daily evidence artifact、`data/audit/`、future UI read model。不强行指定未实现 DB 表。

### Grain

```
per target_date / per data domain (raw / qlib / calendar / instrument) / per universe
```

### Required Fields

`target_date`, `calendar_date`, `latest_raw_date`, `latest_qlib_date`, `universe_id`, `is_trading_day`, `raw_ready`, `qlib_ready`, `calendar_ready`, `instrument_ready`, `missing_rate`, `status` (ready/degraded/blocked), `blocking_scope` (train/backtest/preopen/postclose/all), `reason`, `generated_at`, `run_id`。

### Invariants

- 盘前推荐必须基于 T-1 已收盘数据。
- 数据不满足 minimum readiness 时不得生成假推荐。
- readiness 要区分 **ready / degraded / blocked**：degraded 可继续但必须写入报告，blocked 必须阻断对应流程。
- readiness 不能只看文件存在，还要看日期、coverage、calendar、universe。

### UI / Monitoring

Ops UI 展示：latest raw date、latest qlib date、missing rate、blocking reason、最近一次 sync run。
Monitoring 基于 blocked / degraded 触发告警。

### Current Legacy Compatibility

当前 readiness 检查分散在多个入口脚本中（`run_daily_trading.py`、`run_post_close.py`），无统一 readiness store。迁移期间各入口可各自检查，但不扩张新依赖。

---

## 4. Signal Contract

### Purpose

定义某个 strategy / signal / model 在 signal_date 对 instrument 产生的 score / rank。

### Producers / Consumers

- **Producers**：Predictor、RollingResearchRunner、DailyRunner preopen、StrategyCandidate adapter、signal expression / combination layer。
- **Consumers**：BacktestEngine、SignalEvaluator、ExperimentIndex、DailyRunner preopen、Research UI、Ops UI、Candidate/Shadow dashboard。

### Storage / Transport

SignalStore、experiments artifact、parquet/CSV、DuckDB research analytics store、daily preopen signal artifact。不假设唯一存储介质。

### Grain

```
strategy_id + signal_id + signal_date + instrument
```

模型预测可追溯：`model_id / model_version / feature_set / run_id`。

### Required Fields

`strategy_id`, `signal_id`, `signal_date`, `instrument`, `score`, `universe_id`, `run_id`, `model_id`, `model_version`, `feature_set`, `signal_expression`, `is_oos`, `generated_at`。

`rank` 不是必须物化的字段。rank 可由 score + universe 在消费时派生；若物化，必须记录 ranking universe 和排序方向 `rank_direction`（如 ascending / descending）。

用于 evaluation 的 signal 必须能关联 `label_id` / `horizon`，可直接通过字段携带或通过 run_id 追溯。

### Invariants

- `signal_date` 是信号来源日期，不是执行日期。
- daily preopen 消费的 signal 必须基于最近已收盘数据。
- research signal 必须标记是否 OOS。
- signal 必须可追溯 run_id / model / feature set / expression。
- 不允许把 in-sample training signal 当成 Candidate/Shadow 可交易 signal。
- signal contract 不负责下单，只表达排序或预测强度。

### UI / Monitoring

Research UI：signal distribution、coverage、IC/RankIC 关联、model version 对比。
Ops UI：当日 strategy signal、top/bottom、missing instruments、signal freshness、是否进入 plan。

### Current Legacy Compatibility

当前 alpha_v1 signal 通过 `run_alpha_v1_daily.py` 产生，写 `daily/{date}/pre_open/signals/`。新信号路径（`StrategyCandidate` → `DailyRunner`）尚未完全就绪。迁移期间旧路径不扩张新依赖。

---

## 5. Research Analytics Contract

### Purpose

支持 signal / label / IC / RankIC / backtest summary / experiment index 的横向查询和比较。

### Producers / Consumers

- **Producers**：RollingResearchRunner、SignalEvaluator、BacktestEngine、ExperimentIndex builder、research report generator。
- **Consumers**：Research UI、strategy development SOP、Candidate promotion review、model comparison、monitoring / regression check。

### Storage / Transport

DuckDB 是适合的 research analytics store。parquet / CSV / JSON index 可作为过渡介质。**它不替代 ledger，不参与 broker execution，不存真实账户状态。**

### Grain

```
per experiment / per run / per signal_id + date / per model_version / per strategy_id / per evaluation window
```

### Required Fields

`experiment_id`, `run_id`, `strategy_id`, `signal_id`, `model_version`, `feature_set`, `label_id`, `eval_start`, `eval_end`, `metrics`, `artifact_paths`, `created_at`。

Metrics 第一版：IC、RankIC、ICIR、group_return、long_short_return、turnover、max_drawdown、annual_return、cost_assumption。

### Invariants

- research analytics 是只读分析层。
- 不写 production ledger。
- 不直接触发交易。
- 指标必须能追溯 run_id 和 artifact_paths。
- comparison 必须记录 eval window 和 cost assumption。
- label 必须有 horizon 和 shift 语义，防止未来数据泄露。

### UI / Monitoring

Research UI：experiment list、signal comparison、model comparison、IC/RankIC trend、backtest summary、feature coverage、regression alert。

### Current Legacy Compatibility

当前 research 结果分散在 `experiments/` 目录中。迁移期间 CSV 继续有效，逐步统一到 DuckDB 查询视图。

---

## 6. Daily Ops Read Model Contract

### Purpose

定义 Ops UI 和 monitoring 应如何只读展示 daily 状态，避免 UI 到处读 raw 文件、ledger、shadow、experiments。

### Producers / Consumers

- **Producers**：DailyRunner、preopen pipeline、postclose pipeline、report generator、ledger readonly view、broker reconciliation job。
- **Consumers**：Ops UI、daily monitoring、notification、human operator、Candidate/Shadow dashboard。

### Storage / Transport

daily report JSON（已有 `daily_ops_digest_*.json`）、daily evidence artifact、readonly API（未来）、generated UI read model（未来）、ledger readonly view（未来）。

### Grain

```
execution_date + strategy_id + account_id + run_id
```

### Required Fields

`execution_date`, `strategy_id`, `stage` (candidate / production), `execution_mode` (shadow / broker / simulated / manual), `run_id`, `data_readiness_status`, `model_freshness_status`, `plan_status`, `order_intent_count`, `expected_turnover`, `account_snapshot_status`, `postclose_status`, `reconciliation_status`, `blocking`, `reason`, `artifact_paths`, `generated_at`。

### Invariants

- UI read model **只读**。
- UI 不写 ledger。
- UI 不下单。
- UI 不改策略。
- UI 不绕过 DailyRunner。
- UI 不直接依赖 legacy shadow files 作为长期接口。
- blocking 状态必须清晰，阻止性异常必须显式传递。
- 所有展示项必须能回跳 artifact path 或 run_id。

### UI / Monitoring

Ops UI：data readiness、latest data date、daily plan、order intents、shadow/production status、ledger snapshot、postclose report、reconciliation gap、blocking reason。
Monitoring：blocked alert、stale data alert、reconciliation gap alert、model freshness alert、empty plan alert。

### Current Legacy Compatibility

当前 daily ops 产物由旧入口（`run_daily_trading.py`、`run_post_close.py`、`run_alpha_v1_daily.py`）生成，写 `daily/{date}/`。新入口未接入 systemd，无独立 read model。迁移期间 UI 原型可直接消费旧产物 JSON/CSV，但不扩张新依赖。

---

## 7. Portfolio State Contract

### Purpose

定义账户、持仓、成交、快照、组合状态的语义边界。

### Producers / Consumers

- **Producers**：LedgerService、DailyRunner postclose、Execution Backend、broker reconciliation job、legacy live/account path、migration script。
- **Consumers**：DailyRunner preopen（只读）、postclose reconciliation、Ops UI、Candidate/Shadow dashboard、risk/exposure monitor、broker bridge。

### Storage / Transport

*"DB"不是架构语义本身。SQLite、JSON、CSV 只是介质。架构上真正重要的是状态语义。*

| 对象 | 介质 | 语义 | 当前角色 | 目标角色 |
|------|------|------|---------|---------|
| Account State / Execution Ledger | SQLite `data/trade.db` | 账户、持仓、订单、成交、快照的结构化状态 | 新主线 | 唯一 SOT |
| Legacy Account Store | SQLite `data/meta/real_account.db` | 旧 live/account 路径使用的账户状态 | active legacy | 迁移后只读/移除 |
| Legacy Shadow Files | JSON/CSV `shadow/` | 旧 alpha / shadow ops 兼容状态 | active compatibility | 只读或移除 |
| Daily Evidence | files `daily/{date}/post_close/` | 盘后证据 | 当前有效 | 当前有效 |
| Broker Snapshot | external | 外部状态快照 | 外部源 | 外部源 |

### Grain

```
account_id + strategy_id + execution_date + snapshot_time + instrument / order / fill
```

### Required Concepts

`AccountSnapshot`、`PositionSnapshot`、`OrderIntent`（计划输入，不是账户状态 SOT）、`ExecutionFill`、`CashEvent`、`PortfolioSnapshot`、`ReconciliationResult`。

### Minimal Fields

`account_id`, `strategy_id`, `execution_date`, `snapshot_time`, `cash`, `market_value`, `total_asset`, `instrument`, `quantity`, `available_quantity`, `cost_basis`, `last_price`, `order_id`, `fill_id`, `side`, `fill_qty`, `fill_price`, `fee`, `run_id`, `source_run_id`（派生来源）。

### Invariants

- `data/trade.db` 是目标账户状态与执行流水 SOT。
- preopen 只能读取已确认的上一状态。
- postclose 才能提交 execution / portfolio snapshot。
- broker snapshot 与内部 ledger 的差异必须通过 reconciliation 暴露。
- legacy account store 和 shadow files 不得扩展新依赖。
- 删除 legacy store 前必须完成 consumer 切换、数据迁移和回归验证。
- 不允许策略 adapter 直接写生产账户状态。
- 所有状态变更必须可追溯 run_id 或 source_run_id。

### UI / Monitoring

UI 只读：cash、positions、available quantity、daily return、turnover、position gap、cash gap、reconciliation result、account state freshness。
Monitoring：account stale、position mismatch、cash mismatch、abnormal turnover、missing fill、unexpected empty portfolio。

### Current Legacy Compatibility

当前三态共存：`data/trade.db`（新主线）、`data/meta/real_account.db`（旧 live/account 路径默认）、`shadow/`（JSON/CSV 文件）。旧入口仍写后两者。迁移完成前不删除、不扩张新依赖。迁移前必须完成 schema 对齐、数据迁移、consumer 切换和回归验证。
