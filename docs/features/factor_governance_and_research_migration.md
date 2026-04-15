# FEATURE: factor_governance_and_research_migration

## Goal

- 把 Qsys 的 feature / factor 研究体系，从“字段 + subset + 脚本驱动”推进到“因子对象 + 变体对象 + bundle 对象 + experiment 对象驱动”。
- 在不打断现有训练、回测、strict eval 主链路的前提下，建立一套长期可维护、可追溯、可比较的研究治理骨架。
- 让后续新增因子、比较 raw/norm 变体、维护主线 bundle、解释回测结论，都有稳定工程落点，而不是继续散落在 `FeatureLibrary`、脚本参数和聊天说明里。

## Why Now

当前 Qsys 已经具备：

- raw -> adapter -> derived feature 的构造能力
- subset / feature_set 驱动训练的最小闭环
- signal eval / backtest / strict eval 的研究雏形
- 一批主线因子与变体讨论（如 `173`、`254`、`254 norm`）

但系统还停留在“能跑研究”而不是“能治理研究资产”的状态，主要问题是：

- `FeatureLibrary` 职责过重，同时承担 feature set 管理、变体展开、研究辅助等职责。
- subset 更像训练配置，不是真正的研究对象。
- raw / norm / absnorm 等 variant 没有完整 lineage，证据链不稳。
- experiment 结果难以直接回答：到底是哪个 factor variant、哪个 bundle、哪个 strategy 假设带来的差异。
- 文档 `docs/features/research_framework_v1.md` 已经把 signal / strategy 拆开，但还没把 factor / variant / bundle / experiment 这几层补齐。

这已经不是局部 feature 文档问题，而是研究系统的长期治理问题，应作为一个大需求推进。

## Scope

### In Scope

本需求覆盖以下内容：

- 明确定义 Qsys 研究对象分层：`raw / factor definition / factor variant / factor bundle / experiment run / decision`
- 设计 manifest/schema 级别的接口草案
- 给出现有代码结构到目标结构的迁移关系
- 规定训练层、signal evaluation 层、portfolio backtest 层、report 层的对接方式
- 定义分阶段迁移顺序
- 定义需要补充的测试契约
- 标注当前文档体系中与新设计冲突或不足的部分

### Out of Scope

本次文档需求明确不做：

- 不直接改写 `qsys/feature/groups/*.py` 中已有特征逻辑
- 不重写 `qsys/data/adapter.py`
- 不重写 `qsys/backtest.py` 或 `qsys/evaluation/*`
- 不直接替换现有 `run_train.py`、`run_backtest.py`、`run_strict_eval.py`
- 不引入复杂执行模拟器或新 broker 抽象
- 不在本阶段解决所有标签体系、所有 universe、所有序列模型问题

## Current Diagnosis

### 1. 现在真正稳定的一等公民还不是因子对象

当前系统里最稳定的对象更像是：

- raw 字段
- feature builder
- feature_set / subset
- train script 参数
- backtest script 参数

这意味着系统更偏“字段驱动研究”，而不是“因子对象驱动研究”。

### 2. `FeatureLibrary` 职责过重

当前 `qsys/feature/library.py` 实际承担了多种职责：

- 命名 feature set 的维护
- absnorm / semantic variant 的展开
- 部分研究辅助函数
- 作为训练入口的隐式配置源

这会带来两个工程问题：

- 配置对象、研究对象、验证对象边界混杂
- 难以回答一个结论到底挂在 factor、variant、bundle 还是脚本参数上

### 3. subset 还是配置，不是研究资产

当前 subset / feature_set 能帮助训练和 ablation，但尚未稳定表达：

- 它为什么存在
- 它与上一版相比差了什么
- 它的用途是 sandbox、research 还是 core
- 它当前证据是否足够主线化

### 4. variant 治理不完整

像 `254`、`254 norm` 这种讨论已经说明：

- base factor 和 transform variant 需要分层管理
- 结论应挂在 variant 上，而不是含糊挂在 feature id 上

否则时间一长就会出现：

- 代码里有 variant
- 脚本里比过 variant
- 聊天里讨论过 variant
- 但系统没有稳定记录谁已经通过验证、谁仍在测试

### 5. experiment 还不是完全标准对象

目前的训练、signal eval、backtest 已有雏形，但一次 run 还不够像一个稳定对象。系统还不够容易直接回答：

- 这次 run 用了哪个 bundle
- bundle 中每个 factor 对应哪个 variant
- signal quality 和 portfolio result 分别如何
- 本次结论是 accept、reject、park 还是 promote

## Target State

Qsys 的理想态不是大厂式超重平台，而是一个人/小团队也能长期维护的 factor research operating system。最小可接受目标是：

