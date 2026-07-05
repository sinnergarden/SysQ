# Workspace Claude 入口模板

> 本文档不是实际生效的配置文件。它是你**复制到 workspace `.claude.md`** 的模板。
> 实际生效的 SysQ AI 操作规则在 `AGENTS.md` 和 `docs/agents/` 中。

---

## 用途

这个 workspace 可能包含多个仓库和多种任务。
如果用户请求与 **SysQ / Qsys / A 股量化系统 / SysQ 仓库** 相关，请先读取并遵循：

```
<path-to-SysQ>/AGENTS.md
```

将 `<path-to-SysQ>` 替换为你本地 SysQ 仓库的实际路径（如 `workspace/SysQ`）。

## 规则

- `AGENTS.md` 是 SysQ 所有 AI 操作的**权威入口**。任何 SysQ 相关工作都必须从它开始。
- 非 SysQ 任务不要套用 SysQ 的 use case 和 harness 规则。
- 本文档不包含任何 SysQ 内部规则——那些都在仓库内维护。

---

## 操作步骤

1. 判断用户请求是否与 SysQ 相关。
2. 如果不相关：直接处理，不套 SysQ 规则。
3. 如果相关：**先读取 `<path-to-SysQ>/AGENTS.md`**，然后按其中流程执行。
