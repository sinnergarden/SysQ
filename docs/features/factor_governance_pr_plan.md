# FEATURE: factor_governance_pr_plan

## Goal

- 把 `factor_governance_and_research_migration.md` 中的大需求，拆成一组可执行、可评审、可逐步落地的文档与实现 PR。
- 明确每一步到底做什么、不做什么、验收什么，避免后续重新陷入“大方向同意，但工程切分不清”的状态。
- 先把文档层整体收口，再给后续代码实现提供稳定提单边界。

## Why This Doc Exists

`factor_governance_and_research_migration.md` 已定义了目标状态，但还需要一份更偏实施管理的拆解文档，回答这些问题：

- 这个大需求应该分几步推进
- 每一步的产出物是什么
- 每一步优先改哪些文件
- 先做哪些最小契约，后做哪些结构升级
- 哪些阶段只动文档，哪些阶段开始改代码
- 哪些风险需要提前防守

这份文档就是为了把“大需求”转成“工程提单序列”。

## Overall Strategy

总策略是：

- 先立对象和契约，再迁运行入口
- 先补 manifest / snapshot / 文档，再动训练和回测接口
- 先迁主线对象（如 `173`、`254`、`254 norm`），不先追求全量迁移
- 先让新旧路径并行一段时间，再逐步降低 `feature_set` 的主导地位

也就是说，这不是一次重构，而是一条多阶段迁移线。

## Delivery Phases

### Phase A: 文档基线 PR

目标：

- 把问题定义、目标对象模型、迁移边界、长期 ADR、分阶段计划全部写清楚

本阶段交付：

- `docs/features/factor_governance_and_research_migration.md`
- `docs/features/factor_governance_pr_plan.md`
- `docs/adr/004-factor-objects-over-feature-sets.md`
- 相关目录索引更新

本阶段不做：

- 不改代码行为
- 不引入 manifest loader
- 不动训练 / 回测接口

验收标准：

- 后续实现 PR 不需要再重谈目标对象模型
- 团队能清楚知道这个需求分几步做
- 文档中已明确哪些现有文档需升级

### Phase B: Manifest Schema PR

目标：

- 在代码层正式引入研究对象的最小 schema / parser，但不接管训练主流程

建议交付：

- `FactorDefinition` schema
- `FactorVariant` schema
- `FactorBundle` schema
- 基础校验器 / loader
- 1-3 个示例 manifests

建议改动文件：

- 新增 `qsys/research/manifest.py` 或等价模块
- 新增 `qsys/research/schemas.py` 或等价模块
- 新增 `research/factors/...` 或 `data/research/...` 目录
- 新增 schema contract tests

本阶段不做：

- 不替换 `FeatureLibrary`
- 不要求 `run_train.py` 立即消费全部 manifest
- 不追求历史全量因子迁移

验收标准：

- manifest 能被稳定解析
- 非法 variant / bundle 引用会显式报错
- 示例对象能通过 contract tests

### Phase C: Bundle-Driven Train Snapshot PR

目标：

- 让训练入口开始接受 `bundle_id`，并把输入对象稳定落盘为 snapshot

建议交付：

- `run_train.py` 支持 `bundle_id`
- 训练输出 `config_snapshot.json` 或 `experiment_snapshot.yaml`
- snapshot 中显式记录 factor variants、label spec、split spec、strategy spec

建议改动文件：

- `scripts/run_train.py`
- `qsys/research/spec.py`
- 可能新增 `qsys/research/snapshot.py`
- 对应测试文件

本阶段不做：

- 不删除旧 `feature_set` 入口
- 不强制所有训练都改走新路径

验收标准：

- 同一训练 run 能追溯到 bundle 和 factor variants
- 新旧入口可并存
- snapshot 可被后续 backtest / strict_eval 消费

### Phase D: Signal/Backtest Snapshot Integration PR

目标：

- 让 `run_backtest.py`、`run_strict_eval.py`、signal metrics 正式接入 experiment snapshot

建议交付：

- backtest 读取 prediction + snapshot
- strict eval 读取 prediction + snapshot
- 报告中显式展示 `bundle_id`、strategy spec、cost spec

建议改动文件：

- `scripts/run_backtest.py`
- `scripts/run_strict_eval.py`
- `qsys/evaluation/*`
- `qsys/reports/unified_schema.py`

本阶段不做：

- 不重写 backtest engine
- 不引入复杂 execution simulator

验收标准：

- signal eval 与 portfolio result 继续严格分层
- 报表可反向追溯到 snapshot
- `top_k` 只在 strategy/backtest 层出现

### Phase E: Mainline Object Migration PR

目标：

- 把当前主线讨论对象正式迁入新体系

优先对象：

- `173`
- `254`
- `254 norm`
- 当前主线 bundle
- 当前 absnorm 比较相关对象

建议交付：

- 这些对象对应的 definition/variant manifests
- 至少一版主线 bundle manifest
- 历史主线实验的最小 lineage 补录

