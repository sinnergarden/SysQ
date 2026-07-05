# AGENTS — SysQ AI 操作总入口

> 本文档是 SysQ 仓库的 AI 操作权威入口。所有 SysQ 相关工作必须从这里开始。
> 系统架构见 `docs/ARCHITECTURE.md`，use case registry 见 `docs/requirements/01_usecase_index.md`。

---

## 1. Mission

SysQ 是一个个人可维护的 A 股日频量化系统。AI 负责：需求拆解、方案实施、测试修复、文档同步、边界检查。
人主导方向与高风险决策，AI 辅助执行。

执行模式是 **skill-first + harness-first**，不是 agent-first：
- **Skills** 定义任务执行轨道（`sysq-daily`、`sysq-dev`）
- **Harness checks** 是执行护栏，模型声称不能替代可执行检查
- **Subagents** 是可选的只读辅助工具
- **主对话** 拥有状态和最终决定权

---

## 2. 必须工作流

所有 SysQ 任务必须按以下顺序：

```
1. 将请求归类到 docs/requirements/01_usecase_index.md 中的一个 use case。
2. 读取 docs/requirements/harness_map.yaml，查看 owner_agent、skills、entrypoints、allowed/forbidden_paths、checks。
3. 读取对应的 domain 文档（docs/requirements/domains/*.md）。
4. 选择主 skill（如果 UC 在 harness_map 中定义了 skills），或有针对性地执行。
5. 声明检测到的 UC、skill、影响范围、禁止路径、需要运行的 checks。
6. 仅在 allowed 范围内实施变更。
7. 运行相关的 harness checks 和 tests。
```

---

## 3. Use Case First

- **不得在识别 use case 之前开始任何 SysQ 代码变更。**
- 如果请求不匹配任何 use case，使用 `UC_TEMPORARY_REQUESTS` 并向用户确认是否作为临时请求处理。
- 同一临时请求出现超过 2 次，必须补文档并考虑收束为正式 use case。

---

## 4. Skill-First Execution

SysQ 的执行是 skill-first，不是 agent-first。使用 task skills 执行具体工作流：

| Skill | 用途 | 对应 UC |
|-------|------|---------|
| `sysq-daily` | 日频运营：数据就绪、标签成熟度、重训资格、推理就绪、候选输出 | UC_DAILY_OPS, UC_MODEL_TRAINING |
| `sysq-dev` | 开发：bug fix、测试、harness、PR 工作 | UC_DIAGNOSTICS, UC_TEMPORARY |
| `sysq-stock-research` | 基本面研究：财报/公告/新闻 PDF 研究、memo 生成 | UC_STOCK_FUNDAMENTAL_RESEARCH |

规则：
- **不要依赖 agent 人格来执行任务行为。** 如果行为重要，编码为 skill。
- **不要依赖模型记忆来保证正确性。** 如果正确性重要，编码为 harness check。
- Skills 在 `.claude/skills/*/SKILL.md` 中定义。没有 skill 的 UC 使用通用的 builder 行为。

---

## 4a. 运行型任务规则

所有 SysQ **运行型任务**（即使不修改代码）也必须走 use case / skill / harness。
涉及 infer、prediction、signal、candidate、latest feature、shadow 前检查的任务：
1. 归类为 UC_DAILY_INFERENCE_RUN。
2. 读取 `docs/agents/sysq-daily` skill 中的 **Manual / Ad-hoc Inference Run** 章节。
3. 确认：signal_date、execution_date、strategy_id、feature_snapshot、model_pointer（解析到具体 model_hash）、train_start/train_end。
4. 输出 provenance（模型 hash、训练范围、数据日期）。
5. 运行 `check_daily_inference_ready.py` 和 `check_inference_artifact.py`。
6. 不得直接给无法追溯的候选股票（缺少 provenance 字段的结果标记为 exploratory）。

## 5. Harness-First Reliability

Skills 是 prompt。Harness checks 是可执行护栏。

**模型的说法不能保证日频/训练/推理的安全性。** 关键决策点必须有自动化 check 覆盖。

目标 harness checks：

```bash
python harness/checks/check_label_maturity.py
python harness/checks/check_daily_inference_ready.py
python harness/checks/check_scripts_entrypoints.py
python harness/checks/check_model_resolution_boundary.py
python harness/checks/check_no_latest_model_resolution.py
python harness/checks/check_usecase_registry.py
```

执行原则：
- 每个 daily/training/inference 决策前检查对应 check
- check 失败时：停止，报告，不要静默绕过
- check 待实现时显式失败或 warning，不要无条件 PASS

---

## 6. Subagent Policy

