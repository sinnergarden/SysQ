# Feature Refactor Validation

## 1. 验证目标

本次验证除了确认 feature 系统重构未破坏现有流程，还额外确认“语义化命名收束”没有破坏兼容性。

## 2. 实际执行命令

### 2.1 编译检查

```bash
./.envs/test/bin/python -m compileall qsys scripts tests
```

结果：通过。

### 2.2 全量单元测试

```bash
PYTHONPATH=$(pwd) ./.envs/test/bin/python -m unittest discover tests
```

结果：

- `Ran 75 tests`
- `OK`

### 2.3 research builder 最小脚本验证

```bash
PYTHONPATH=$(pwd) ./.envs/test/bin/python scripts/run_feature_build.py \
  --codes 600176.SH,000338.SZ \
  --start 2026-03-10 \
  --end 2026-03-25 \
  --feature_set short_horizon_state_core_v1 \
  --output scratch/feature_build_sample.csv
```

结果：通过。

## 3. 这次特别验证了什么

### 3.1 语义化 feature set 可用

本次新增或强调的语义化 set 名称，例如：

- `price_volume_expression_core_v1`
- `short_horizon_state_core_v1`
- `atomic_panel_plus_state_v1`
- `mixed_provider_demo_v1`

都可以被正确解析。

### 3.2 历史名字仍兼容

`FeatureLibrary` 和 resolver 仍保留历史 alias，因此现有训练入口和测试未被破坏。

### 3.3 正式 group 与 builder_group 的分离可用

registry 现在按通用语义分组，但 builder 仍可通过 `builder_group` 正常解析所需实现层模块，说明这次命名收束没有打断实际装配。

## 4. 结论

本次第二轮命名收束已经满足以下条件：

- 正式 taxonomy 从历史名字切换为通用语义
- 历史入口保持兼容
- 全量测试通过
- research builder 最小流程可跑通

因此当前 feature 系统已经可以作为后续开发的稳定基座继续使用。