- 新因子进入系统时，不需要反复改核心代码
- 同一因子的 raw / norm / neutralized 变体可以稳定比较
- 主线 bundle 有版本、有目的、有变更记录
- 任意回测结果都可追溯到 factor variants、bundle、strategy spec 和 cost spec
- 研究结论可以沉淀为可复核资产，而不是一次性聊天结论

## Proposed Object Model

### Layer 1: Raw Layer

职责：

- 只负责原始或接近原始的数据获取、存储、校验和增量更新

要求：

- 不混研究性派生逻辑
- 时点语义明确
- 支持增量更新
- 来源可追溯

这一层当前基本保留现状，不作为本需求优先重构对象。

### Layer 2: Factor Definition Layer

职责：

- 把“一个因子是什么”定义成稳定对象

建议字段：

- `factor_id`
- `name`
- `family`
- `kind`
- `dependencies`
- `builder`
- `default_lookback`
- `timing_semantics`
- `default_preprocess`
- `description`
- `caveats`

说明：

- `kind` 需区分：`pricing_factor`、`anomaly_factor`、`predictive_feature`、`context_feature`、`execution_filter`
- 后续研究时，不应再只围绕“字段名”讨论，而应围绕 `FactorDefinition` 讨论

### Layer 3: Factor Variant Layer

职责：

- 管理 base factor 的变体谱系

建议字段：

- `variant_id`
- `base_factor_id`
- `transform_chain`
- `status`
- `evidence_refs`
- `notes`

说明：

- `f254@raw`、`f254@absnorm`、`f254@industry_neutralized` 应是不同对象
- 研究结论优先挂在 variant，而不是模糊挂在 base factor 上

### Layer 4: Factor Bundle Layer

职责：

- 把当前 subset 升级为真正的研究对象

建议字段：

- `bundle_id`
- `purpose`
- `factor_variants`
- `universe_scope`
- `intended_usage`
- `parent_bundle`
- `change_log`

说明：

- bundle 不再只是字段集合
- bundle 必须有存在理由、使用目的和变更记录
- `intended_usage` 建议至少支持：`sandbox`、`research`、`core`

### Layer 5: Experiment Run Layer

职责：

- 把一次研究运行定义成标准对象

建议字段：

- `experiment_id`
- `bundle_id`
- `model_spec`
- `label_spec`
- `split_spec`
- `strategy_spec`
- `cost_spec`
- `result_paths`
- `summary_metrics`
- `decision`

说明：

- 训练、signal eval、portfolio backtest 的结果都应汇总到 `ExperimentRun`
- 以后横向比较应优先比较 experiment objects，而不是人工比目录和命令行参数

### Layer 6: Decision / Promotion Layer

职责：

- 记录研究结论与晋级状态

建议状态：

- `accept_research_only`
- `promote_to_core`
- `reject`
- `park`

说明：

- promotion decision 可以挂在 factor variant、bundle 或 experiment conclusion 上
- 决策必须能追溯证据来源，而不是停留在口头讨论

## API Change

- 是否新增 API：是
- 是否修改现有 API：是，但应分阶段推进

### 目标输入契约

第一阶段建议引入以下 manifest/schema。

#### `FactorDefinition`

```yaml
factor_id: f254
name: operating_cf_to_profit
family: fundamental_quality
kind: predictive_feature
dependencies:
  - op_cashflow
  - net_income
builder:
  type: formula
  expression: op_cashflow / net_income
default_lookback: 1
timing_semantics:
  visibility: announcement_aligned
  signal_date_rule: T-1_available
default_preprocess:
  - raw
description: cashflow quality proxy
caveats:
  - unstable when net_income near zero
```

#### `FactorVariant`

```yaml
variant_id: f254@absnorm
base_factor_id: f254
transform_chain:
  - signed_log
  - cs_zscore
status: testing
evidence_refs:
  - exp_2026_04_16_001
notes:
  - not yet validated on mainline universe
```

#### `FactorBundle`

```yaml
bundle_id: bundle_mainline_candidate_v3
purpose: current mainline candidate bundle
factor_variants:
  - f173@raw
  - f254@absnorm
  - f301@raw
universe_scope: csi300
intended_usage: research
parent_bundle: bundle_mainline_candidate_v2
change_log:
  - add f254@absnorm
  - remove f212@raw
```

#### `ExperimentRun`

```yaml
experiment_id: exp_2026_04_16_001
bundle_id: bundle_mainline_candidate_v3
model_spec:
  model_type: qlib_lgbm
label_spec:
  label_type: forward_return
  horizon: 5d
split_spec:
  train: 2020-01-01~2024-12-31
  test: 2025-01-01~2026-03-20
strategy_spec:
  strategy_type: rank_topk
  top_k: 5
  weighting: equal_weight
cost_spec:
  fee_rate: 0.0007
  slippage: 0.0005
artifacts:
  signal_metrics: reports/exp_2026_04_16_001/signal_metrics.json
  backtest_report: reports/exp_2026_04_16_001/backtest_summary.json
summary:
  rank_ic: 0.03
  total_return: 0.11
  max_drawdown: -0.08
decision: accept_research_only
```

