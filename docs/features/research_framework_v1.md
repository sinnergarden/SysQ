# FEATURE: research_framework_v1

## Goal

- 把 Qsys 从“单次训练 / 单次回测 / 单次修 bug”的系统，推进成一个可自由研究、可公平比较、可直接对接 UI 的研究框架。
- 让研究维度通过显式配置组合，而不是每次改脚本和代码。
- 明确拆开“模型输出”和“组合构造”，为后续研究和 UI 对接提供稳定骨架。

## Why Now

当前问题不是某一个策略临时跑得好不好，而是研究骨架还不够稳定：

- 很多研究仍偏 `单脚本 + 单策略 + 单次 debug`，feature / model / label / strategy / 调仓频率 / 成本假设经常固化在具体脚本里。
- 研究维度缺少统一 spec，导致后续很难公平比较两次 run 的差异到底来自 signal、strategy、还是 execution 假设。
- 研究结果解释仍偏一次性，很多时候只能看 PnL，却看不清 signal quality、分组收益、long-short spread、turnover 和执行损耗。
- 产物还不够统一，UI 能展示的内容依然依赖人工解释，难以把“一次 run”变成可重复分析对象。

因此第一版研究框架的目标不是再写一个新策略，而是先把：

- 研究配置
- 核心抽象
- 评价口径
- 产物契约
- UI 最小对接面

这些边界写清楚，让后续实现可直接照文档推进。

## Scope

### In Scope

第一版只做以下事项：

- 定义 `ExperimentSpec / ResearchSpec`
- 定义 `SignalEngine / StrategyEngine / Evaluator / Artifact Contract`
- 定义第一版支持的研究配置字段、默认值和枚举值
- 定义 signal 层与 strategy 层的输入输出边界
- 定义第一版策略类型
- 定义第一版 evaluator 指标集合
- 定义第一版统一产物 contract
- 定义 UI 第一版最小展示要求
- 定义它与现有 `run_train / run_backtest / run_strict_eval / daily ops / unified schema` 的衔接方式

### Out of Scope

第一版明确不做：

- 不继续围绕单个策略做深度 debug
- 不新增一堆临时 audit script
- 不做大规模架构重写
- 不先碰复杂交易所微观规则
- 不打乱 daily ops 主链路
- 不在本阶段强做 partial turnover 等尚未准备好的策略细节
- 不在本阶段引入新的复杂 execution simulator

## Core Abstractions

### ExperimentSpec / ResearchSpec

作用：
- 描述一次研究 run 的显式配置
- 作为 train / backtest / strict_eval / UI 的共同输入摘要
- 避免研究维度继续散落在 CLI 默认值和脚本常量里

第一版要求：
- 可以稳定序列化到 `config_snapshot.json`
- 可以稳定映射到 UI 的“实验配置”区
- 可以作为后续 runner / workflow 的统一输入层

### SignalEngine

职责：
- 只负责从特征和模型中产出 signal
- 不负责组合构造和换仓逻辑

第一版输出形态：
- `score`
- `prob`
- `binary`
- `expected_return`

说明：
- 第一版不要求所有模型同时支持全部输出形态
- 但每次 run 必须明确自己的 signal 类型和字段含义

### StrategyEngine

职责：
- 把 signal 变成 target weights / orders
- 显式表达选股、过滤、持仓、空仓、换仓和调仓频率
- 明确“空仓 / 部分空仓是合法结果，不是异常”

第一版输入：
- signal output
- strategy config
- current holdings（若有）
- rebalance config
- transaction cost assumptions

第一版输出：
- `target_weights`
- `orders`（若该 flow 需要）
- strategy-level audit / reject / cash gate 信息

### Evaluator

职责：
- 分开评价 signal quality 和 portfolio result
- 产出可横向比较的统一指标

第一版要求：
- signal quality 和 portfolio result 必须分开展示
- 不允许只看 PnL 就结束一轮研究解释

### Artifact Contract

职责：
- 让每一轮研究产物都能被统一消费
- 直接服务于 UI，而不是继续依赖人工解释一次 run

第一版要求：
- 至少覆盖配置、训练摘要、signal 指标、分组收益、执行审计和汇总指标
- 缺失文件必须显式说明 `not_available`，不能无声缺失

## ExperimentSpec V1

### Required Fields

| Field | Required | Default | V1 Supported Values | Notes |
|---|---|---:|---|---|
| `run_name` | Yes | None | free text | 研究 run 的稳定名字 |
| `feature_set` | Yes | None | `baseline`, `extended`, `phase123`, future named sets | 显式指向特征集合 |
| `model_type` | Yes | None | `qlib_lgbm`, `qlib_xgb`, `qlib_tabular_nn` | 第一版先列出可扩展枚举，不要求都立即实现 |
| `label_type` | Yes | None | `forward_return`, `relative_return`, `binary_event` | 标签语义类型，不隐含 horizon / benchmark / threshold |
| `strategy_type` | Yes | None | `rank_topk`, `rank_topk_with_cash_gate`, `rank_plus_binary_gate` | signal 和 strategy 显式拆层 |
| `universe` | Yes | None | `csi300`, `all_a`, future named universes | 第一版优先支持 `csi300` |
| `output_dir` | Yes | None | path | 研究产物输出目录 |

