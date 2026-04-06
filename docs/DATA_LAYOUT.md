# Data Layout

## 1. 当前目录契约

SysQ 只保留下面几层职责：

- `data/raw/`：长期原始数据。
- `data/qlib_bin/`：QLib serving 数据。
- `data/models/`：模型与生产 manifest。
- `data/meta/`：长期小型账本与映射；默认账户主库是 `data/meta/real_account.db`。
- `data/derived/`：从 `daily/{date}` 抽出的长期结构化汇总表。
- `daily/{date}/`：单个交易日的 evidence package。
- `experiments/`：研究、训练、回测、参数扫描等输出。
- `runs/{date}/`：runner 级最小编排证据。

一句话：`data/` 放长期主数据，`daily/` 放单日证据，`experiments/` 放研究输出。

## 2. 当前真实布局

```text
.
├── data/
│   ├── raw/
│   ├── qlib_bin/
│   ├── models/
│   ├── meta/
│   │   └── real_account.db
│   └── derived/
├── daily/
│   └── {date}/
│       ├── pre_open/
│       │   ├── plans/
│       │   ├── order_intents/
│       │   ├── signals/
│       │   ├── reports/
│       │   └── manifests/
│       ├── post_close/
│       │   ├── reconciliation/
│       │   ├── snapshots/
│       │   ├── reports/
│       │   └── manifests/
│       └── snapshot_index.json
├── experiments/
│   └── reports/
└── runs/
    └── {date}/
```

## 3. Daily 目录约定

### 3.1 盘前 `daily/{date}/pre_open/`

只保留五类目录：

- `plans/`
  - `plan_{signal_date}_{account}.csv`
  - `real_sync_template_{signal_date}_{account}.csv`
- `order_intents/`
  - `order_intents_{execution_date}_{account}.json`
- `signals/`
  - `signal_basket_{signal_date}.csv`
- `reports/`
  - `daily_ops_pre_open_*.json`
  - `signal_quality_summary_{signal_date}.json`
  - `signal_quality_vintages_{signal_date}.csv`
  - `daily_ops_digest_{execution_date}.md`
  - `daily_ops_digest_{execution_date}.json`
- `manifests/`
  - `daily_ops_manifest_{execution_date}.json`

### 3.2 盘后 `daily/{date}/post_close/`

只保留四类目录：

- `reconciliation/`
  - `reconcile_summary_{date}.csv`
  - `reconcile_positions_{date}.csv`
  - `reconcile_real_trades_{date}.csv`
- `snapshots/`
  - `real_sync_snapshot_{date}.csv`
- `reports/`
  - `daily_ops_post_close_*.json`
  - `signal_quality_summary_{date}.json`
  - `signal_quality_vintages_{date}.csv`
  - `daily_ops_digest_{execution_date}.md`
  - `daily_ops_digest_{execution_date}.json`
- `manifests/`
  - `daily_ops_manifest_{execution_date}.json`

约束：

- 不在 `daily/` 中复制账户主库。
- 不单独保留额外的 legacy 子目录。
- 只有实际写入文件时才创建对应子目录，不默认铺空目录。

## 4. `data/derived/` 当前 rollup

`scripts/rollup_daily_artifacts.py` 只做简单稳定的结构化抽取，当前支持四类表：

- `data/derived/signal_baskets.csv`
  - 来源：`daily/{date}/pre_open/signals/signal_basket_*.csv`
- `data/derived/order_intents.csv`
  - 来源：`daily/{date}/pre_open/order_intents/order_intents_*.json`
- `data/derived/reconciliation_summary.csv`
  - 来源：`daily/{date}/post_close/reconciliation/reconcile_summary_*.csv`
- `data/derived/position_gaps.csv`
  - 来源：`daily/{date}/post_close/reconciliation/reconcile_positions_*.csv`

共同约束：

- 使用 CSV，便于追加和排查。
- 保留 `artifact_source`，用于回跳原始 daily evidence。
- 支持重复 rollup，按主键去重。

## 5. 研究输出

研究、训练、回测、参数扫描统一放到 `experiments/`：

- 表格、日志、扫描结果放 `experiments/`
- 结构化 JSON 报告默认放 `experiments/reports/`

## 6. 常用命令

盘前：

```bash
python3 scripts/run_daily_trading.py --date 2026-04-02
```

盘后：

```bash
python3 scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

rollup：

```bash
python3 scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```
