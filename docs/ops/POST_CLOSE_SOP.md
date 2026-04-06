# POST_CLOSE SOP

## 1. 目标

盘后阶段要固化三类事实：

- 实盘账户回写了什么状态。
- real 与 shadow 的差异有多大。
- 哪些结构化字段需要 rollup 到 `data/derived/`。

## 2. 输入

盘后执行前，至少准备：

- `daily/{execution_date}/pre_open/plans/` 中的盘前计划
- broker 导出的 `real_sync` 文件
- 可写的长期主账本 `data/meta/real_account.db`

## 3. 执行命令

```bash
python3 scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

## 4. 当前应产出的目录与文件

默认输出到 `daily/{execution_date}/post_close/`。

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

## 5. 对账最低验收口径

盘后至少要能回答：

- `cash`、`total_assets` 差异是多少。
- 哪些持仓存在 `amount_diff` 或 `market_value_diff`。
- 实盘成交是否写回到 `reconcile_real_trades_{date}.csv`。
- 长期主账本是否已更新到 `data/meta/real_account.db`。

## 6. Rollup

盘后如需跨日复盘，执行：

```bash
python3 scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```

当前 rollup 只会抽取：

- `signal_baskets`
- `order_intents`
- `reconciliation_summary`
- `position_gaps`

## 7. 阻断条件

出现以下任一情况，不应把结果当作可运营输出：

- 找不到盘前计划。
- `real_sync` 缺字段或日期语义不清。
- reconciliation 缺少 `cash` / `total_assets` 等关键指标。
- 主账本没有写回 `data/meta/real_account.db`。

## 8. 快速排查

- 先看 `daily/{execution_date}/post_close/reconciliation/`
- 再看 `daily/{execution_date}/post_close/snapshots/`
- 查长期差异汇总时，看 `data/derived/reconciliation_summary.csv` 与 `data/derived/position_gaps.csv`