- **默认在主对话中执行。** 主对话拥有任务连续性、最终决策和代码写入权限。
- **Subagent 只用于有界的只读辅助任务：**
  - 仓库探索
  - 引用搜索
  - 调用链路总结
  - PR / diff 审查
  - 日志/测试失败总结
  - 长文档提取
- **不得委托最终决策给 subagent：**
  - 是否重训
  - 是否推理
  - 最终候选股票输出
  - 生产/promotion 决策
  - 代码实现所有权

---

## 7. 边界规则

- **不得修改** `harness_map.yaml` 中定义的 `forbidden_paths`。
- **不得创建顶层脚本**，除非该脚本被列为 canonical entrypoint。
- **不得在同一个 PR 中混合** 架构重构、业务逻辑变更、文档清理、测试重写。
- **不得修改** production/daily/promotion 行为，除非 selected UC 明确允许。
- **docs/requirements/harness_map.yaml** 是机器约束源，entrypoints 和 allowed/forbidden paths 以它为准。

---

## 8. Improvement Loop

SysQ uses a failure-driven improvement loop.

**Triggers:**
- user correction
- failed harness check
- PR review finding
- repeated agent mistake

**Process:**
1. capture the failure
2. classify the failure type (see `docs/agents/SYSQ_LOOP_ENGINEERING.md`)
3. propose the smallest skill/harness/usecase/memory update
4. validate with the original failure case
5. update `docs/agents/loop_memory.md` only after validation

**Rules:**
- Reviewer subagents (\`sysq-reviewer\`) may propose improvements, but must not directly modify files.
- The main conversation owns the decision.
- Implementation uses the relevant skill, usually \`sysq-dev\`.
- Harness changes must not weaken existing safety checks.

**Reference:**
- \`docs/agents/SYSQ_LOOP_ENGINEERING.md\` — full loop model and failure taxonomy
- \`docs/agents/loop_memory.md\` — validated lessons


### Post-task Loop Check
Every SysQ task, including read-only analysis and runtime inference tasks, must end with a loop check.
Before final response, answer:
1. Did the task follow UC / skill / harness?
2. Was there any user correction, retry, abnormal result, missing provenance, skipped check, or temporary workaround?
3. Did the task reveal one of:
   - skill_gap / harness_gap / harness_semantic_bug / usecase_gap / boundary_gap / memory_gap / artifact_contract_gap / documentation_conflict
4. If yes, output a `Loop Finding`.
5. If the issue is likely to repeat, propose the smallest update to skill / harness / use case / loop_memory.

Output format when issue found:
```
Loop Finding:
- Trigger:
- Failure type:
- Root cause:
- Proposed update:
- Should update skill:
- Should update harness:
- Should update loop_memory:
- Reviewer needed:
```

If no issue: `Loop check: no new framework gap found.`

Must request sysq-reviewer or produce reviewer-style analysis when:
- user says the framework did not help
- agent skipped UC / skill / harness
- task output lacks provenance
- same error appears twice
- modifying skill / harness / loop_memory
- PR changes AI operation rules

When reviewer cannot be called automatically, output:
```
Reviewer needed: yes
Suggested reviewer task: ...
```

## 9. 基础 Checks

根据 UC 选择相关检查，至少运行：

```bash
python harness/checks/check_usecase_registry.py
python harness/checks/check_scripts_entrypoints.py
python harness/checks/check_no_latest_model_resolution.py
python harness/checks/check_model_resolution_boundary.py
```

根据情况额外运行：

```bash
python harness/checks/check_label_maturity.py --trade-date <date> --horizon <h> --train-end <date>
python harness/checks/check_daily_inference_ready.py --trade-date <date> --strategy-id <strategy>
```

不强制每次都跑全部 pytest，根据更改范围选择相关测试目录：

```bash
python -m pytest tests/<module>/ -q
```

---

## 10. Handoff 格式

每次输出完成后，按以下格式提供摘要：

```
Detected UC: <UC_ID>
Selected skill: <sysq-daily | sysq-dev | sysq-stock-research>
Scope: <本次变更的范围>
Allowed paths: <相关的 allowed_paths>
Forbidden paths: <本次未触碰的 forbidden_paths>
Changed files: <文件路径列表>
Checks run: <运行的 check 和测试>
Risks: <剩余风险>
Open questions: <需要用户决策的事项>
```

---

## 11. 文档同步义务

修改了以下内容，必须同步更新对应文档：
- 新增/修改 entrypoint → 同步 `harness_map.yaml` 和对应 domain doc
- 新增 use case → 同步 `01_usecase_index.md`、`harness_map.yaml`、对应 domain doc
- 修改架构/角色 → 同步 `00_sysq_vision.md`、本文档
- 新增 skill → 同步 `.claude/skills/`、本文档 §4
