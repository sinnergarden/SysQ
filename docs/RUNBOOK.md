# RUNBOOK

## 1. 先记住这四条

- `data/raw/` 与 `data/qlib_bin/` 是长期 canonical / serving data，不放 daily evidence。
- `daily/{date}/` 是当天证据包，不是长期主数据层。
- `data/derived/` 是从 `daily/{date}` 抽出来的长期结构化汇总表，用于横向分析与 debug。
- `experiments/` 是研究输出，不参与 daily 生产真相源。

## 2. Daily 流程总览

一个完整交易日建议按下面顺序执行：

1. 数据健康检查。
2. 盘前生成 signal basket、plan、order intents、report、manifest。
3. 盘中由人工或 broker 执行，不在 `daily/` 内复制主账本。
4. 盘后回写 real account snapshot，做 reconciliation 与报告。
5. 将当天稳定结构化字段 rollup 到 `data/derived/`。

## 3. 默认目录契约

- 盘前默认写入 `daily/{execution_date}/pre_open/`
- 盘后默认写入 `daily/{execution_date}/post_close/`
- 长期账户主库固定为 `data/meta/real_account.db`
- 实验输出默认写入 `experiments/`
- runner 证据保留在 `runs/{date}/`

`daily/` 中只保留这些证据子目录：

- `pre_open/plans`
- `pre_open/order_intents`
- `pre_open/signals`
- `pre_open/reports`
- `pre_open/manifests`
- `post_close/reconciliation`
- `post_close/snapshots`
- `post_close/reports`
- `post_close/manifests`

## 4. 盘前操作

### 4.1 目标

盘前阶段要确认三件事：

- 数据是否健康且日期语义明确。
- 模型是否可用，且输出了分数/排序依据。
- 每个账户是否拿到了可执行的计划与 order intents。

### 4.2 命令

```bash
python scripts/run_daily_trading.py --date 2026-04-02
```

如果要显式指定执行日：

```bash
python scripts/run_daily_trading.py --date 2026-04-02 --execution_date 2026-04-03
```

### 4.3 关键产物

- `daily/{execution_date}/pre_open/signals/signal_basket_{signal_date}.csv`
- `daily/{execution_date}/pre_open/plans/plan_{signal_date}_{account}.csv`
- `daily/{execution_date}/pre_open/plans/real_sync_template_{signal_date}_{account}.csv`
- `daily/{execution_date}/pre_open/order_intents/order_intents_{execution_date}_{account}.json`
- `daily/{execution_date}/pre_open/reports/daily_ops_pre_open_*.json`
- `daily/{execution_date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`

### 4.4 盘前验收口径

最少检查：

- `signal_date` 与 `execution_date` 是否正确。
- `signal_basket` 是否含 `score`, `score_rank`, `weight`, `price`。
- `plan` 是否含计划股数、参考价格、权重或目标价值信息。
- `order_intents` 是否区分账户并保留执行顺序字段。
- 若数据健康检查失败，必须视为 blocker，不得把输出当成可运营结果。

## 5. 盘后操作

### 5.1 目标

盘后阶段要确认：

- real account 是否按收盘后状态成功回写。
- real vs shadow 的差异是否结构化输出。
- 当日收盘证据是否完整落袋。

### 5.2 命令

```bash
python scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

### 5.3 关键产物

- `daily/{execution_date}/post_close/reconciliation/reconcile_summary_{date}.csv`
- `daily/{execution_date}/post_close/reconciliation/reconcile_positions_{date}.csv`
- `daily/{execution_date}/post_close/reconciliation/reconcile_real_trades_{date}.csv`
- `daily/{execution_date}/post_close/snapshots/real_sync_snapshot_{date}.csv`
- `daily/{execution_date}/post_close/reports/daily_ops_post_close_*.json`
- `daily/{execution_date}/post_close/manifests/daily_ops_manifest_{execution_date}.json`

### 5.4 盘后验收口径

最少检查：

- `cash`, `total_assets`, `position_count` 的 diff 是否可解释。
- `reconcile_positions` 中的持仓缺口是否只剩预期差异。
- `real_sync_snapshot` 是否能追到当天 broker 导出的原始回写。
- 若账户状态缺失、日期不清、回写失败，必须视为 blocker。

## 6. Daily -> Derived Rollup

`daily/{date}` 解决单日证据定位，`data/derived/` 解决跨日分析。

### 6.1 命令

```bash
python scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```

### 6.2 第一版汇总表

- `data/derived/signal_baskets.csv`
- `data/derived/order_intents.csv`
- `data/derived/reconciliation_summary.csv`
- `data/derived/position_gaps.csv`

### 6.3 使用原则

- rollup 可以重复执行。
- 以 CSV 作为简单稳定的长期表格式。
- 每条记录都带 `signal_date`, `execution_date`, `account_name`, `artifact_source`。
- 发现问题时，先从 derived 表定位，再回跳到 `artifact_source` 指向的 daily evidence。

## 7. 实验与 Runner

- 研究实验统一写入 `experiments/`。
- `experiments/` 中的结果可以支持模型比较、参数扫描、回测报表，但不作为 daily ops 真相源。
- `runs/{date}/` 用于 minimal runner 的步骤状态机证据，不替代 `daily/{date}`。

## 8. 常见排障路径

### 8.1 找不到当天计划

优先检查：

- `daily/{execution_date}/pre_open/plans/`
- `daily/{execution_date}/pre_open/manifests/`
- `daily/{execution_date}/snapshot_index.json`

当前实现不再从 `data/` 根目录的旧散落文件回退推断。

### 8.2 账户状态不一致

优先检查：

- 主账本：`data/meta/real_account.db`
- 盘后快照：`daily/{execution_date}/post_close/snapshots/real_sync_snapshot_{date}.csv`
- 对账表：`daily/{execution_date}/post_close/reconciliation/`
- 长期汇总：`data/derived/reconciliation_summary.csv` 与 `data/derived/position_gaps.csv`

### 8.3 横向复盘某个信号或账户

推荐顺序：

1. 先查 `data/derived/` 的长期表。
2. 记下 `artifact_source`。
3. 回到对应 `daily/{date}` 证据包核对原始文件。

## 9. 常用命令汇总

```bash
python scripts/run_daily_trading.py --date 2026-04-02
python scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
python scripts/run_signal_quality.py --date 2026-04-03
python scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```
