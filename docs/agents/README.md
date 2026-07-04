# docs/agents — AI Agent 操作文档

## 文件分层

```
workspace .claude.md          ← 只做 SysQ 任务分流，不包含内部规则
  └→ AGENTS.md                ← SysQ AI 操作权威入口（本文档）
       ├→ docs/agents/main_agent.md     ← 主代理行为规范
       ├→ docs/agents/builder_agent.md  ← 构建代理行为规范
       ├→ docs/agents/reviewer_agent.md ← 审查代理行为规范
       └→ docs/requirements/            ← use case registry + harness 约束
            ├→ 01_usecase_index.md
            ├→ harness_map.yaml
            └→ domains/*.md
```

## 核心原则

- **用户不需要手动切换多个 agent**。`main_agent` 是默认接口，其他角色是 `main_agent` 内部选择的模式。
- **所有 SysQ AI 规则都在仓库内**，随代码版本化、随 PR 审查。不从 workspace 级配置加载复杂规则。
- **Harness 优先**：关键约束写成自动化 check，不在 prompt 中反复强调。

## 已落地的角色

| 角色 | 文档 | 职责 |
|------|------|------|
| main_agent | `main_agent.md` | 需求路由、角色选择、scope 声明、PR 管理 |
| builder_agent | `builder_agent.md` | 在已选定的 UC 和 scope 内实现变更 |
| reviewer_agent | `reviewer_agent.md` | 审查 PR：UC 匹配、边界违规、entrypoint 纪律、测试 |

## 已规划但暂未独立的角色

| 角色 | 预期职责 |
|------|---------|
| research_agent | 量化研究：特征/标签/信号/回测方案 |
| operator_agent | daily ops：运行产物、日志、环境、PR 状态 |
| ui_agent | UI/API/可视化需求 |
| stock_research_agent | 财报/公告/新闻 agent 研究 |

这些角色等需求明确后再独立成文档。
