# 盘前 SOP

## 1. 目的

在执行日开盘前，基于上一交易日收盘数据生成可执行计划，并把计划、信号篮子、order intents、报告统一落到 `daily/{execution_date}/pre_open/`。

## 2. 输入

- `signal_date`：上一交易日
- `execution_date`：实际执行日
- 生产模型或指定 `--model_path`
- 账户数据库 `--db_path`
- 盘前产物根目录 `--output_dir`（默认 `daily/{execution_date}/pre_open`）
- 报告目录 `--report_dir`（默认 `daily/{execution_date}/pre_open/reports`）

## 3. 默认输出

- `daily/{execution_date}/pre_open/plans/plan_{signal_date}_shadow.csv`
- `daily/{execution_date}/pre_open/plans/plan_{signal_date}_real.csv`
- `daily/{execution_date}/pre_open/templates/real_sync_template_{signal_date}_shadow.csv`
- `daily/{execution_date}/pre_open/templates/real_sync_template_{signal_date}_real.csv`
- `daily/{execution_date}/pre_open/order_intents/order_intents_{execution_date}_shadow.json`
- `daily/{execution_date}/pre_open/order_intents/order_intents_{execution_date}_real.json`
- `daily/{execution_date}/pre_open/signals/signal_basket_{signal_date}.csv`
- `daily/{execution_date}/pre_open/diagnostics/signal_quality_summary_{signal_date}.json`
- `daily/{execution_date}/pre_open/reports/daily_ops_pre_open_{run_id}.json`
- `daily/{execution_date}/pre_open/manifests/daily_ops_manifest_{execution_date}.json`

## 4. 标准步骤

### Step A：确认日期

规则：

- 若 `--execution_date` 明确传入，则 `--date` 就是 `signal_date`
- 若仅传 `--date` 且该日期晚于今天，则该日期视为 `execution_date`，脚本自动回退到上一交易日作为 `signal_date`
- 任何盘前输出都必须同时写清 `signal_date` 与 `execution_date`

### Step B：先做数据健康检查

至少检查：

- 最新日期是否到位
- raw 与 qlib 是否对齐
- 字段是否齐全
- 缺失率是否异常
- `core_daily_status` 是否阻断

建议命令：

```bash
python scripts/run_daily_trading.py \
  --date 2026-04-03 \
  --execution_date 2026-04-06 \
  --require_update_success
```

通过条件：

- 显式刷新未失败
- `health_ok == true`
- `aligned == true`
- `core_daily_status` 不阻断

若不通过，流程必须停在报告阶段，不得继续生成可运营计划。

### Step C：确认模型

通过条件：

- 生产 manifest 能解析，或手工指定 `--model_path`
- 模型可加载
- 特征配置可读取
- 模型信息能写入报告与 order intents

### Step D：生成 signal basket 与计划

盘前至少应生成：

- signal basket
- shadow plan
- real plan
- shadow / real order intents
- real sync template

计划 CSV 至少应包含：

- `symbol`
- `side`
- `amount`
- `price`
- `weight`
- `score`
- `score_rank`
- `signal_date`
- `execution_date`
- `plan_role`
- `execution_bucket`
- `cash_dependency`
- `t1_rule`
- `price_basis_date`
- `price_basis_field`
- `price_basis_label`

### Step E：人工复核

至少检查：

- 是否出现异常集中持仓
- 是否存在大面积空计划
- `sell` 是否先于 `buy` 的现金依赖逻辑
- 小账户在 `min_trade` 约束下是否失真
- 输出路径是否确实在 `daily/{execution_date}/pre_open/`
- `order_intents` 是否包含 `intent_count`、`price_basis`、`execution_bucket`

## 5. 成功标准

- real / shadow 计划已生成
- signal basket 与 order intents 已落盘
- 日期字段一致
- 报告和 manifest 已落盘
- 人能看懂计划含义、评分依据和下一步动作

## 6. 常见故障

### 数据 stale

表现：`last_qlib_date < expected_latest_date`

处理：转 `docs/ops/DATA_PIPELINE_SOP.md`，禁止硬跑盘前。

### 模型不存在

表现：manifest 无法解析或 model path 不存在

处理：切到 `docs/ops/MODEL_OPS_SOP.md`，恢复生产模型或回滚。

### 计划为空

表现：real / shadow 某一侧无交易

处理：检查账户状态、`min_trade`、昨日回填是否完成。若为空属正常，也必须在报告中明确原因。

### 输出仍落到 legacy 根目录

表现：出现 `data/plan_*.csv`、`data/order_intents_*.json`、`data/signal_basket_*.csv`

处理：检查是否误传了 legacy `--output_dir`，或是否绕过了 `run_daily_trading.py` 主入口。

## 7. 人工接管

当盘前流程失败时：

- 人工先确认是否允许继续交易日运营
- 若数据未 ready，直接阻断，不做猜测推荐
- 若仅路径或账户状态问题，可修复后重跑
- 接管后必须保留本次使用的命令、目录和报告路径
- 若临时改了 `--output_dir` / `--report_dir`，必须把实际路径写进交接说明
