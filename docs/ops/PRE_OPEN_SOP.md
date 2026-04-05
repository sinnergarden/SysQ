# PRE OPEN SOP

## 1. 目标

盘前 SOP 的目标不是“生成几个文件”，而是确认今天可以运营：

- 数据健康通过。
- 模型输出可解释的分数与排序。
- 每个账户都有明确的计划、权重、计划股数与参考价格。
- 所有证据都落进 `daily/{execution_date}/pre_open/`。

## 2. 前置检查

开始前先确认：

- canonical data 已更新到正确日期：`data/raw/` 与 `data/qlib_bin/`
- 生产模型可解析：`data/models/production_manifest.yaml`
- 长期账户账本存在于 `data/meta/real_account.db`
- 日期语义明确：`signal_date` 是算信号用的数据日期，`execution_date` 是计划执行日期

若日期不清、数据滞后、账户状态缺失，直接视为 blocker。

## 3. 执行命令

标准命令：

```bash
python scripts/run_daily_trading.py --date 2026-04-02
```

如果需要显式指定执行日：

```bash
python scripts/run_daily_trading.py --date 2026-04-02 --execution_date 2026-04-03
```

## 4. 默认输出位置

盘前默认输出目录：`daily/{execution_date}/pre_open/`

固定子目录：

- `plans/`
- `order_intents/`
- `signals/`
- `reports/`
- `manifests/`

关键文件模式：

- `plans/plan_{signal_date}_{account}.csv`
- `plans/real_sync_template_{signal_date}_{account}.csv`
- `order_intents/order_intents_{execution_date}_{account}.json`
- `signals/signal_basket_{signal_date}.csv`
- `reports/daily_ops_pre_open_*.json`
- `reports/signal_quality_summary_{signal_date}.json`
- `manifests/daily_ops_manifest_{execution_date}.json`

说明：`real_sync_template` 归入 `plans/`，因为它本质上是盘前计划的执行回写模板，不再单独开 `templates/` 目录。

## 5. 盘前验收清单

### 5.1 数据健康

至少检查：

- 日期是否正确
- 是否存在缺口
- 关键字段是否齐全
- 空值是否异常偏多

如果脚本输出了 data readiness blocker，禁止继续把结果当作正式计划。

### 5.2 Signal Basket

至少检查这些字段：

- `symbol`
- `score`
- `score_rank`
- `weight`
- `price`
- `signal_date`
- `execution_date`

如果只有代码没有排序依据，不满足运营要求。

### 5.3 Plan

至少检查这些字段：

- `symbol`
- `side`
- `amount`
- `price`
- `est_value`
- `weight` 或 `target_value`
- `signal_date`
- `execution_date`

### 5.4 Order Intents

至少检查这些字段：

- `account_name`
- `symbol`
- `side`
- `amount`
- `price`
- `execution_bucket`
- `cash_dependency`
- `t1_rule`

## 6. 结果解释

盘前 evidence 包回答的问题应该是：

- 用了哪一天的数据？
- 模型为什么选了这些票？
- 每个账户打算怎么执行？
- 有没有 blocker 阻止今日执行？

## 7. 失败时怎么处理

- 数据健康失败：先修数据，不继续下游。
- 模型路径缺失：先修 `data/models/production_manifest.yaml` 或重新训练。
- 账户主库问题：检查 `data/meta/real_account.db`，不要去 `daily/` 找主库副本。
- 找不到计划：只检查 `daily/{execution_date}/pre_open/`，不再回退到 `data/` 根目录旧文件。

## 8. 盘前完成后的建议动作

```bash
python scripts/run_signal_quality.py --date 2026-04-03
```

如果当天完成交易并在收盘后拿到 broker 回写，再执行盘后 SOP。
