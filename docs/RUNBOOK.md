# SysQ 运行总手册（RUNBOOK）

本文档定义 SysQ 当前 daily ops、数据目录、日期语义、产物契约的统一口径。目标是让人和 AI 都按同一套规则运行，不靠口头约定记忆路径。

## 1. 适用范围

本手册覆盖：

- 盘前计划生成
- 数据健康检查与 readiness 阻断
- 模型日常运营与生产模型选择
- 收盘后真实账户回填与对账
- daily ops 目录与产物契约
- minimal production kernel 的运行证据定位

不覆盖：

- 临时研究草稿
- 无关实验记录
- 非 SysQ 主流程的个人脚本

## 2. 文档入口

当前以这几份文档为准：

- `docs/RUNBOOK.md`：总手册，定义原则、边界、统一口径
- `docs/DATA_LAYOUT.md`：目录树、职责、关键文件模式、legacy 兼容说明
- `docs/ops/PRE_OPEN_SOP.md`：盘前 SOP
- `docs/ops/POST_CLOSE_SOP.md`：收盘后 SOP
- `docs/ops/DATA_PIPELINE_SOP.md`：数据链路 SOP
- `docs/ops/MODEL_OPS_SOP.md`：模型操作 SOP
- `docs/ops/READINESS.md`：readiness 分层口径说明
- `docs/ops/FEATURE_PIPELINE_SOP.md`：特征链路 SOP
- `docs/features/feature_system.md`：长期特征系统说明

当前 daily ops 入口只保留：

- `scripts/run_daily_trading.py`
- `scripts/run_post_close.py`
- `scripts/run_signal_quality.py`

历史别名 `scripts/run_plan.py` 与 `scripts/run_reconcile.py` 不再作为当前执行依据。

## 3. 目录约定

### 3.1 顶层边界

统一边界如下：

- `data/`：canonical data 与长期持久资产
- `daily/`：按交易日归档的运行证据
- `experiments/`：研究与实验输出
- `runs/`：minimal kernel 的步骤状态机产物

最重要的规则：

- `data/` 不再默认写 daily plan、template、order intents、signal basket、reconciliation 等按日期中间产物
- `daily/{execution_date}/` 是单个交易日的默认证据目录
- `experiments/` 是新实验默认目录，`data/experiments/` 只保留 legacy 兼容

### 3.2 `data/` 内部职责

- `data/raw/`：原始 canonical 数据
- `data/qlib_bin/`：Qlib bin 数据
- `data/feature/`：长期持久化特征资产与 feature 版本目录
- `data/models/`：模型目录、训练摘要、生产 manifest
- `data/meta/`：小型 metadata 资产与账户数据库默认位置
- `data/meta.db`：当前数据适配层仍直接使用的 legacy metadata db

### 3.3 `daily/{execution_date}/` 内部职责

- `pre_open/`：盘前计划、signal basket、order intents、盘前报告、盘前 manifest
- `post_close/`：对账输出、真实快照、盘后报告、盘后 manifest
- `snapshot_index.json`：日级 artifact 索引
- `summary/`：日级 digest

详细目录树与文件模式见 `docs/DATA_LAYOUT.md`。

## 4. 日期语义

必须固定以下含义：

- `signal_date`：生成信号所使用的收盘日
- `execution_date`：计划实际执行日
- `plan_date`：当前等同于 `signal_date`，仅用于 plan 命名

硬规则：

- 盘前默认基于 T-1 收盘生成 T 日计划
- 若 `run_daily_trading.py --date` 传的是未来交易日且未显式传 `--execution_date`，该日期视为 `execution_date`，脚本自动回退上一交易日作为 `signal_date`
- 盘后若找到了盘前 plan，则应优先沿用 plan 内的 `signal_date`
- 不允许把 `signal_date` 与 `execution_date` 混写为同一业务概念

## 5. Readiness 与阻断规则

daily ops 默认必须先做数据健康检查，再继续主流程。至少检查：

- 日期是否正确
- 是否存在缺口
- 核心字段是否齐全
- 空值或缺失率是否异常

readiness 分三层：

- `core_daily_status`：交易主链路，阻断级
- `pit_status`：基本面 PIT，告警级
- `margin_status`：融资融券层，告警级

若 `health_ok == false` 或 `core_daily_status` 阻断，则 daily ops 必须停在报告阶段，不得把结果当成可运营输出。

## 6. 标准输入输出契约

### 6.1 盘前 CLI：`scripts/run_daily_trading.py`

输入至少包括：

- `--date`
- 可选 `--execution_date`
- 可选 `--model_path`
- 可选 `--db_path`
- 可选 `--output_dir`
- 可选 `--report_dir`
- 可选 `--require_update_success`

