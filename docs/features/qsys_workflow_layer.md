# FEATURE: qsys_workflow_layer

## Goal

- 为 Qsys 增加一层轻量的 workflow / plugin abstraction，把高频研究与运营流程沉淀成稳定入口。
- 不重写底层 Python engine，而是在现有代码、脚本、文档之上补一层可复用的规则层。
- 让后续 agent 可以基于 `skills` 产出更稳定的实现 PR，而不是每次从散装文档和聊天记录重新理解需求。

## Why Now

当前 Qsys 已经具备最小闭环，但仍存在以下问题：

- 高频流程仍偏脚本驱动，入口多，口径容易漂移。
- 一部分关键规则散落在 `README`、`RUNBOOK`、feature 文档、聊天结论和脚本默认值里。
- 文档大多是给人看的，不是给 agent / 自动流程直接消费的执行规则层。
- 结构化 report 尚未完全统一，导致后续自动衔接能力偏弱。

## Scope

本功能只做 workflow layer 的设计与首批草案，不在本阶段重写核心逻辑。

包括：
- 定义 `skill / command / connector / output contract` 的最小结构
- 给出现有文档到 workflow asset 的映射表
- 起草首批 commands 与 skills
- 明确它们与现有 Python 入口的关系

不包括：
- 不重写训练、回测、daily ops 主逻辑
- 不引入自动下单
- 不强行兼容某个外部宿主格式

## Core Concepts

### Skill

一个 `SKILL.md` 文件，用来描述：
- 什么场景下应触发
- 启用后按什么 workflow 执行
- 需要哪些输入
- 输出什么结构化结果
- 哪些情况必须阻断

它不是业务代码本身，而是“可被 agent 复用的结构化 SOP”。

### Command

一个显式入口，通常是一个 markdown 文件，用来描述：
- 用户想做什么
- 应加载哪个 skill
- 最终应该调用哪些底层脚本或模块

它不是复杂业务实现，而是入口与编排层。

### Connector

一个稳定的数据/状态访问抽象。当前阶段不一定实现成 MCP，先定义语义边界：
- `data_status`
- `feature_store`
- `model_registry`
- `backtest_store`
- `shadow_state`
- `repo_state`

### Output Contract

每个高频流程既输出人读得懂的 markdown 摘要，也输出 agent 可续接的 json artifact。

## Physical Layout

建议目录：

```text
qsys_plugins/
  core/
    plugin.json
    README.md
    connectors.json
    commands/
      preopen-plan.md
      feature-audit.md
      rolling-eval.md
    skills/
      trading-calendar-guard/SKILL.md
      feature-readiness-audit/SKILL.md
      train-split-discipline/SKILL.md
      shadow-execution-planner/SKILL.md
```

## Mapping: existing docs -> workflow assets -> code

| Existing source | Lift into skill/command? | Asset | Underlying code / entrypoint |
|---|---|---|---|
| `docs/RUNBOOK.md` daily pre-open checklist | Yes | `preopen-plan` + `trading-calendar-guard` + `shadow-execution-planner` | `scripts/run_daily_trading.py`, `qsys/live/*` |
| `docs/RUNBOOK.md` weekly model ops | Partly | future `weekly-refresh` + `train-split-discipline` | `scripts/run_train.py`, `scripts/run_strict_eval.py` |
| `docs/strict-evaluation.md` | Yes | `rolling-eval` + `train-split-discipline` | `scripts/run_backtest.py`, `scripts/run_strict_eval.py`, `qsys/evaluation` |
| `docs/features/ops_requirements.md` | Yes | shared output contract + blocker rules | multiple scripts |
| `docs/features/post_close_reconciliation.md` | Later | future `postclose-review` / `shadow-sync` | `scripts/run_post_close.py`, `qsys/live/reconciliation.py` |
| `docs/features/daily_signal_monitoring.md` | Later | future `signal-quality-review` | `scripts/run_signal_quality.py` |
| `ROADMAP.md` / `ARCHITECTURE.md` | Partly | plugin scope / boundaries / invariants | documentation only |
| ad-hoc chat rules on T+1 / signal_date | Yes | `trading-calendar-guard`, `shadow-execution-planner` | daily ops code + future adapter |

## First Batch Recommendation

### Commands

1. `preopen-plan`
- 目标：生成盘前可执行计划摘要
- 调用 skill：`trading-calendar-guard`、`shadow-execution-planner`
- 依赖：`scripts/run_daily_trading.py`

2. `feature-audit`
- 目标：判断某个 feature set 是否适合入训
- 调用 skill：`feature-readiness-audit`
- 依赖：现有数据健康检查、feature 覆盖脚本；后续可补统一入口脚本

3. `rolling-eval`
- 目标：按统一口径跑 rolling / strict evaluation 摘要
- 调用 skill：`train-split-discipline`
- 依赖：`scripts/run_backtest.py`、`scripts/run_strict_eval.py`

### Skills

1. `trading-calendar-guard`
- 固化 `signal_date / execution_date / raw_latest` 语义
- 明确何时必须阻断，不允许出假计划

2. `feature-readiness-audit`
- 检查 coverage、缺失、重复列、日期错位、主列遮蔽
- 输出 `ready / warning / blocked`

3. `train-split-discipline`
- 固化 non-overlap split、主评估窗口、辅助评估窗口、`top_k=5`
- 阻断 train/test overlap 与短窗自嗨

4. `shadow-execution-planner`
- 把 `target plan` 与 `executable plan` 分开
- 显式处理 T+1、现金回流、未成交残留与费用假设

## Relation to Existing Docs

- `README / ARCHITECTURE / RUNBOOK / features/*` 继续保留，负责“给人看”。
- `skills` 负责“给 agent / 自动流程消费”。
- `commands` 负责把高频任务变成稳定入口。
- 底层 Python 代码继续是唯一业务真相来源。

换句话说：workflow layer 不是替代现有文档，而是从现有文档中抽取最常复用、最容易出错、最值得稳定复用的那部分规则。

## Done Criteria

- 仓库内存在 workflow layer 设计文档
- `qsys_plugins/core` 有首批 skeleton
- 首批 3 个 command 与 4 个 skill 已起草
- 每个首批 asset 都指向明确的现有代码入口
- 后续实现任务可以直接据此拆为 PR

## Test Plan

当前阶段以文档与结构设计为主：
- 检查目录结构是否自洽
- 检查每个 skill 是否有清晰触发条件、workflow、输出契约
- 检查每个 command 是否能映射到现有脚本或模块

后续实现阶段再补：
- adapter 代码测试
- command 输出契约测试
- daily / eval 集成测试

## Rollback Plan

- 若这套 workflow layer 被证明引入额外复杂度，可整体按目录回滚
- 回滚时不影响现有 Python engine 与 runbook