### Fields With Defaults

| Field | Required | Default | V1 Supported Values | Notes |
|---|---|---:|---|---|
| `top_k` | No | `5` | positive int | 仅对需要 top-k 的 strategy 生效 |
| `label_horizon` | No | `5d` | `1d`, `5d`, future horizons | 显式定义标签预测窗口，不再隐藏在 `label_type` 里 |
| `label_benchmark` | No | `none` | `none`, `csi300`, future named benchmarks | 仅对相对收益类标签生效 |
| `label_threshold` | No | `not_applicable` | float or `not_applicable` | 仅对二分类/事件类标签生效 |
| `rebalance_mode` | No | `full_rebalance` | `full_rebalance`, `hold_if_no_trigger` | 表示调仓语义 |
| `rebalance_freq` | No | `weekly` | `daily`, `weekly` | 表示组合调仓频率 |
| `retrain_freq` | No | `weekly` | `daily`, `weekly` | 表示模型重训频率 |
| `inference_freq` | No | `daily` | `daily`, `weekly` | 表示信号推理频率 |
| `transaction_cost_assumptions.fee_rate` | No | strategy default | float | 显式写入 config snapshot |
| `transaction_cost_assumptions.slippage` | No | strategy default | float | 显式写入 config snapshot |
| `transaction_cost_assumptions.tax_rate` | No | strategy default | float | 显式写入 config snapshot |
| `transaction_cost_assumptions.volume_participation_cap` | No | `not_available` | float or `not_available` | 第一版允许部分 flow 不支持 |

### Notes

- `ExperimentSpec` 第一版只要求把字段写清楚，不要求一次支持所有组合。
- `rebalance_mode` 与 `rebalance_freq` 语义必须分开：前者表示“怎么调仓”，后者表示“多久调一次仓”；实现时不要混用成一个字段。
- `label_type` 只表达标签语义类型，不再隐含 horizon / benchmark / threshold；这些条件应通过显式字段补足。
- 不支持的字段值应在实现中显式报 `not_supported_in_v1`，不要偷偷 fallback。
- `transaction_cost assumptions` 必须进入 `config_snapshot.json`，后续比较 run 时不能靠聊天还原。

## Signal / Strategy Split

### SignalEngine Output Contract

Signal 层至少输出：

| Field | Meaning |
|---|---|
| `instrument_id` | 标的 |
| `trade_date` | signal 日期 |
| `signal_type` | `score / prob / binary / expected_return` |
| `signal_value` | 核心 signal 值 |
| `aux_fields` | 可选辅助字段，如 rank、threshold、probability |

Signal 层不负责：
- top-k 选股
- 过滤后持仓数
- 现金是否留存
- 是否生成订单

### StrategyEngine Input/Output Contract

Strategy 层输入：
- signal table
- strategy config
- optional holdings snapshot
- rebalance config
- transaction cost assumptions

Strategy 层输出：

| Output | Meaning |
|---|---|
| `target_weights` | 目标持仓权重 |
| `orders` | 若该 flow 需要，输出订单层 |
| `strategy_audit` | 过滤、现金门槛、空仓原因、候选集裁剪原因 |

### Why This Split Matters

只有把 signal 和 strategy 拆开，后续才可以自然支持这些研究动作：

- 纯截面排序 top-k
- 先 binary 过滤，再按 score 排序
- 预测都不够好时允许部分空仓或全空仓
- 全仓换仓 vs 部分换仓
- 日级重训 vs 周级重训
- 日级调仓 vs 周级调仓

## strategy_type V1

### 1. `rank_topk`

定义：
- 按 signal 排序后直接选 top-k
- 默认等权或现有 strategy default weight rule

适用：
- 最简单的纯截面排序研究

### 2. `rank_topk_with_cash_gate`

定义：
- 先按 signal 排序
- 再根据一个显式 cash gate / confidence gate 决定是否允许部分空仓或保留现金

适用：
- 研究“模型整体信号不足时是否应留现金”
- 该策略允许部分空仓或全空仓；空仓在第一版属于合法策略结果，而不是错误态

### 3. `rank_plus_binary_gate`

定义：
- 先用 binary signal 过滤候选池
- 再在通过过滤的样本上按 score 排序并选 top-k

适用：
- 研究“方向判断 + 排序强弱”的组合逻辑

### Deferred

第一版明确延期：
- partial turnover / partial rebalance
- 更复杂的目标持仓优化
- 更复杂的风险预算或行业/风格约束优化

如果后续补 partial turnover，应作为 v1.x 或 v2 的扩展，而不是现在强塞进第一版。

## Evaluator V1 Metrics

第一版至少统一这些指标：

### Signal Quality

| Metric | Meaning |
|---|---|
| `IC` | Pearson IC |
| `RankIC` | Spearman RankIC |
| `ICIR` | IC information ratio |
| `RankICIR` | RankIC information ratio |
| `group_1_5_returns` | 1-5 分组收益 |
| `group_1_5_nav` | 1-5 分组净值 |
| `long_short_spread` | long-short spread |

