# Data Layout

## 1. 分层原则

SysQ 的目录约定先服务业务语义，再服务文件归档。判断一个产物该放哪里，先问它属于哪一层：

- `data/raw/` 与 `data/qlib_bin/` 是 canonical data / serving data。
  - `data/raw/` 保存原始抓取与标准化后的基础行情、财务、成分等长期主数据。
  - `data/qlib_bin/` 是供特征、训练、推理直接消费的 bin 形态，不承载 daily ops 证据。
- `data/models/` 是模型接口产物层。
  - 放生产模型目录、模型元数据、生产模型 manifest。
  - 它是 daily plan 的输入，不是 daily evidence 包。
- `data/meta/` 是长期小型账本与映射层。
  - 例如 `data/meta/real_account.db`、行业映射、账户别名等。
  - 这类文件是长期唯一主账本，不能按天复制到 `daily/`。
- `daily/{date}/` 是当天 evidence package。
  - 只保存当天盘前/盘后运行留下的证据、快照、报告、manifest。
  - 它不是长期主数据层，也不是研究实验层。
- `data/derived/` 是长期 append 型结构化汇总层。
  - 它从 `daily/{date}` 抽取稳定字段，形成横向分析、排障、复盘可直接读取的长期表。
  - 它不替代 `daily/`，而是把高价值字段做长期沉淀。
- `experiments/` 是研究实验输出层。
  - 只放研究、参数扫描、分析报表、试验模型等。
  - 它不参与 daily 生产真相源判定。
- `runs/{date}/` 是 minimal runner 证据层。
  - 面向 runner 的步骤状态机与最小编排证据。
  - 它与 `daily/{date}` 并行存在，职责不同。

一句话归纳：`data/` 放长期主数据与长期派生表，`daily/` 放单日证据包，`experiments/` 放研究输出，`runs/` 放最小 runner 证据。

## 2. 目录总览

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
└── runs/
    └── {date}/
```

说明：

- `snapshot_index.json` 是单日 artifact 索引文件，用于快速定位该交易日 evidence；它不是长期分析表。
- 不再使用 `daily/ops/`。
- 不再把 daily plan、signal basket、reconciliation 等产物散落写回 `data/` 根目录。
- 不再按天复制账户主库；`real_account.db` 固定放在 `data/meta/`。

## 3. Daily Evidence Package

### 3.1 `daily/{date}/pre_open/`

盘前 evidence 包只保留五类子目录：

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

`pre_open` 的核心目标是回答三件事：

- 今天基于哪天数据发信号。
- 模型给出了什么排序/分数与目标持仓。
- 对不同账户准备了什么可执行交易意图。

### 3.2 `daily/{date}/post_close/`

盘后 evidence 包只保留四类子目录：

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

`post_close` 的核心目标是固化收盘后证据：

- 实盘账户回写了什么状态。
- real vs shadow 的差异有多大。
- 哪些差异需要次日继续追踪。

## 4. Long-Lived Derived Tables

`data/derived/` 的存在，是为了避免横向分析时反复扫描大量 `daily/{date}` 文件。

第一版长期汇总表：

- `data/derived/signal_baskets.csv`
  - 粒度：单日、单标的信号篮子。
  - 主字段：`signal_date`, `execution_date`, `account_name`, `symbol`, `score`, `score_rank`, `weight`, `artifact_source`。
  - 其中 `account_name` 固定为 `shared`，表示该篮子在账户分配前共享。
- `data/derived/order_intents.csv`
  - 粒度：单日、单账户、单 intent。
  - 主字段：`signal_date`, `execution_date`, `account_name`, `intent_id`, `symbol`, `side`, `amount`, `price`, `score`, `artifact_source`。
- `data/derived/reconciliation_summary.csv`
  - 粒度：单日、单指标。
  - 主字段：`signal_date`, `execution_date`, `account_name`, `metric`, `real`, `shadow`, `diff`, `artifact_source`。
  - `account_name` 固定为 `real_vs_shadow`。
- `data/derived/position_gaps.csv`
  - 粒度：单日、单标的持仓缺口。
  - 主字段：`signal_date`, `execution_date`, `account_name`, `symbol`, `real_amount`, `shadow_amount`, `amount_diff`, `market_value_diff`, `artifact_source`。

设计约束：

- 采用 CSV，保持 append-friendly 与 debug-friendly。
- 支持重复 rollup；实现通过主键去重避免明显重复。
- `artifact_source` 必须保留，便于从长期表回跳到原始 daily evidence。

## 5. 关键字段口径

### 5.1 Signal Basket

最少要能回答“为什么选这只股票”：

- `signal_date`: 用哪天数据算出的分数
- `execution_date`: 计划在哪天执行
- `symbol`: 标的
- `score` / `score_rank`: 模型分数与排序依据
- `weight`: 策略目标权重
- `price`: 计划参考价

### 5.2 Order Intents

最少要能回答“打算怎么下单”：

- `account_name`: 账户名
- `symbol` / `side` / `amount`: 买卖方向与计划股数
- `price` / `est_value`: 参考价格与预计金额
- `execution_bucket`: 执行顺序，例如先卖后买
- `t1_rule`: T+1 相关约束

### 5.3 Reconciliation

最少要能回答“实盘与影子盘差在哪”：

- summary: `metric`, `real`, `shadow`, `diff`
- position gaps: `symbol`, `real_amount`, `shadow_amount`, `amount_diff`, `market_value_diff`
- snapshots: 收盘回写时的实盘状态快照

## 6. 不再使用的旧约定

以下约定不再作为默认路径，也不再作为兼容读取入口：

- `daily/ops/`
- `data/experiments/` 作为实验默认目录
- `data/` 根目录下散落的 daily artifacts
- 按交易日复制账户 SQLite 主库

## 7. 常用命令

盘前：

```bash
python scripts/run_daily_trading.py --date 2026-04-02
```

盘后：

```bash
python scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

汇总 daily evidence 到长期表：

```bash
python scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```
