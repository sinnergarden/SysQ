# SysQ Data Layout

本文档定义 SysQ 当前统一目录契约。目标只有三个：

- `data/` 只放 canonical data 与少量长期持久资产
- `daily/` 只放按交易日归档的运行证据
- `experiments/` 只放研究与实验输出

若脚本没有显式覆盖路径，默认都应遵守这里的约定。

## 1. 顶层目录树

```text
SysQ/
├── data/
│   ├── raw/
│   │   └── daily/
│   ├── qlib_bin/
│   ├── feature/
│   ├── meta/
│   ├── models/
│   ├── meta.db                  # legacy metadata db, 仍被数据适配层使用
│   └── experiments/             # legacy research outputs, 只读兼容，不再默认新增
├── daily/
│   └── {execution_date}/
│       ├── pre_open/
│       │   ├── plans/
│       │   ├── templates/
│       │   ├── order_intents/
│       │   ├── signals/
│       │   ├── diagnostics/
│       │   ├── reports/
│       │   └── manifests/
│       ├── post_close/
│       │   ├── reconciliation/
│       │   ├── snapshots/
│       │   ├── diagnostics/
│       │   ├── reports/
│       │   └── manifests/
│       ├── snapshot_index.json
│       └── summary/
├── experiments/
│   ├── leaderboard.csv
│   └── {experiment_run}/
└── runs/
    └── {date}/
```

## 2. 目录职责

### 2.1 `data/`

`data/` 只保留长期资产，不再接收 daily ops 的按日期中间产物。

- `data/raw/`：原始 canonical 数据。当前主数据是 `data/raw/daily/*.feather`
- `data/qlib_bin/`：Qlib bin 数据，是研究/盘前读取的标准特征底座
- `data/feature/`：长期持久化特征资产、版本化 feature 集输出
- `data/models/`：模型目录、模型元数据、训练摘要、production manifest
- `data/meta/`：小型 metadata 资产，例如映射文件、账户数据库的新默认位置
- `data/meta.db`：当前数据适配层仍直接使用的 metadata sqlite；暂视为 legacy canonical metadata db
- `data/experiments/`：历史实验输出保留区；新实验不再默认写这里

### 2.2 `daily/`

`daily/{execution_date}/` 是单个交易日的运行证据目录。凡是与盘前/盘后某个交易日强绑定、且可用于 debug / 审计 / 回放的文件，默认都写这里。

- `pre_open/`：盘前计划、信号篮子、order intents、盘前报告、盘前 manifest
- `post_close/`：盘后对账、真实回填快照、盘后报告、盘后 manifest
- `snapshot_index.json`：按 stage 聚合的文件索引，便于快速定位当日所有关键产物
- `summary/`：交易日 digest，串联盘前摘要与最近盘后回顾

### 2.3 `experiments/`

只存研究和实验输出，不应混入交易日运行证据。

- 参数扫描 CSV
- 研究日志
- 实验 run 目录
- 研究侧 leaderboard

### 2.4 `runs/`

`runs/{date}/` 是 minimal production kernel 的状态机产物，不等同于 `daily/`。

- `runs/{date}/manifest.json`：该 runner 的单一真相源
- 其余 `01_data/`、`02_broker/`、`05_portfolio/`、`06_order_staging/` 等目录，记录内核步骤输出

## 3. Raw Data 与 Qlib Bin 的关系

这两类目录必须分清：

- `data/raw/`：原始 canonical 数据，保留原始列和原始频率语义，是更新和审计的上游真相源
- `data/qlib_bin/`：从 raw 数据整理出的 qlib 可读 bin 数据，是模型训练、推理、回测、盘前计划的下游消费层

统一口径：

- 先更新 `data/raw/`
- 再刷新 `data/qlib_bin/`
- 盘前 readiness 以 `raw_latest`、`qlib_latest`、字段齐全度、缺失率、日期对齐情况共同判断

## 4. Daily Ops 文件模式

### 4.1 盘前 `daily/{execution_date}/pre_open/`

默认文件模式：

- `plans/plan_{signal_date}_{account}.csv`
- `templates/real_sync_template_{signal_date}_{account}.csv`
- `order_intents/order_intents_{execution_date}_{account}.json`
- `signals/signal_basket_{signal_date}.csv`
- `diagnostics/signal_quality_vintages_{signal_date}.csv`
- `diagnostics/signal_quality_summary_{signal_date}.json`
- `reports/daily_ops_pre_open_{run_id}.json`
- `manifests/daily_ops_manifest_{execution_date}.json`

关键 schema 提示：

- `plan_{signal_date}_{account}.csv`
  - 必含：`symbol`, `side`, `amount`, `price`, `weight`, `score`, `score_rank`, `signal_date`, `execution_date`
  - 重要补充：`plan_role`, `execution_bucket`, `cash_dependency`, `t1_rule`, `price_basis_date`, `price_basis_field`, `price_basis_label`