### 与现有 CLI / runner 的对接建议

第一阶段不要求重写脚本，而是新增解析路径：

- `run_train.py` 支持 `bundle_id`
- `run_backtest.py` 支持读取 `ExperimentRun` 或其 snapshot
- `run_strict_eval.py` 支持直接消费 prediction + experiment snapshot

后续逐步降低这些旧参数的主导地位：

- `feature_set`
- 各类 ad hoc variant flags
- 脚本内部 feature grouping 常量

## Training / Evaluation / Backtest Contract

建议统一研究主链路：

`bundle -> dataset build -> train -> signal eval -> portfolio backtest -> report -> decision`

### 训练层

输入：

- `bundle_id`
- `label_spec`
- `split_spec`
- `model_spec`

输出：

- model artifact
- prediction panel
- config snapshot

要求：

- 输出中必须保留 bundle 与 factor variants 的快照
- 不允许训练结束后只能靠聊天还原输入特征集合

### Signal Evaluation 层

输入：

- prediction panel
- label panel
- universe/group info

输出：

- IC / RankIC
- monotonicity
- grouped returns
- turnover proxy
- sensitivity summary

要求：

- signal evaluation 不依赖 `top_k`
- signal quality 和 portfolio result 必须严格拆开

### Portfolio Backtest 层

输入：

- prediction panel
- `strategy_spec`
- `cost_spec`
- execution assumptions

输出：

- return / drawdown / turnover
- holdings path
- order / trade summary

要求：

- `top_k`、持仓约束、成本假设只在本层生效
- backtest 必须能明确追溯到它消费的是哪一个 experiment / bundle

### Report / Decision 层

输入：

- signal evaluation summary
- portfolio backtest summary
- factor metadata
- bundle metadata

输出：

- experiment report
- decision record
- 可被 wiki 摘要沉淀的结论

## Storage Layout Recommendation

建议保持代码与研究对象分离。

### 代码继续放在 `qsys/`

例如：

- `qsys/data/*`
- `qsys/feature/groups/*`
- `qsys/feature/transforms.py`
- `qsys/backtest.py`
- `qsys/evaluation/*`

### 研究对象建议独立持久化

推荐形态：

```text
research/
  factors/
    definitions/
      f173.yaml
      f254.yaml
    variants/
      f173@raw.yaml
      f254@raw.yaml
      f254@absnorm.yaml
    bundles/
      bundle_mainline_candidate_v1.yaml
      bundle_mainline_candidate_v2.yaml
    experiments/
      exp_2026_04_16_001.yaml
```

说明：

- 若未来更适合放在 `data/research/`，也可以迁过去
- 关键不是路径，而是对象分层与版本治理

## Migration Plan

### Phase 0: 文档冻结当前问题与目标

目标：

- 把本次讨论沉淀为长期需求，作为后续 PR 的统一需求基线

交付：

- 本 feature 文档
- 相关 ADR

### Phase 1: 引入对象层，不打断现有训练主线

动作：

- 新增 `FactorDefinition` manifest
- 新增 `FactorVariant` manifest
- 新增 `FactorBundle` manifest
- 先不强制所有脚本立刻消费这些对象

收益：

- 先把研究对象立起来
- 先结束“只有字段和脚本，没有对象”的状态

### Phase 2: 训练与回测开始消费 bundle / experiment snapshot

动作：

- `run_train.py` 支持 `bundle_id`
- 训练产物落盘 `config_snapshot.json` / `experiment_snapshot.yaml`
- `run_backtest.py` 与 `run_strict_eval.py` 能消费统一快照

收益：

- 让实验成为可追溯对象
- 让结果和输入绑定

### Phase 3: 迁移现有主线 feature_set / variant 体系

优先迁移对象：

- 当前主线 bundle
- `173`
- `254`
- `254 norm`
- 当前 absnorm 比较相关集合

动作：

- 把主线 subset 映射成 bundle manifests
- 把当前重要 variant 映射成 variant manifests
- 对历史 run 至少补核心 lineage

收益：

- 让当前主线结论可追溯
- 为后续研究比较打基础

### Phase 4: 把 decision 挂到 factor variant / bundle / experiment

动作：

- 新增 decision record
- 规定 `accept_research_only / promote_to_core / reject / park`
- 每次重要实验输出明确 decision

收益：

- 研究结果开始变成资产，而不是一次性 run

## Test Plan

### A. Factor Definition / Variant Tests

需要测试：

