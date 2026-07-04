# AGENTS — SysQ AI 操作总入口

> 本文档是 SysQ 仓库的 AI 操作权威入口。所有 SysQ 相关工作必须从这里开始。
> 系统架构见 `docs/ARCHITECTURE.md`，use case registry 见 `docs/requirements/01_usecase_index.md`。

---

## 1. Mission

SysQ 是一个个人可维护的 A 股日频量化系统。AI agent 负责：需求拆解、方案实施、测试修复、文档同步、边界检查。
人主导方向与高风险决策，AI 辅助执行。

---

## 2. 必须工作流

所有 SysQ 任务必须按以下顺序：

```
1. 将请求归类到 docs/requirements/01_usecase_index.md 中的一个 use case。
2. 读取 docs/requirements/harness_map.yaml，查看 owner_agent、entrypoints、allowed_paths、forbidden_paths、checks。
3. 读取对应的 domain 文档（docs/requirements/domains/*.md）。
4. 选择角色模式：
   - main_agent — 路由/规划
   - builder_agent — 实现
   - reviewer_agent — 审查/harness/边界
5. 声明检测到的 UC、角色、影响范围、禁止路径、需要运行的 checks。
6. 仅在 allowed 范围内实施变更。
7. 运行相关的 harness checks 和 tests。
```

---

## 3. Use Case First 原则

- **不得在识别 use case 之前开始任何 SysQ 代码变更。**
- 如果请求不匹配任何 use case，使用 `UC_TEMPORARY_REQUESTS` 并向用户确认是否作为临时请求处理。
- 同一临时请求出现超过 2 次，必须补文档并考虑收束为正式 use case。

---

## 4. 边界规则

- **不得修改** `harness_map.yaml` 中定义的 `forbidden_paths`。
- **不得创建顶层脚本**，除非该脚本被列为 canonical entrypoint。
- **不得在同一个 PR 中混合** 架构重构、业务逻辑变更、文档清理、测试重写。
- **不得修改** production/daily/promotion 行为，除非 selected UC 明确允许。
- **docs/requirements/harness_map.yaml** 是机器约束源，entrypoints 和 allowed/forbidden paths 以它为准。

---

## 5. 角色文档

| 角色 | 文档 | 何时使用 |
|------|------|---------|
| main_agent | `docs/agents/main_agent.md` | 默认角色：需求路由、scope 声明、PR 管理 |
| builder_agent | `docs/agents/builder_agent.md` | 当任务以工程实现为主（测试、修复、小改动） |
| reviewer_agent | `docs/agents/reviewer_agent.md` | 当任务以代码审查、边界检查为主 |

默认使用 main_agent。builder/reviewer 是 main_agent 内部选择的模式，**用户不需要手动切换**。

---

## 6. 基础 Checks

根据 UC 选择相关检查，至少运行：

```bash
python harness/checks/check_usecase_registry.py
python harness/checks/check_scripts_entrypoints.py
python harness/checks/check_no_latest_model_resolution.py
python harness/checks/check_model_resolution_boundary.py
```

不强制每次都跑全部 pytest。根据更改范围选择相关测试目录：

```bash
python -m pytest tests/<module>/ -q
```

---

## 7. Handoff 格式

每次输出完成后，按以下格式提供摘要：

```
Detected UC: <UC_ID>
Selected role: <main|builder|reviewer>
Scope: <本次变更的范围>
Allowed paths: <相关的 allowed_paths>
Forbidden paths: <本次未触碰的 forbidden_paths>
Changed files: <文件路径列表>
Checks run: <运行的 check 和测试>
Risks: <剩余风险>
Open questions: <需要用户决策的事项>
```

---

## 8. 文档同步义务

修改了以下内容，必须同步更新对应文档：
- 新增/修改 entrypoint → 同步 `harness_map.yaml` 和对应 domain doc
- 新增 use case → 同步 `01_usecase_index.md`、`harness_map.yaml`、对应 domain doc
- 修改架构/角色 → 同步 `00_sysq_vision.md`、本文档