默认输出位置：

- `daily/{execution_date}/pre_open/plans/plan_{signal_date}_{account}.csv`
- `daily/{execution_date}/pre_open/templates/real_sync_template_{signal_date}_{account}.csv`
- `daily/{execution_date}/pre_open/order_intents/order_intents_{execution_date}_{account}.json`
- `daily/{execution_date}/pre_open/signals/signal_basket_{signal_date}.csv`
- `daily/{execution_date}/pre_open/diagnostics/signal_quality_summary_{signal_date}.json`
- `daily/{execution_date}/pre_open/reports/daily_ops_pre_open_{run_id}.json`
- `daily/{execution_date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`

`--output_dir` 可覆盖 pre-open artifact 根目录，`--report_dir` 可覆盖结构化报告目录；若覆盖后与默认目录不同，报告里必须能看到真实路径。

### 6.2 盘后 CLI：`scripts/run_post_close.py`

输入至少包括：

- `--date`
- `--real_sync`
- 可选 `--execution_date`
- 可选 `--db_path`
- 可选 `--plan_dir`
- 可选 `--output_dir`
- 可选 `--report_dir`

默认输出位置：

- `daily/{execution_date}/post_close/reconciliation/reconcile_summary_{date}.csv`
- `daily/{execution_date}/post_close/reconciliation/reconcile_positions_{date}.csv`
- `daily/{execution_date}/post_close/reconciliation/reconcile_real_trades_{date}.csv`
- `daily/{execution_date}/post_close/snapshots/real_sync_snapshot_{date}.csv`
- `daily/{execution_date}/post_close/diagnostics/signal_quality_summary_{date}.json`
- `daily/{execution_date}/post_close/reports/daily_ops_post_close_{run_id}.json`
- `daily/{execution_date}/post_close/manifests/daily_ops_manifest_{execution_date}.json`

默认读取的盘前计划目录：

- `daily/{execution_date}/pre_open/plans/`

若新目录中未找到，会最小兼容回退到 legacy `data/plan_*.csv`。

### 6.3 `order_intents` 契约

`order_intents` 是 pre-open 到 broker bridge 的稳定输入，不允许下游再去解析 plan markdown 或 stdout。

顶层至少包含：

- `artifact_type`
- `signal_date`
- `execution_date`
- `account_name`
- `model_info`
- `assumptions`
- `intent_count`
- `intents`

每条 intent 至少包含：

- `intent_id`
- `symbol`
- `side`
- `amount`
- `price`
- `execution_bucket`
- `cash_dependency`
- `t1_rule`
- `price_basis`
- `status`

### 6.4 `runs/{date}/manifest.json`

这是 minimal production kernel 的唯一真相源。它和 `daily/` 不冲突：

- `daily/` 面向 daily ops 运营与审计
- `runs/` 面向 runner 的步骤状态机与自动化编排

## 7. Legacy 兼容规则

当前仍兼容读取，但不再作为默认写入路径：

- `data/plan_*.csv`
- `data/real_sync_template_*.csv`
- `data/order_intents_*.json`
- `data/signal_basket_*.csv`
- `data/reports/daily_ops_*.json`
- `data/reports/daily_ops_manifest_*.json`
- `daily/ops/{date}/snapshot_index.json`
- `data/experiments/`

迁移原则：

- 能明确归属到某个 `execution_date` 的 daily 产物，优先迁移到 `daily/{execution_date}/...`
- 无法确认归属的历史文件，保留为 legacy，不粗暴删除
- 新写入必须遵守新目录，不继续污染 `data/` 根目录

## 8. 常见排障顺序

排查单个交易日时，建议按顺序看：

1. `daily/{execution_date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`
2. `daily/{execution_date}/snapshot_index.json`
3. `daily/{execution_date}/pre_open/plans/`
4. `daily/{execution_date}/post_close/reconciliation/`
5. `daily/{execution_date}/summary/daily_ops_digest_{execution_date}.md`

若是模型或研究问题，再看：

1. `data/models/`
2. `experiments/`
3. `runs/{date}/manifest.json`

## 9. 常用命令

盘前：

```bash
python scripts/run_daily_trading.py \
  --date 2026-04-03 \
  --execution_date 2026-04-06 \
  --require_update_success
```

盘后：

```bash
python scripts/run_post_close.py \
  --date 2026-04-06 \
  --real_sync broker/real_sync_2026-04-06.csv
```

信号质量重算：

```bash
python scripts/run_signal_quality.py --date 2026-04-06 --require_ready
```

提交前最小要求：

```bash
python3 -m compileall qsys scripts tests
python3 -m unittest discover tests
```
