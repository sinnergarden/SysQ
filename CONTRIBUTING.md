# CONTRIBUTING

本文档定义本仓库的协作流程。它既面向开发者，也面向 AI 助手。

## 开发流程

1. 在本地拉取最新代码并创建分支。
2. 先阅读 [AGENTS.md](file:///Users/liuming/Documents/trae_projects/SysQ/AGENTS.md) 与 [ARCHITECTURE.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ARCHITECTURE.md)。
3. 若是新功能，先创建 `docs/features/<feature_name>.md`。
4. 做小步修改，避免一次混入多个目标。
5. 为非平凡改动补充测试与文档。
6. 本地通过检查后再提交。

## 新功能文档要求

- 路径：`docs/features/<feature_name>.md`
- 模板：`docs/features/new_feature.md`
- 必填项：
  - Goal
  - Use Cases
  - API Change
  - UI（若有）
  - Constraints
  - Done Criteria
- 没有功能文档的新功能改动，不进入合并。

## 分支与 PR 规则

- 分支命名建议：`feat/*`、`fix/*`、`refactor/*`、`docs/*`、`test/*`。
- 一个分支只解决一个主题。
- PR 描述必须包含：背景、改动点、验证方式、风险与回滚方式。
- 涉及架构边界调整时，先补 ADR 再合并代码。

## Commit Message 规范

建议采用以下前缀：

- `feat`: 新功能
- `fix`: 缺陷修复
- `refactor`: 重构
- `test`: 测试相关
- `docs`: 文档相关
- `chore`: 工程维护

示例：

```text
fix: 修复影子账户在重复执行时的幂等问题
docs: 新增 AGENTS 与架构说明文档
```

## 测试要求

- 默认全量测试命令：

```bash
python -m unittest discover tests
```

- 按改动范围做最小回归：
  - 改动 `qsys/live`：`python -m unittest tests/test_live_trading.py`
  - 改动 `qsys/data`：`python -m unittest tests/test_data_quality.py`
  - 改动核心契约：`python -m unittest tests/test_core_api_contracts.py`

## Lint 与格式要求

当前基线检查：

```bash
python -m compileall qsys scripts tests
```

后续若引入 ruff 或 mypy，以仓库规则文件为准。

## 哪些改动需要先开 Issue 或先讨论

- 公共 API 变更
- 数据库 schema 变更
- 交易执行规则变更
- 架构分层与依赖方向变更
- 删除核心模块或核心脚本

## 文档同步要求

当行为发生变化时，至少更新以下之一：

- [README.md](file:///Users/liuming/Documents/trae_projects/SysQ/README.md)
- [RUNBOOK.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/RUNBOOK.md)
- [TESTING.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/TESTING.md)
- 对应 ADR 文档
