# PRE_OPEN SOP

## 1. 目标

盘前阶段必须回答三件事：

- 数据是否健康，日期语义是否明确。
- 模型是否给出了可解释的分数与排序。
- 各账户是否拿到了可执行的计划与 order intents。

## 2. 前置检查

继续盘前前，至少确认：

- 请求日与执行日正确。
- `data/raw/`、`data/qlib_bin/` 已更新到请求口径。
- `data/meta/real_account.db` 可读。
- 缺口、空值、字段缺失没有触发 blocker。

## 3. 执行命令

```bash
python3 scripts/run_daily_trading.py --date 2026-04-02
```

如需显式指定执行日：

```bash
python3 scripts/run_daily_trading.py --date 2026-04-02 --execution_date 2026-04-03
```

## 4. 当前应产出的目录与文件

默认输出到 `daily/{execution_date}/pre_open/`。

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

## 5. 最低验收口径

盘前结果不能只有股票代码，至少应包含：

- `signal_basket` 中的 `score` 或 `score_rank`
- `weight`
- `plan` 中的 `amount`
- `price` 或其他参考价格字段
- `order_intents` 中的执行顺序与现金依赖信息

## 6. 阻断条件

出现以下任一情况，盘前应直接阻断：

- 数据日期不清或数据滞后。
- 缺失率异常，核心字段不可用。
- 模型路径缺失或模型不可加载。
- 计划文件缺少 `amount`、`price`、`weight`、`score` 等关键字段。
- 账户状态缺失，无法生成可执行计划。

## 7. 快速排查

- 数据问题：先看 `docs/ops/DATA_PIPELINE_SOP.md`
- 计划问题：查 `daily/{execution_date}/pre_open/plans/`
- 信号问题：查 `daily/{execution_date}/pre_open/signals/` 与 `daily/{execution_date}/pre_open/reports/`
- 账户问题：查 `data/meta/real_account.db`
