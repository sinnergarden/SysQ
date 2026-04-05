# POST CLOSE SOP

## 1. 目标

盘后 SOP 的核心不是简单保存 broker 导出文件，而是闭合当天证据链：

- 收盘后的 real account 状态成功回写到长期账本。
- real vs shadow 的差异有结构化对账输出。
- 当天盘后证据落进 `daily/{execution_date}/post_close/`。
- 关键结构化字段可继续 rollup 到 `data/derived/` 做横向分析。

## 2. 前置条件

执行盘后前，确保：

- 已有对应交易日的盘前 evidence 包。
- broker 导出的 `real_sync` 文件日期明确且内容完整。
- 长期账本使用 `data/meta/real_account.db`，不要在 `daily/` 中维护主库副本。

如果账户状态缺失、broker 导出日期不清、回写字段不完整，直接视为 blocker。

## 3. 执行命令

标准命令：

```bash
python scripts/run_post_close.py --date 2026-04-03 --real_sync broker/real_sync_2026-04-03.csv
```

如果盘前计划和盘后对账使用同一 execution date，但你想显式传入：

```bash
python scripts/run_post_close.py \
  --date 2026-04-03 \
  --execution_date 2026-04-03 \
  --real_sync broker/real_sync_2026-04-03.csv
```

## 4. 默认输出位置

盘后默认输出目录：`daily/{execution_date}/post_close/`

固定子目录：

- `reconciliation/`
- `snapshots/`
- `reports/`
- `manifests/`

关键文件模式：

- `reconciliation/reconcile_summary_{date}.csv`
- `reconciliation/reconcile_positions_{date}.csv`
- `reconciliation/reconcile_real_trades_{date}.csv`
- `snapshots/real_sync_snapshot_{date}.csv`
- `reports/daily_ops_post_close_*.json`
- `reports/signal_quality_summary_{date}.json`
- `manifests/daily_ops_manifest_{execution_date}.json`

## 5. 盘后验收清单

### 5.1 Real Account 回写

至少确认：

- `cash`
- `total_assets`
- `positions`
- `trade_log`（如 broker 输入里有稳定成交信息）

这些内容要写回长期账本 `data/meta/real_account.db`，不是复制到按天数据库。

### 5.2 Reconciliation Summary

至少确认 summary 中包含：

- `cash`
- `total_assets`
- `position_count`
- `diff`

### 5.3 Position Gaps

至少确认：

- 缺口标的可追踪到 `symbol`
- `real_amount` 与 `shadow_amount` 可比较
- `amount_diff` 与 `market_value_diff` 可解释

### 5.4 Snapshots

`real_sync_snapshot_{date}.csv` 只作为当天证据快照使用，不能替代主账本。

## 6. 盘后完成后的长期沉淀

盘后 evidence 写完后，建议立刻 rollup：

```bash
python scripts/rollup_daily_artifacts.py --execution_date 2026-04-03
```

当前 rollup 会追加到：

- `data/derived/signal_baskets.csv`
- `data/derived/order_intents.csv`
- `data/derived/reconciliation_summary.csv`
- `data/derived/position_gaps.csv`

这样做的目的：

- 跨天分析不用反复扫整个 `daily/`
- 排障时可以先从长期表定位，再回到原始证据文件
- 同一日重复 rollup 也能去重，避免明显重复记录

## 7. 失败时怎么处理

- 找不到盘前计划：只检查 `daily/{execution_date}/pre_open/plans/`，不再回退到旧 `data/` 根目录。
- 回写失败：先修 broker 导出或字段映射，不要手工创建日级账本副本。
- 对账差异异常：先查 `reconciliation/` 与 `snapshots/`，再查 `data/derived/position_gaps.csv`。
- 信号质量评估失败：保留 `reports/` 里的质量摘要，作为次日 blocker 输入。

## 8. 盘后结束标准

满足以下条件，才算当天 post-close 完成：

- 实盘状态已回写到 `data/meta/real_account.db`
- `daily/{execution_date}/post_close/` 证据齐全
- manifest 与 report 已生成
- 至少一次 rollup 已完成，`data/derived/` 有对应结构化记录
