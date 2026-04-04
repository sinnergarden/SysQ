# FEATURE: qsys_workflow_adapter_plan

## Goal

- 为 `qsys_plugins/core` 的首批 commands 提供可实现的 adapter 设计。
- 明确 command 层如何调用现有 `scripts/` 与 `qsys/*` 模块。
- 把下一步实现拆成可直接提交 PR 的范围，而不是继续停留在概念层。

## Design Principles

- adapter 层只做参数归一化、现有入口调用、结果归一化与结构化输出。
- 不在 adapter 层重写训练、回测、daily ops 主逻辑。
- 优先复用现有 structured report；缺什么就补薄层，不先推翻旧实现。
- command 输出统一同时支持：
  - 人类阅读的 markdown summary
  - agent 续接的 json artifact

## Proposed Layout

```text
qsys/workflow/
  __init__.py
  contracts.py
  preopen.py
  feature_audit.py
  rolling_eval.py
```

说明：
- `qsys/workflow/*` 是 Python adapter 层
- `qsys_plugins/core/*` 是 workflow asset 层
- `scripts/*` 与 `qsys/live|evaluation|data/*` 继续是底层执行层

## Command -> Adapter -> Existing Entry Mapping

| Command | Adapter module | Existing logic to reuse | Gap to fill |
|---|---|---|---|
| `preopen-plan` | `qsys.workflow.preopen` | `scripts/run_daily_trading.py`, `qsys.live.manager.LiveManager`, `qsys.data.health.inspect_qlib_data_health`, `qsys.live.signal_monitoring.*` | 需要把结果整理成稳定 json，而不是只依赖 stdout + report artifact |
| `feature-audit` | `qsys.workflow.feature_audit` | `qsys.data.health`, 现有 feature coverage / debug 逻辑 | 缺一个统一入口与统一 decision schema |
| `rolling-eval` | `qsys.workflow.rolling_eval` | `scripts/run_strict_eval.py`, `qsys.evaluation.StrictEvaluator` | 需要补 risk flags / promotion suggestion / unified json contract |

## Shared Output Contract

所有 adapter 返回一个统一顶层结构：

```json
{
  "task_name": "preopen-plan",
  "status": "ok",
  "decision": "ready",
  "blocker": null,
  "input_params": {},
  "data_status": {},
  "model_info": {},
  "artifacts": {},
  "summary": {},
  "risk_flags": [],
  "next_action": null,
  "markdown_summary": "..."
}
```

说明：
- `status`: task 本身是否执行成功，取值如 `ok` / `error`
- `decision`: 业务判断，取值如 `ready` / `blocked` / `warning`
- `summary`: command-specific 内容
- `markdown_summary`: 直接给人看

## Adapter 1: preopen-plan

### Objective

把现有 daily trading pre-open 流程包装成一个可稳定消费的函数式入口。

### Proposed API

```python
run_preopen_plan(
    signal_date: str,
    execution_date: str | None = None,
    model_path: str | None = None,
    top_k: int = 5,
    min_trade: int = 5000,
    shadow_cash: float = 1_000_000.0,
    real_cash: float = 20_000.0,
    skip_update: bool = True,
    skip_signal_quality_gate: bool = False,
) -> dict
```

### Reuse Plan

直接复用：
- `resolve_signal_and_execution_date()` 的日期逻辑
- `inspect_qlib_data_health()` 的 readiness 检查
- `ModelScheduler.resolve_production_model()` 的模型选择
- `LiveManager.generate_signal_basket()` 与 `run_daily_plan()`
- `collect_signal_quality_snapshot()` 的 gate

### Required Summary Payload

```json
{
  "signal_date": "2026-04-03",
  "execution_date": "2026-04-06",
  "target_portfolio": [],
  "executable_portfolio": [],
  "blocked_symbols": [],
  "signal_quality_gate": {},
  "cash_utilization": {},
  "assumptions": {
    "top_k": 5,
    "min_trade": 5000
  }
}
```

### Minimal PR Scope

首个 PR 只做：
- 提供 `run_preopen_plan()` adapter
- 包装已有 pre-open 流程为稳定 json 输出
- 不改底层交易计划逻辑
- 若 `target_portfolio` 与 `executable_portfolio` 当前还无法完全分离，可先：
  - `target_portfolio` 使用 signal basket
  - `executable_portfolio` 使用当前 real/shadow plan
  - 把差异与 caveat 明确写入 `risk_flags`

### Acceptance

- 可以在 Python 中无 stdout 依赖地拿到 pre-open 结果 dict
- dict 中显式包含 `signal_date` / `execution_date` / data_status / model_info / plan summary
- block 条件能显式返回，而不是只靠异常日志

## Adapter 2: feature-audit

### Objective

提供统一的 feature readiness 审计入口，输出 `ready / warning / blocked`。

### Proposed API

```python
run_feature_audit(
    feature_set: str,
    start_date: str,
    end_date: str,
    universe: str = "csi300",
) -> dict
```

### Reuse Plan

可复用：
- `qsys.data.health` 里的日期对齐与行数检查逻辑
- 现有 debug / coverage 脚本中的字段统计逻辑

### Gap

当前 repo 缺一个标准的 feature audit 聚合入口，因此这个 adapter 可能需要：
- 先实现最小版 coverage + missingness + duplicate-column 检查
- 再逐步把临时 debug 逻辑下沉进来

### Minimal PR Scope

- 输出统一 `decision schema`
- 首期支持：coverage、missing ratio、core anomalies
- 先不追求包含所有因子归因分析

## Adapter 3: rolling-eval

### Objective

把 strict evaluation 与 rolling review 统一成一个面向决策的结果对象。

### Proposed API

```python
run_rolling_eval(
    baseline_model_path: str,
    candidate_model_path: str,
    end_date: str | None = None,
    top_k: int = 5,
) -> dict
```

### Reuse Plan

直接复用：
- `StrictEvaluator`
- `StrictEvalReport`

### Gap

当前 strict eval 已有结构化 report，但还缺：
- 标准 risk flags
- promote / hold / investigate suggestion
- 统一顶层 contract

### Minimal PR Scope

- 包装 strict eval 成统一 dict
- 增加 risk flag 规则：
  - top_k != 5
  - missing baseline comparison
  - no auxiliary window
  - excessive drawdown
- 暂不把真正 rolling retrain 全部并入

## Implementation Order

推荐顺序：

1. `preopen-plan`
- 现有代码最完整
- 与 `trading-calendar-guard`、`shadow-execution-planner` 对应最直接
- 能最早形成“skill -> adapter -> code”的闭环

2. `rolling-eval`
- 底层 `StrictEvaluator` 已较成熟
- 适合快速形成第二个闭环

3. `feature-audit`
- 价值高，但当前底层入口最分散
- 适合在前两个闭环稳定后再补

## First PR Recommendation

PR 标题建议：
- `feat: add workflow adapter for preopen plan`

首个 PR 内容建议：
- 新增 `qsys/workflow/contracts.py`
- 新增 `qsys/workflow/preopen.py`
- 为 pre-open 结果补统一 dict contract
- 补最小测试：
  - block case returns `decision=blocked`
  - success case returns required top-level fields
- 文档更新：在 `docs/features/qsys_workflow_layer.md` 中补 adapter status

## Non-Goals

- 不在这一批里把 `qsys_plugins` 真的接到 Claude/OpenClaw 宿主
- 不把 adapter 设计成新的大而全 CLI 体系
- 不在 adapter 层重做 `run_daily_trading.py`
