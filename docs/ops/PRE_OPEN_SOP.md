# 盘前 SOP

## 1. 目的

在执行日开盘前，基于上一交易日收盘数据生成可执行计划，并给出明确的风险提示与人工复核点。

## 2. 输入

- `signal_date`：上一交易日
- `execution_date`：实际执行日
- 生产模型或指定 `--model_path`
- 账户数据库 `--db_path`
- 盘前产物目录 `--output_dir`
- 报告目录 `--report_dir`

## 3. 输出

- `plan_<signal_date>_shadow.csv`
- `plan_<signal_date>_real.csv`
- `real_sync_template_<signal_date>_shadow.csv`
- `real_sync_template_<signal_date>_real.csv`
- `daily_ops_pre_open_*.json`

## 4. 标准步骤

### Step A：确认日期

规则：

- 若 `--execution_date` 明确传入，则 `--date` 就是 `signal_date`
- 若仅传 `--date` 且该日期晚于今天，则该日期视为 `execution_date`，脚本自动回退到上一交易日作为 `signal_date`

### Step B：执行数据更新与 readiness 检查

建议命令：

```bash
python scripts/run_daily_trading.py \
  --date 2026-03-27 \
  --execution_date 2026-03-30 \
  --require_update_success
```

通过条件：

- 显式刷新未失败
- `health_ok == true`
- `aligned == true`
- `core_daily_status` 不阻断

### Step C：确认模型

通过条件：

- 生产 manifest 能解析，或手工指定 `--model_path`
- 模型可加载
- 特征配置可读取

### Step D：生成 shadow / real 计划

计划字段至少应有：

- `symbol`
- `side`
- `amount`
- `price`
- `weight`
- `score`
- `score_rank`
- `signal_date`
- `execution_date`

### Step E：人工复核

至少检查：

- 是否出现异常集中持仓
- 是否存在大面积空计划
- `sell` 是否先于 `buy` 的现金依赖逻辑
- 小账户在 `min_trade` 约束下是否失真
- 计划路径是否写到运营目录，而不是默认仓库目录

## 5. 成功标准

- real / shadow 计划已生成
- 日期字段一致
- 报告已落盘
- 人能看懂计划含义和下一步动作

## 6. 常见故障

### 数据 stale

表现：`last_qlib_date < expected_latest_date`

处理：转 `DATA_PIPELINE_SOP.md`，禁止硬跑盘前。

### 模型不存在

表现：manifest 无法解析或 model path 不存在

处理：切到 `MODEL_OPS_SOP.md`，恢复生产模型或回滚。

### 计划为空

表现：real / shadow 某一侧无交易

处理：检查账户状态、`min_trade`、昨日回填是否完成。若为空属正常，也必须在报告中讲清原因。

## 7. 人工接管

当盘前流程失败时：

- 人工先确认是否允许继续交易日运营
- 若数据未 ready，直接阻断，不用猜测推荐
- 若仅路径或账户状态问题，可在修复后重跑
- 接管后必须保留本次使用的命令、目录和报告路径
