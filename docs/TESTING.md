# TESTING

## 测试框架

- 当前主测试框架为 `unittest`。
- 部分开发者会使用 `pytest` 运行，但测试编写基线按 `unittest` 组织。

## 如何运行测试

1. 先设置模块路径：

```bash
export PYTHONPATH=$PYTHONPATH:$(pwd)
```

2. 运行全量测试：

```bash
python -m unittest discover tests
```

3. 按改动范围运行最小回归：

```bash
python -m unittest tests/test_live_trading.py
python -m unittest tests/test_data_quality.py
python -m unittest tests/test_core_api_contracts.py
```

4. 若使用 pytest，只作为运行器，不改变测试组织方式：

```bash
pytest -q tests
```

## 哪些改动必须写测试

- 新增公共 API 或修改公共 API 行为。
- 修改交易执行逻辑、账户状态逻辑、计划生成逻辑。
- 修改数据适配和字段映射逻辑。
- 修复线上或高优先级缺陷。

## 测试前置条件

- 仓库根目录存在 `config/settings.yaml`。
- `config/settings.yaml` 至少包含可用的 `data_root` 配置。
- 数据相关测试需保证 `data/qlib_bin` 可读取。

## 合并前门禁

- 所有改动至少通过 `compileall + unittest discover`。
- 涉及交易执行与账户状态改动，必须通过 `tests/test_live_trading.py`。
- 涉及数据字段或映射改动，必须通过 `tests/test_data_quality.py`。
- 涉及 API 变更，必须补充或更新契约测试。

## Mock 原则

- 优先使用最小真实数据样本，减少过度 mock。
- 对外部系统依赖可使用 mock，但要保留关键路径校验。
- 不允许用 mock 掩盖真实行为差异。

## 端到端测试建议

- 使用 `scripts/run_daily_trading.py` 做脚本级回归。
- 运行前确认配置可用、数据路径存在、模型可加载。
- 端到端失败时先定位是“配置问题、数据问题、逻辑问题”中的哪一类。

## 常见失败原因

- 未设置 `PYTHONPATH` 导致 `ModuleNotFoundError`。
- 本地缺失 `config/settings.yaml`。
- 数据目录为空或字段不匹配配置。
- 使用 pytest 时未设置 `PYTHONPATH`，导致导入失败。
