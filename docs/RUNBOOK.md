# RUNBOOK

本文档描述 Qsys 的**日常运营流程、周级投研流程、候选晋级流程与异常处理流程**。
目标不是“列命令”，而是定义一套可稳定执行、可审计、可回滚的操作规程。

---

## 1. 操作原则

默认遵守：

- 先确认数据 ready，再训练、回测、出计划
- 生产与研究分离，不拿研究产物直接上线
- 每次运行必须能回答：
  - 做了什么
  - 用了什么数据
  - 用了哪个模型
  - 得到了什么结果
  - 下一步是什么
- 新功能优先收束到现有入口脚本，不轻易新增脚本
- 脚本变更必须同步补文档与测试

---

## 2. 环境准备

```bash
pip install -r requirements.txt
export PYTHONPATH=$PYTHONPATH:$(pwd)
cp config/settings.example.yaml config/settings.yaml
```

最小配置校验：

```bash
python - <<'PY'
from qsys.config.manager import cfg
print('data_root=', cfg.data_root)
print('qlib_bin=', cfg.get_path('qlib_bin'))
PY
```

---

## 3. Daily Ops（周一到周五）

### 3.1 盘前 checklist

目标：为下一个交易日生成可执行计划。

#### Step A. 数据 readiness 检查

必须确认：
- raw 已更新到上一交易日
- qlib 已对齐到上一交易日
- universe/instrument 可用
- 关键字段缺失不异常

典型入口：

```bash
python scripts/run_update.py --universe csi300 --start 20230101
python scripts/create_instrument_csi300.py
python scripts/run_daily_trading.py --date 2026-03-20 --require_update_success
```

成功标准：
- `raw_latest == last_qlib_date`
- `aligned == True`
- requested date 有 feature rows

失败处理：
- 优先修数据，不跳过数据问题直接出计划
- `--skip_update` 只用于 debug / 回放 / 已确认数据完整的补跑，不应作为长期盘前默认
- 生产盘前建议加 `--require_update_success`，显式刷新失败或 raw/qlib 仍未对齐时直接阻断

#### Step B. 生产模型检查

必须确认：
- production model 存在
- model metadata 完整
- 模型未过期，或已按周级流程完成重训

当前入口：
- `qsys.live.scheduler.ModelScheduler`

目标状态：
- production model 应由 manifest 明确指定，而不是只靠“最新目录”

#### Step C. 生成次日计划

当前主入口：

```bash
python scripts/run_daily_trading.py --date 2026-03-20 --execution_date 2026-03-23
```

小账户生产路径（20k，top_k=5，min_trade=1000，运营产物不写入 `SysQ/data/**`）建议直接用：

```bash
/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python scripts/run_daily_trading.py \
  --date 2026-03-23 \
  --require_update_success \
  --db_path /home/liuming/.openclaw/workspace/positions/qsys_real_account.db \
  --output_dir /home/liuming/.openclaw/workspace/orders \
  --report_dir /home/liuming/.openclaw/workspace/daily
```

说明：
- `--date` 传未来交易日时，会自动回退上一交易日作为 `signal_date`
- 以上命令默认已使用 `top_k=5`、`min_trade=1000`、`real_cash=20000`
- 如需显式覆盖，也可追加 `--top_k 5 --min_trade 1000 --real_cash 20000`

当前语义：
- `signal_date`：可用市场数据日期，用于生成目标组合
- `execution_date`：计划执行日期
- `plan_*.csv` 本质是 **target portfolio delta**，不是“保证能全部成交的委托回报”
- `price` / `price_basis_*` 表示生成该计划时使用的信号日价格基准（默认 `close@signal_date`），不是盘中实时价，也不是已成交价格
- 对 A 股默认基线执行语义：
  - 先卖旧持仓，优先集合竞价 / 开盘阶段处理
  - 待现金回流后再买入目标新增仓位
  - 接受滑点、未成交、部分成交，因此真实结果要以盘后回填为准
  - 当日新买入仓位遵守 T+1，需到下一交易日才可卖出

