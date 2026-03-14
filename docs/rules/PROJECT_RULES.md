# 项目规则 (Project Rules)

本文档列出了 SysQ 项目必须遵循的核心规则和检查项。

## 代码检查 (Lint)

所有 Python 代码必须通过编译检查，以避免基本的语法错误。

```bash
python -m compileall qsys scripts tests
```

## 类型检查 (Typecheck)

（目前与 Lint 步骤相同，未来可引入 MyPy）

```bash
python -m compileall qsys scripts tests
```

## 测试基线

提交前至少执行：

```bash
python -m unittest discover tests
```

按改动范围追加执行：

```bash
python -m unittest tests/test_data_quality.py
python -m unittest tests/test_live_trading.py
python -m unittest tests/test_core_api_contracts.py
```

## 核心原则

1.  **单一事实来源**：`tutorial.ipynb` 是主要参考。调试脚本是允许的，但使用后必须删除。
2.  **简单性**：`tutorial.ipynb` 应专注于 API 调用。复杂的逻辑必须封装在 `qsys` 包中。
3.  **验证**：在任何代码更改后，运行 `tests/test_tutorial_flow.py`（使用 50 只股票的小样本）以确保核心流程保持完整。

## 产物管理

-   **删除**：任何临时调试脚本（例如 `debug_tutorial.py`，`reproduce_issue.py`）。
-   **保留**：`tutorial.ipynb`，`qsys/` 包，`tests/`。
