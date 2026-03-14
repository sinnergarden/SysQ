# 贡献指南

感谢您有兴趣为 SysQ 做出贡献！本文档概述了开发标准、工作流和最佳实践。

在开始前，建议先阅读：
- [PROJECT_TARGETS.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/PROJECT_TARGETS.md)
- [ENVIRONMENT.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ENVIRONMENT.md)
- [context.md](file:///Users/liuming/Documents/trae_projects/SysQ/context.md)

## 开发工作流

SysQ 强调 `tutorial.ipynb` 作为**单一事实来源**（Single Source of Truth）。

1.  **单一事实来源**：`tutorial.ipynb` 是主要参考。调试脚本是允许的，但使用后必须删除。
2.  **简单性**：`tutorial.ipynb` 应专注于 API 调用。复杂的逻辑必须封装在 `qsys` 包中。
3.  **验证**：在任何代码更改后，运行 `tests/test_tutorial_flow.py`（使用 50 只股票的小样本）以确保核心流程保持完整。

## 代码标准

### 1. 类型检查与 Lint
在提交之前，请确保代码可以通过 Python 编译检查：

```bash
python -m compileall qsys scripts tests
```

### 2. 单元测试
SysQ 有严格的 API 契约。请确保所有测试通过：

```bash
python -m unittest discover tests
```

建议按改动范围执行最小回归：
- 改动 `qsys/data`：`python -m unittest tests/test_data_quality.py`
- 改动 `qsys/live`：`python -m unittest tests/test_live_trading.py`
- 改动核心 API：`python -m unittest tests/test_core_api_contracts.py`

### 3. 文档
- 所有公共 API（尤其是 `context.md` 中列出的）必须有清晰的文档字符串。
- 如果更改了核心架构，请更新 `README.md` 和 `context.md`。

## 提交规范

请使用清晰的提交信息：

- `feat`: 新功能
- `fix`: 修复 bug
- `docs`: 文档更改
- `style`: 代码格式（不影响逻辑）
- `refactor`: 代码重构
- `test`: 添加或修改测试
- `chore`: 构建过程或辅助工具更改

## 如何添加新特性

1.  在 `qsys` 中实现逻辑。
2.  在 `tests/` 中添加单元测试。
3.  如果它是核心流程的一部分，请更新 `tutorial.ipynb` 进行演示。
4.  运行完整测试套件。
5.  提交 Pull Request。