本阶段不做：

- 不追求所有历史实验都结构化回填
- 不追求一次迁完全部 feature groups

验收标准：

- 对主线因子可明确回答：base 是谁、variant 是谁、bundle 在哪、最近证据是什么
- `254` 与 `254 norm` 的讨论不再只存在于聊天/脚本里

### Phase F: Promotion / Decision Layer PR

目标：

- 把“研究结论”正式建模

建议交付：

- decision record schema
- `accept_research_only / promote_to_core / reject / park` 状态定义
- report -> decision 的最小对接

建议改动文件：

- `qsys/research/decision.py` 或等价模块
- `qsys/reports/*`
- wiki / docs 的结果沉淀指引

验收标准：

- 主线实验不再只有结果，没有结论
- bundle 与 variant 的状态可以被系统读取和展示

## Priority Order

推荐优先级：

1. Phase A 文档基线
2. Phase B manifest schema
3. Phase C bundle-driven train snapshot
4. Phase D signal/backtest snapshot integration
5. Phase E mainline object migration
6. Phase F promotion / decision layer

这个顺序的核心原因是：

- 如果先改训练和回测，而对象层没立起来，会继续把治理问题藏进脚本逻辑
- 如果先要求全量迁移历史对象，工程量会立刻失控
- 如果不先做 snapshot，后面的评估和报表无法稳定对接

## File-Level Change Plan

### 现阶段建议保留不动的模块

优先保留：

- `qsys/data/adapter.py`
- `qsys/feature/groups/*.py`
- `qsys/feature/transforms.py`
- `qsys/backtest.py`
- `qsys/strategy/engine.py`

原因：

- 这些模块承担的是当前可跑能力，不应在对象层未立稳前就大动

### 现阶段建议逐步降权的模块

- `qsys/feature/library.py`

降权方向：

- 减少其作为“事实来源”的职责
- 逐步把 bundle/variant 定义迁到 manifest objects
- 保留必要兼容层，但不再继续向这里堆长期治理逻辑

### 现阶段建议逐步增强的模块

- `qsys/research/spec.py`
- `qsys/reports/unified_schema.py`
- `scripts/run_train.py`
- `scripts/run_backtest.py`
- `scripts/run_strict_eval.py`

增强方向：

- 支持对象化 snapshot
- 支持 `bundle_id`
- 支持更清晰的 report lineage

## Documentation Upgrade Plan

以下文档建议在后续逐步升级：

### `docs/features/feature_system.md`

要补的内容：

- factor definition 与 feature list 的关系
- variant lineage 的长期规则
- bundle 作为研究对象的角色

### `docs/features/research_framework_v1.md`

要补的内容：

- `feature_set` -> `bundle_id` 的迁移说明
- factor / variant / bundle / decision 四层对象
- signal eval / backtest 如何消费 experiment snapshot

建议形式：

- 要么新增 `research_framework_v2.md`
- 要么在 V1 中明确“本文件被对象层设计补充”

### `docs/research/*.md`

要补的内容：

- 以后实验文档尽量引用 experiment snapshot / bundle id
- 少写无法追溯输入对象的一次性结论

## Test Plan By Phase

### Phase B

- manifest parse tests
- invalid reference tests
- schema required field tests

### Phase C

- `bundle_id` -> feature resolution tests
- train snapshot serialization tests
- snapshot reproducibility tests

### Phase D

- snapshot-fed backtest tests
- signal eval independence tests
- report lineage tests

### Phase E

- mainline object migration regression tests
- `173` / `254` / `254 norm` object resolution tests
- bundle diff tests

### Phase F

- decision schema tests
- decision/report linkage tests
- core/research state rendering tests

## Risks

### 风险 1：对象层过重

表现：

- schema 太复杂
- manifest 写起来过于繁琐
- 个人量化维护成本反而上升

缓解：

- 第一阶段只做最小必要字段
- 不强求一次把所有历史细节都结构化

### 风险 2：新旧路径长期双轨

表现：

- `feature_set` 和 `bundle_id` 长期并存，形成双重事实来源

缓解：

- 从 Phase C 开始明确新入口优先级
- 对主线 bundle 先迁，再逐步淘汰旧入口

### 风险 3：过早重写核心计算链路

表现：

- 为了对象化治理，误把 adapter / builder / backtest 整体重写

缓解：

- 明确本路线先立对象，后迁消费端
- 核心计算模块优先保持稳定

## Done Criteria

- 文档层已经把大需求拆成一条清晰实施线
- 后续每个实现 PR 都能明确落在哪个 phase
- 已说明每个 phase 的产物、边界、验收与风险
- 不再需要每次实现前重新讨论“先做什么、后做什么”

## Notes

- 这份文档服务的是工程切分，而不是替代对象模型文档。
- 对象模型、迁移原因与接口草案，以 `factor_governance_and_research_migration.md` 为主；本文件负责把它拆成可执行提单。