成功标准：
- 生成 `plan_*.csv`
- 生成 `real_sync_template_*.csv`
- 计划包含：
  - symbol
  - side
  - amount
  - price
  - weight
  - score / score_rank

#### Step D. 人工复核

盘前人工复核至少看：
- 当日 top picks 是否异常集中
- 是否有大量不可交易标的
- 小账户是否因为最小成交额约束导致 plan 为空或失真
- real / shadow 是否严重偏离
- `plan_*.csv` 中 sell 是否基本覆盖旧仓退出、buy 是否明显依赖卖出回款
- 若连续 shadow 多日运行，检查前一日盘后回填是否已完成，否则第二天 real plan continuity 会失真

---

### 3.2 盘后 checklist

目标：回写真实状态并完成 real vs shadow 对账。

主入口：

```bash
python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/real_sync_2026-03-20.csv
```

若运营写边界不允许落到 `SysQ/data/**`，则改用：

```bash
/home/liuming/.openclaw/workspace/.mamba/envs/dl/bin/python scripts/run_post_close.py \
  --date 2026-03-23 \
  --real_sync /home/liuming/.openclaw/workspace/orders/real_sync_2026-03-23.csv \
  --db_path /home/liuming/.openclaw/workspace/positions/qsys_real_account.db \
  --plan_dir /home/liuming/.openclaw/workspace/orders \
  --output_dir /home/liuming/.openclaw/workspace/runs/reconciliation \
  --report_dir /home/liuming/.openclaw/workspace/daily
```

盘后必须完成：
- 回填真实持仓 / 现金 / 总资产
- 若有成交，补成交信息
- 完成 reconciliation
- 检查 real vs shadow 偏差

当前最小必填列：
- `symbol`
- `amount`
- `price`
- `cost_basis`
- `cash`
- `total_assets`

建议补充列：
- `side`
- `filled_amount`
- `filled_price`
- `fee`
- `tax`
- `total_cost`
- `order_id`

成功标准：
- 真实账户状态已落盘
- 对账结果可追踪
- 下一交易日可继续接续运行

### 3.3 连续数日 shadow trial 的最小纪律

每天只抓三件事：
- 盘前：显式刷新数据并通过健康检查，再出 next-day plan
- 盘中/执行后：按 `plan_*.csv` 的 sell -> buy 基线语义记录真实执行，不把 target portfolio 误当成已成交结果
- 盘后：完成真实 CSV 回填与 reconciliation，确保第二天 real/shadow continuity 依赖的是最新状态

建议连续观察：
- 数据更新是否稳定对齐到上一交易日
- shadow 与 real 的现金/持仓偏差是否日积月累
- 小账户在 `top_k=5`、`min_trade=1000` 下是否长期出现“有目标但买不进去/卖不干净”
- T+1 与部分成交是否导致第二天计划出现连续残留仓位

---

## 4. Weekly Model Ops（周级模型运营）

目标：维持模型新鲜度，同时避免把研究噪音直接带进生产。

### 周级流程建议

1. 确认数据已更新到最近交易日
2. 运行周级重训
3. 运行固定口径回测
4. 与当前 baseline / production 比较
5. 判断是否进入 candidate
6. 通过后再人工批准进 production

当前主入口：

```bash
python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --feature_set extended
python scripts/run_strict_eval.py --test_start 2025-01-01 --test_end 2026-03-20 --top_k 5
```

### 周级评估口径（当前共识）

默认：
- train 窗口足够长
- 避免 train/test overlap
- 主看 `2025 -> 最近`
- 辅看 `2026 YTD`
- 回测默认 `top_k=5`

评估必须至少包含：
- 总收益
- 年化收益
- Sharpe
- 最大回撤
- 交易次数 / 换手 / 费用
- baseline 对比

---

## 5. Research Runbook（不固定投研流程）

目标：研究新因子、新模型、新策略，但不污染生产。

