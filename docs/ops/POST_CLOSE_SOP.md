# 收盘后 SOP

## 1. 目的

在交易日结束后，把真实账户状态回填进系统，并完成 real / shadow 对账，为下一交易日接续运行提供可信基线。

## 2. 输入

- `--date`：执行日
- `--real_sync`：券商导出 CSV
- `--db_path`
- `--plan_dir`
- `--output_dir`
- `--report_dir`

## 3. 输出

- 真实账户状态写入账户数据库
- 对账 CSV / 摘要
- `daily_ops_post_close_*.json`

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

### Step B：确定对应的 signal_date

规则：

- 默认以 `--date` 作为兜底
- 若 `plan_dir/plan_<date>_shadow.csv` 中存在 `signal_date`，盘后报告应沿用该字段

### Step C：同步真实账户状态

通过条件：

- balance_history 写入成功
- position_history 写入成功
- trade_log 可追溯

### Step D：执行对账

至少比较：

- cash
- total_assets
- 持仓数量差异
- 真实成交记录数量

## 5. 成功标准

- 真实账户最新状态已落盘
- 对账摘要可读
- 报告路径正确
- 第二天盘前能接续读取到最新状态

## 6. 常见故障

### real_sync 缺字段

处理：补 CSV 列，不允许凭猜测落库。

### 找不到计划文件

处理：盘后仍可运行，但 `signal_date` 会退回 `--date`；需要在报告里说明。

### 对账差异过大

处理：优先查成交回填、部分成交、手续费税费、T+1 限制。

## 7. 人工接管

人工接管时至少保留：

- 券商 CSV 原文件路径
- 实际写入的 db_path
- 对账输出目录
- 差异解释
- 是否允许第二天继续自动盘前