### Portfolio Result

| Metric | Meaning |
|---|---|
| `turnover` | 换手 |
| `fee` | 手续费 |
| `slippage` | 滑点成本 |
| `net_pnl` | 净收益 |
| `max_drawdown` | 最大回撤 |
| `hit_ratio` | 胜率 / 命中率 |

### Display Rule

必须明确：
- signal quality 和 portfolio result 分开展示
- 不允许只给一个总 PnL 就算完成一轮研究
- group 1-5 和 long-short 是第一版 signal 解释面的硬指标，不是 optional 附件

## Artifact Contract V1

第一版统一产物至少包括：

| File | Producer | Key Fields | May Be Missing In | Missing Rule |
|---|---|---|---|---|
| `config_snapshot.json` | experiment runner / entrypoint | experiment spec、交易成本假设、频率配置 | none | 必须存在 |
| `training_summary.json` | train flow / model artifact / strict eval aggregator | training mode、train end、infer date、label maturity | strict eval may not own training step | 若拿不到真实训练信息，显式写 `not_available` 或 baseline/extended 双摘要 |
| `signal_metrics.json` | evaluator | IC、RankIC、ICIR、RankICIR、group stats、long-short | some daily ops views | 显式写 `not_available_in_flow` |
| `group_returns.csv` | evaluator | group 1-5 returns / nav | some non-group flows | 显式空表头 + metadata 或 `not_available` |
| `execution_audit.csv` | strategy/execution layer | reject、fill assumptions、turnover inputs | pure signal-only flows | 显式空表头或 `not_available_in_flow` |
| `metrics.json` | evaluator / report layer | pnl、drawdown、turnover、fee、slippage、hit ratio | none | 必须存在 |

### Contract Rules

- 文件名固定，便于 UI 和后续脚本稳定消费。
- 没有该类数据时，必须显式 `not_available`，不能靠文件缺失让下游猜。
- `training_summary.json` 不能伪造“好看值”；没有真实字段时应保留 `null` / `not_available`。
- `execution_audit.csv` 不等于复杂交易所微观规则模拟；第一版只要求显式记录当前假设。

## UI Integration V1

第一版 UI 至少要能展示：

### Experiment Config

- `feature_set`
- `model_type`
- `label_type`
- `strategy_type`
- `top_k`
- `retrain_freq`
- `rebalance_mode`
- `rebalance_freq`
- `inference_freq`
- `universe`
- transaction cost assumptions

### Signal Metrics

- `IC`
- `RankIC`
- `ICIR`
- `RankICIR`
- group 1-5 summary
- long-short spread

### Portfolio / Execution

- `pnl`
- `drawdown`
- `turnover`
- `reject count`
- `fee / slippage / net pnl`

### Visuals

第一版至少应支持：
- group 1-5 图
- pnl / drawdown / turnover 摘要区
- reject count 或 execution audit 摘要区

## Relation To Existing System

### `run_train`

第一版关系：
- 保留为训练入口
- 后续应接受或映射 `ExperimentSpec` 中与训练相关的字段
- 负责产出 `training_summary.json`

### `run_backtest`

第一版关系：
- 保留为回测入口
- 后续应逐步接受 signal / strategy / rebalance 相关显式参数
- 负责产出 `signal_metrics.json`、`group_returns.csv`、`metrics.json`

### `run_strict_eval`

第一版关系：
- 保留为比较入口
- 后续应基于统一 contract 比较 baseline / extended 或更多 experiment 组合
- 不应再用模糊占位值替代训练或评估状态

### `daily ops`

第一版关系：
- 不打乱主链路
- daily ops 可继续使用现有 unified schema，但后续可复用 research framework 的 artifact contract 与 signal / strategy 分层
- 日常运营不是这次的主改造对象

### `current unified schema`

第一版关系：
- 不是推翻重来
- 而是在现有 unified schema 基础上，把研究框架所需的 signal metrics / group returns / config fields 收口得更完整、更适合 UI

## Done Criteria

- `ROADMAP.md` 增加 `research framework v1` 小节，说明目标、完成判定和边界
- repo 新增 `docs/features/research_framework_v1.md`
- 文档能直接回答：
  - 第一版为什么做
  - 第一版做什么 / 不做什么
  - 核心抽象是什么
  - `ExperimentSpec` 字段有哪些
  - `SignalEngine` / `StrategyEngine` 如何解耦
  - evaluator 输出哪些指标
  - artifact contract 长什么样
  - UI 至少展示什么
  - 与现有系统怎么衔接
- 文档足够具体，后续实现 PR 不需要再先靠聊天解释核心边界

## Notes

- 这份文档是第一版研究框架的开发规格，不是架构总论。
- 后续如果实现中发现某个字段或枚举值必须调整，应优先更新这份 feature 文档，而不是先把差异埋进脚本默认值。
- 若后续研究框架引入长期稳定的新架构约束，再考虑补 ADR；当前阶段先不改 `ARCHITECTURE.md` 主体。