### 5.1 立项规则

每个研究任务至少回答：
- 研究什么
- 相对 baseline 的假设是什么
- 用什么评估口径证明有效
- 如果有效，准备如何晋级

### 5.2 当前研究优先级共识

- 不做 feature selection
- 优先引入一组保守的资金流 / 基本面 / 估值因子
- 季度字段谨慎对待，不默认并主线
- 评估优先看严格 out-of-sample，而不是训练内漂亮指标

### 5.3 研究产物要求

至少包括：
- feature set 定义
- train window
- test window
- backtest 参数
- 指标表
- 是否值得晋级的结论

### 5.4 晋级条件（从 research 到 candidate）

至少满足：
- 比 baseline 有明确增量
- 不是靠 train/test 泄漏
- 风险指标不恶化太多
- 可复现
- 数据质量没有明显作弊/未来函数风险

---

## 6. Promotion Runbook（candidate -> production）

当前这部分仍偏人工，但流程应先明确：

1. 研究结果整理为 candidate 报告
2. 与当前 production 对比
3. 明确替换理由与风险
4. 保留回滚目标
5. 人工批准
6. 切换 production model / manifest

当前 repo 中这块尚未完全产品化，是近期优先任务之一。

---

## 7. 标准运行报告（所有重要流程都应产出）

后续统一要求以下字段：

- task_name
- run_time
- input_params
- data_status
- universe
- model_version
- feature_set
- metrics
- decision
- blocker
- next_action

适用流程：
- 数据更新
- 训练
- 回测
- 严格评估
- 每日计划
- 盘后对账

---

## 8. 常用命令

### 数据

```bash
python scripts/run_update.py --universe csi300 --start 20230101
python scripts/create_instrument_csi300.py
python scripts/update_data_all.py
```

### 训练与评估

```bash
python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --feature_set alpha158
python scripts/run_train.py --model qlib_lgbm --start 2020-01-01 --end 2026-03-20 --feature_set extended
python scripts/run_backtest.py --model_path data/models/qlib_lgbm_extended --start 2025-01-01 --end 2026-03-20 --top_k 5
python scripts/run_strict_eval.py --test_start 2025-01-01 --test_end 2026-03-20 --top_k 5
```

### 日常运营

```bash
python scripts/run_daily_trading.py --date 2026-03-20 --execution_date 2026-03-23
python scripts/run_post_close.py --date 2026-03-20 --real_sync broker/real_sync_2026-03-20.csv
```

### 测试

```bash
python -m unittest discover tests
python -m compileall qsys scripts tests
```

---

## 9. 常见故障与处理

### 9.1 数据未对齐

表现：
- `raw_latest != last_qlib_date`
- requested_date 无数据

处理：
- 先修 raw -> qlib 闭环
- 再重跑训练 / plan
- 不允许带着错数据继续

### 9.2 模型元信息不完整

表现：
- scheduler 读不出 model age
- retrain check 失效

处理：
- 检查 `meta.yaml` 与 `training_summary.csv`
- 补齐产物元信息

### 9.3 plan 为空

表现：
- No trades planned

可能原因：
- 小账户受最小交易额约束
- 候选标的不满足交易条件
- 账户状态不正确

处理：
- 先看账户资产规模与 `min_trade`
- 再看标的是否可交易
- 再看 real / shadow 状态是否失真

### 9.4 回测看起来很好，但不可信

优先检查：
- 是否 train/test overlap
- 回测窗口是否太短
- 是否只看训练内指标
- 是否存在未来函数 / 数据泄漏

---

## 10. 当前待补强处

这份 runbook 落地后，下一阶段重点是：

- production manifest / candidate promotion 正式化
- 统一 evaluator / run report
- 盘前与盘后 checklist 产品化
- 脚本收束与 legacy 入口淘汰

Runbook 负责回答：
- 每天怎么跑
- 每周怎么评估和替换模型
- 研究结果怎么进入生产
- 出问题先查哪