- definition 的依赖字段是否齐全
- variant transform chain 是否可重复执行
- 相同输入下 variant 结果是否稳定
- timing semantics 是否可合法解析

目标：

- 防止因子定义漂移

### B. Bundle Contract Tests

需要测试：

- bundle 中引用的 factor variants 是否都存在
- bundle 解析后字段集合是否稳定
- bundle 变更是否触发预期差异
- 固定数据窗口下 bundle 构造是否可重现

目标：

- 防止 subset / bundle 漂移

### C. Experiment Contract Tests

需要测试：

- 训练输出是否带完整 snapshot
- signal eval 是否可独立于 backtest 运行
- backtest 是否可消费统一 prediction / experiment snapshot
- report 是否能反向追溯到 bundle 与 factor variants

目标：

- 防止研究 run 断链

### D. 回归验证建议

最低回归建议：

- 用当前主线 universe 跑一组固定 bundle
- 对 `173`、`254`、`254 norm` 形成最小对照
- 验证 signal eval 与 backtest 的输入快照一致
- 验证报告可明确显示：bundle、variant、top_k、cost spec

## Documentation Impact

### 与当前文档一致的部分

以下方向与现有文档一致，应保留：

- `docs/features/feature_system.md` 已强调 raw 与 feature engineering 分离、研究与生产分离、feature list 权威表
- `docs/features/research_framework_v1.md` 已强调 signal 与 strategy 分离、显式 spec、统一 artifact contract

### 当前不足或冲突的部分

#### 1. `docs/features/research_framework_v1.md`

当前不足：

- `feature_set` 仍然是过于粗糙的研究入口
- 文档重心在 `ExperimentSpec / SignalEngine / StrategyEngine`，但缺少 `FactorDefinition / Variant / Bundle / Decision` 四层
- factor validation 尚未在 bundle/model experiment 之前显式成层

后续建议：

- 在 V2 中把 `feature_set` 升级为 `bundle_id`
- 增加 factor / variant / bundle / decision 四类对象定义

#### 2. `docs/features/feature_system.md`

当前不足：

- 已提出“feature list 必须有唯一权威表”，但尚未细化 base factor 与 variant 的分层
- 已强调 feature list、normalization rule、neutralization rule，但还没把它们建模为稳定 manifest objects

后续建议：

- 在本文件中补充 factor definition 与 variant lineage 的长期规则
- 避免继续把所有治理压力压在单一 feature list 表上

#### 3. `docs/research/feature_backtest_report.md` 及同类实验文档

当前不足：

- 更偏实验结果记录，缺乏标准 experiment object 视角

后续建议：

- 逐步转为引用 experiment snapshots 与 bundle ids

## UI

本需求当前不直接要求新增 UI，但要求未来 UI 具备以下展示能力：

- 展示 experiment 的 `bundle_id`
- 展示 bundle 内包含哪些 factor variants
- 展示 signal quality 与 portfolio metrics 的分层结果
- 展示 promotion / decision 状态

这意味着后续 UI 不应继续只展示 `feature_set + PnL`。

## Constraints

### 技术约束

- 迁移过程中必须保持现有训练 / 回测主链路可用
- 不能一次性重写 adapter、builder、backtest 核心逻辑
- manifest / snapshot 必须能稳定序列化，便于版本控制与产物追溯

### 业务约束

- A 股研究与生产仍需遵守现有 `signal_date / execution_date` 与 T+1 约束
- `top_k` 继续视为 portfolio/backtest robustness 参数，不属于训练层语义
- 当前主线讨论必须优先围绕真实在用因子与变体，不做脱离主线的 toy workflow 演示

### 禁止改动

本需求第一阶段不应：

- 借机大改交易执行架构
- 借机重写所有 feature groups
- 借机引入过重的中台式配置系统

## Done Criteria

- 文档层明确了目标对象模型、迁移顺序、测试要求和接口草案
- 相关 ADR 已建立长期决策基线
- 后续代码开发可以按本文件拆分 PR，而无需重新争论研究对象分层
- 已明确当前文档体系中哪些部分保留、哪些部分需要升级

## Rollback Plan

- 本次为文档型需求，无运行时回滚动作
- 若后续实现中发现对象层设计过重，可回退到“bundle + experiment snapshot 优先、definition/variant 后补”的轻量落地路径

## Notes

- 本需求本质上不是“再加几组特征”，而是把 Qsys 从字段驱动研究系统推进到因子对象驱动研究系统。
- 对个人量化/小团队，重点不是追求最完美平台，而是：新因子易定义、变体易比较、bundle 易治理、实验易追溯、结论易沉淀。
- 现阶段最重要的不是重写核心计算代码，而是先把研究对象和接口契约立起来，再逐步让训练/回测成为这些对象的消费者。
