# 收盘后 SOP

## 1. 目的

在交易日结束后，把真实账户状态回填进系统，完成 real / shadow 对账，并把盘后证据统一落到 `daily/{execution_date}/post_close/`，为下一交易日接续运行提供可信基线。

## 2. 输入

- `--date`：交易日，通常也是 `execution_date`
- `--real_sync`：券商导出 CSV 或 bridge readback JSON
- `--db_path`
- `--plan_dir`（默认 `daily/{execution_date}/pre_open/plans`）
- `--output_dir`（默认 `daily/{execution_date}/post_close`）
- `--report_dir`（默认 `daily/{execution_date}/post_close/reports`）

## 3. 默认输出

- `daily/{execution_date}/post_close/reconciliation/reconcile_summary_{date}.csv`
- `daily/{execution_date}/post_close/reconciliation/reconcile_positions_{date}.csv`
- `daily/{execution_date}/post_close/reconciliation/reconcile_real_trades_{date}.csv`
- `daily/{execution_date}/post_close/snapshots/real_sync_snapshot_{date}.csv`
- `daily/{execution_date}/post_close/diagnostics/signal_quality_summary_{date}.json`
- `daily/{execution_date}/post_close/reports/daily_ops_post_close_{run_id}.json`
- `daily/{execution_date}/post_close/manifests/daily_ops_manifest_{execution_date}.json`

## 4. 标准步骤

### Step A：读取真实券商回填文件

最小字段：

- `symbol`
- `amount`
- `price`
- `cost_basis`
- `cash`
- `total_assets`

建议字段：

- `side`
- `filled_amount`
- `filled_price`
- `fee`
- `tax`
- `total_cost`
- `order_id`
- `note`

如果缺最小字段，不允许凭猜测落库。

### Step B：定位对应的盘前计划

规则：

- 默认从 `daily/{execution_date}/pre_open/plans/` 找对应 plan
- 若找到了 plan，则优先使用 plan 内的 `signal_date`
- 若新目录找不到，脚本会最小兼容回退到 legacy `data/plan_*.csv`
- 若仍找不到，才回退到 `--date`

### Step C：同步真实账户状态

通过条件：

- balance history 写入成功
- position history 写入成功
- trade log 可追溯
- 真实快照已写入 `post_close/snapshots/`

### Step D：执行对账

至少比较：

- `cash`
- `total_assets`
- 持仓数量差异
- 真实成交记录数量

默认对账产物：

- `reconcile_summary_{date}.csv`
- `reconcile_positions_{date}.csv`
- `reconcile_real_trades_{date}.csv`

### Step E：生成盘后报告与 manifest

盘后报告必须能回答：

- 用的是哪天的 `signal_date` / `execution_date`
- 真实账户回填文件来自哪里
- real / shadow 差异有多大
- signal quality 诊断是否有缺口
- 关键文件落在哪里

## 5. 成功标准

- 真实账户最新状态已落库
- 对账 CSV 与摘要可读
- 盘后报告和 manifest 已落盘
- 第二天盘前能接续读取到最新状态
- 输出确实在 `daily/{execution_date}/post_close/`

## 6. 常见故障

### real_sync 缺字段

处理：补 CSV / JSON 标准化字段，不允许凭经验推断。

### 找不到盘前计划

处理：盘后仍可运行，但 `signal_date` 会回退到 `--date`；报告里必须说明是 fallback。

### 对账差异过大

处理：优先查成交回填、部分成交、手续费税费、T+1 约束、人工临时下单。

### 盘后文件写回 legacy 根目录

表现：出现 `data/reconcile_*.csv` 或 `data/reports/daily_ops_post_close_*.json`

处理：检查是否误传了 legacy `--output_dir` / `--report_dir`，或是否直接调用了底层 helper 并绕过主脚本。

## 7. 人工接管

人工接管时至少保留：

- 券商原始导出文件路径
- 实际写入的 `db_path`
- 对账输出目录
- 差异解释
- 是否允许第二天继续自动盘前
- 若临时使用了 legacy 路径，需标明原因与回滚方案