- `real_sync_template_{signal_date}_{account}.csv`
  - 基于 plan 扩展出 `cash`, `total_assets`, `cost_basis`, `filled_amount`, `filled_price`, `fee`, `tax`, `total_cost`, `order_id`
  - 作用：作为盘后真实回填模板或对账辅助模板
- `order_intents_{execution_date}_{account}.json`
  - 顶层：`artifact_type`, `signal_date`, `execution_date`, `account_name`, `model_info`, `assumptions`, `intent_count`, `intents`
  - `intents[]` 关键字段：`intent_id`, `symbol`, `side`, `amount`, `price`, `execution_bucket`, `cash_dependency`, `t1_rule`, `price_basis`, `status`
- `signal_basket_{signal_date}.csv`
  - 关键字段：`symbol`, `score`, `score_rank`, `weight`, `price`, `signal_date`, `execution_date`, `price_basis_date`, `model_name`, `model_path`, `universe`

### 4.2 盘后 `daily/{execution_date}/post_close/`

默认文件模式：

- `reconciliation/reconcile_summary_{date}.csv`
- `reconciliation/reconcile_positions_{date}.csv`
- `reconciliation/reconcile_real_trades_{date}.csv`
- `snapshots/real_sync_snapshot_{date}.csv`
- `diagnostics/signal_quality_vintages_{date}.csv`
- `diagnostics/signal_quality_summary_{date}.json`
- `reports/daily_ops_post_close_{run_id}.json`
- `manifests/daily_ops_manifest_{execution_date}.json`

关键 schema 提示：

- `reconcile_summary_{date}.csv`
  - 行级 metric 摘要，当前至少包含：`cash`, `total_assets`, `position_count`
  - 列至少包含：`metric`, `real`, `shadow`, `diff`
- `reconcile_positions_{date}.csv`
  - 关键字段：`symbol`, `real_amount`, `shadow_amount`, `amount_diff`, `real_market_value`, `shadow_market_value`, `market_value_diff`, `real_cost_basis`, `shadow_cost_basis`
- `reconcile_real_trades_{date}.csv`
  - 关键字段：`symbol`, `side`, `amount`, `price`, `fee`, `tax`, `total_cost`, `order_id`
- `real_sync_snapshot_{date}.csv`
  - 来自券商导出或 bridge readback 标准化后的快照
  - 最小字段：`symbol`, `amount`, `price`, `cost_basis`, `cash`, `total_assets`

### 4.3 日级索引与摘要

- `daily/{execution_date}/snapshot_index.json`
  - 按 `pre_open` / `post_close` 记录 artifact 分类、原路径、归档路径、是否存在
- `daily/{execution_date}/summary/daily_ops_digest_{execution_date}.md`
- `daily/{execution_date}/summary/daily_ops_digest_{execution_date}.json`

## 5. Minimal Kernel 文件模式

`runs/{date}/manifest.json` 为唯一真相源。当前常见模式：

- `runs/{date}/01_data/data_status.json`
- `runs/{date}/02_broker/broker_snapshot.json`
- `runs/{date}/03_retrain/training_summary.json`
- `runs/{date}/05_portfolio/portfolio.json`
- `runs/{date}/06_order_staging/staged_orders.json`
- `runs/{date}/07_reconcile/reconcile_summary.json`

与 `daily/` 的区别：

- `daily/` 面向人类运营与审计
- `runs/` 面向 minimal kernel runner 的步骤状态机

## 6. Legacy 兼容约定

以下路径仍保留兼容读取，但新的默认写入不再使用：

- `data/plan_*.csv`
- `data/real_sync_template_*.csv`
- `data/order_intents_*.json`
- `data/signal_basket_*.csv`
- `data/reports/daily_ops_*.json`
- `data/reports/daily_ops_manifest_*.json`
- `data/experiments/`
- `daily/ops/{date}/snapshot_index.json`

兼容策略：

- 读：daily ops helper 会优先找新目录，必要时回退到 legacy 路径
- 写：新的盘前/盘后脚本默认写入 `daily/{execution_date}/...`
- 迁移：对 legacy daily 产物优先移动到新目录，不确认归属的历史文件保持原地并在文档中标记 legacy

## 7. Debug 建议

排查某个交易日时，先看：

1. `daily/{execution_date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`
2. `daily/{execution_date}/snapshot_index.json`
3. `daily/{execution_date}/pre_open/plans/`
4. `daily/{execution_date}/post_close/reconciliation/`
5. `daily/{execution_date}/summary/daily_ops_digest_{execution_date}.md`

如果是模型或研究问题，再去：

1. `data/models/`
2. `experiments/`
3. `runs/{date}/manifest.json`
