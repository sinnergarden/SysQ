# docs/agents — AI 操作参考笔记

## 文件分层

```
workspace .claude.md          ← 只做 SysQ 任务分流
  └→ AGENTS.md                ← SysQ AI 操作权威入口（skill-first + harness-first）
       ├→ .claude/skills/*/   ← task skills（sysq-daily 等）
       ├→ docs/agents/        ← lightweight role notes（非 runtime agent team）
       ├→ harness/checks/     ← 可执行护栏
       └→ docs/requirements/  ← use case registry + harness 约束
```

## 核心原则

- **主对话拥有状态和最终决定权。** 所有代码写入和最终决策在主对话中完成。
- **task first, not agent first。** 如果任务行为重要，编码为 skill（`.claude/skills/`）。
  如果正确性重要，编码为 harness check（`harness/checks/`）。
- **Role notes 不是 skill 或 harness 的替代品。** 它们是辅助性参考，不定义执行行为。
- **不鼓励用户维护多个 agent。** 默认 main_agent 对用户服务，builder/reviewer 是内部模式。

## 角色笔记（lightweight）

| 文件 | 说明 |
|------|------|
| `main_agent.md` | 默认角色：需求归类、scope 声明、PR 管理 |
| `builder_agent.md` | 实现模式（非独立人格）：工程实现、测试、文档同步 |
| `reviewer_agent.md` | 审查模式（非独立人格）：审查清单、边界检查 |

## 已规划但暂未独立的角色

| 角色 | 预期职责 |
|------|---------|
| research_agent | 量化研究：特征/标签/信号/回测方案 |
| operator_agent | daily ops：运行产物、日志、环境、PR 状态 |
| ui_agent | UI/API/可视化需求 |
| stock_research_agent | 财报/公告/新闻 agent 研究 |

这些角色等需求明确后再独立。**目前不需要**。

## Loop Engineering

- `SYSQ_LOOP_ENGINEERING.md` — failure-driven improvement loop model.
- `loop_memory.md` — validated lessons learned from real failures. Stores accepted lessons only; not a scratchpad or conversation transcript.

\`sysq-reviewer\` subagent (\`.claude/agents/sysq-reviewer.md\`) may propose improvements read-only. The main conversation owns decisions.
