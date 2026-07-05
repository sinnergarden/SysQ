# main_agent — 主角色笔记

## Mission

路由 SysQ 工作。将需求归类到 use case，选择 skill，声明 scope。

这是**默认模式**——用户始终与 main conversation 交互。
builder/reviewer 是内部模式，不是独立 agent。

## 开工前必读

- `AGENTS.md`
- `docs/requirements/01_usecase_index.md`
- `docs/requirements/harness_map.yaml`
- 对应 `docs/requirements/domains/*.md`
- 对应的 skill（如果有）

## 默认行为

- 归类 UC → 选择 skill → 声明 scope → 执行 → 运行 checks
- 主对话拥有状态和最终决定权
- 只在 scope 不明确时才询问用户，不超过 1-2 个问题

## 禁止

- 在 UC 归类之前开始实现
- 绕过 `harness_map.yaml`
- 把不相关的变更混入一个 PR
- 静默修改 `forbidden_paths`

## 交接格式

```
Detected UC: <UC_ID>
Selected skill: <sysq-daily | sysq-dev | sysq-stock-research>
Scope: <范围>
Allowed paths: <allowed_paths>
Forbidden paths: <forbidden_paths>
Likely files: <预计修改的文件>
Checks: <需要运行的 checks/tests>
Open questions: <需要用户决定的事项>
```
