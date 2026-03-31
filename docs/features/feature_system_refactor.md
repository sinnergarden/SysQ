# FEATURE: feature_system_refactor

## Goal

- 对当前 Qsys 的 feature 代码、文档与注册机制做一次系统化重构。
- 把 Qlib 内置特征、自定义派生特征、正式 feature set 引用机制统一到一套可审计、可维护、可扩展的体系中。
- 在不破坏现有训练、回测、交易主链路的前提下，为未来 tabular 模型与 sequence / Transformer 模型保留一致的底层 feature source of truth。

## Use Cases

- 用例 1：研究人员需要通过显式 feature registry 和具名 feature set 来稳定引用正式特征，而不是在脚本里散落维护列名。
- 用例 2：工程维护者需要快速判断某个特征的业务语义、建模角色、上游依赖、生成方法与适用模型。
- 用例 3：未来引入 sequence / Transformer 模型时，希望继续复用 Qlib panel-friendly 的单日特征表达，而不是再造底层存储。
- 用例 4：迁移旧分组、旧命名和历史 `phase1 / phase12 / phase123` 入口时，需要兼容现有调用方并提供清晰迁移说明。

## API Change

- 是否新增 API：是。
- 是否修改现有 API：是，但以兼容扩展为主。
- 变更点列表：
  - 增强 `qsys.feature.registry`，支持显式 feature registry 与 feature set 解析。
  - 允许以 feature id / feature set 名称解析 Qlib 内置特征和自定义特征。
  - 保留 `FeatureLibrary` 与 `build_phase1_features` 等现有兼容入口。

## UI

- 无。

## Constraints

- 不修改交易逻辑、label 定义、回测规则。
- 不为了“优雅”引入大规模新依赖。
- 文档统一使用中文。
- 本地 `data/`、`config/settings.yaml` 只作为工作环境资产保留，不进入 commit。

## Done Criteria

- 产出以下正式文档：
  - `docs/research/feature_refactor_inventory.md`
  - `docs/architecture/feature_refactor_plan.md`
  - `docs/features/feature_registry_design.md`
  - `docs/features/README.md`
  - `docs/features/migration_notes.md`
  - `docs/research/feature_refactor_validation.md`
- 建立 `features/registry/feature_registry.yaml` 与 `features/registry/feature_sets.yaml`。
- feature 代码结构完成重组，保留兼容入口。
- 增加最小验证：registry 加载、feature set 解析、代表性 builder 构建、Qlib 内置与自定义特征混合集解析。
- 通过：
  - `python -m compileall qsys scripts tests`
  - `python -m unittest discover tests`

## Test Plan

- 编译检查：`python -m compileall qsys scripts tests`
- 单元测试：`python -m unittest discover tests`
- 最小脚本验证：`scripts/run_feature_build.py`

## Rollback Plan

- 通过单独 commit 回滚本次 feature refactor。
- 若回滚，只回退代码和文档；本地数据与配置环境继续保留在工作区。

## Notes

- 本次重点是系统化重构与统一注册，不追求一次性重写所有研究脚本。
- registry 面向“模型训练时可直接引用的正式特征”，不是简单的 raw field 清单。
