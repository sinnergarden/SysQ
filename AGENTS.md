# AGENTS

本文档是本仓库给 AI 助手的操作说明书。

## 仓库目标

- 在统一工程框架下完成“数据 -> 特征 -> 模型 -> 策略 -> 回测 -> 每日交易计划”的闭环。
- 保持研究和交易执行逻辑一致，减少策略落地偏差。
- 以小步稳定迭代为主，优先可验证与可回滚。

## AI 在本仓库扮演的角色

- 作为代码协作者，优先完成小范围、低风险、可验证的改动。
- 在改动前理解上下文、边界与依赖方向，不做“局部最优、全局破坏”。
- 对不确定区域先给出风险和替代方案，再实施最小改动。

## 人与 AI 推荐工作范式

角色分工：

- 人：定义目标优先级、验收标准、业务边界、是否允许高风险改动。
- AI：完成实现、回归验证、文档同步、可执行命令与变更说明。

协作节奏：

1. 人给出目标与边界。
2. AI 给出最小实施路径并直接落地改动。
3. AI 跑测试并回报风险与影响范围。
4. 人进行审阅并决定是否进入下一轮。

任务粒度建议：

- 单轮只解决一个主题。
- 每轮必须有可验证产物：代码、测试、文档或命令结果。
- 大改动拆分为多轮，避免一次性跨层重构。

## 执行优先级

1. 先保证可运行与可回归。
2. 再做结构优化与可维护性提升。
3. 最后做非必要的体验改进。

出现冲突时，优先级高的目标覆盖低优先级目标。

## 新功能实施模板

当需要做新功能时，按模板执行：

1. 先创建功能文档：`docs/features/new_feature.md`（可复制为具体文件名）。
2. 在文档里补齐 Goal、Use Cases、API Change、UI、Constraints、Done Criteria。
3. 定义功能目标与验收条件。
4. 定位模块落点（见 `ARCHITECTURE` 的放置对照表）。
5. 先改 `qsys/`，再接 `scripts/`，最后补 `tests/` 与文档。
6. 跑最小回归 + 全量回归。
7. 输出变更清单、风险清单、后续建议。

命名约定：

- 功能文档放在 `docs/features/`。
- 文件名建议：`yyyy-mm-dd_feature_name.md` 或 `feature_name.md`。

## 新功能文档是必填项

- 未创建功能文档，不进入实现阶段。
- 未满足 Done Criteria，不进入合并阶段。
- 若改动范围扩大，必须同步更新功能文档。

## ADR 与 Feature 协同规则

- 每个新需求先写 `docs/features/<feature>.md`。
- 若实现中发现触及长期架构决策，追加 `docs/adr/NNN-*.md`。
- 只有功能细节变化，不改长期规则时，不新增 ADR。
- 评审时先审 Feature 是否可交付，再审 ADR 是否有必要。

## 可以直接改的事情

- 文档修订与结构优化。
- 非公共 API 的缺陷修复。
- 测试补全、测试稳定性改进。
- 脚本参数和默认行为的兼容性增强。
- 不改变语义的重构与清理。

## 必须先讨论的事情

- 公共 API 变更。
- 数据库 schema 变更。
- 交易核心规则变更（手续费、最小交易单位、T+1 行为等）。
- 架构边界调整与跨层依赖重构。
- 删除核心模块与主入口脚本。

## 风险分级与动作边界

- 低风险：文档、日志、测试补齐、局部 bug 修复，可直接改。
- 中风险：跨模块重构、配置接口调整、脚本入口行为变更，先说明影响再改。
- 高风险：公共 API、持久化结构、交易规则、核心流程重写，必须先讨论。

## 当前不可随意变更的接口

- `qsys.live.account.RealAccount.sync_broker_state`
- `qsys.live.account.RealAccount.get_state`
- `qsys.live.account.RealAccount.get_latest_date`
- `qsys.live.manager.LiveManager.run_daily_plan`
- `qsys.live.simulation.ShadowSimulator.simulate_execution`
- `qsys.model.base.IModel.fit / predict / save / load`

若必须变更以上接口，必须同步更新调用方与测试。

## 代码风格要求

- Python 代码优先使用类型注解。
- 函数尽量短小，逻辑清晰，避免隐藏状态。
- 变量命名表达业务语义，避免单字母命名。
- 优先最小差异改动，不做无关大面积重排。

## 架构边界

- 分层、模块职责与依赖规则见 [ARCHITECTURE.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/ARCHITECTURE.md)。
- `qsys` 为核心业务层，`scripts` 为入口编排层，`tests` 为验证层。
- 入口脚本不承载复杂业务，复杂逻辑应下沉到 `qsys`。

## 禁止触碰区域

- 生产密钥与本地私密配置。
- 未经批准的数据 schema 与持久化结构。
- 未经讨论的公共接口删除或重命名。
- 未经讨论删除 `qsys/live`、`qsys/model`、`qsys/strategy` 下核心文件。

## 文件级约束

- `config/settings.yaml` 是本地私密配置，不提交仓库。
- `scripts/` 只放编排逻辑，复杂业务逻辑必须下沉到 `qsys/`。
- `tests/` 中的失败用例不得通过删测规避，必须修复或明确降级原因。

## 提交前必须执行

```bash
python -m compileall qsys scripts tests
python -m unittest discover tests
```

若只改局部，可追加最小回归测试。

最小回归对照表：

- 改动 `qsys/live`：`python -m unittest tests/test_live_trading.py`
- 改动 `qsys/data`：`python -m unittest tests/test_data_quality.py`
- 改动核心 API：`python -m unittest tests/test_core_api_contracts.py`

## 回答问题时的输出方式

- 先给结论，再给证据，再给可执行命令。
- 对代码引用使用明确文件路径与行号范围。
- 对风险和假设显式说明，不隐含前提。
- 对提交建议提供可直接复制的命令。
- 涉及删除或重命名文件时，先列影响范围再执行。

输出格式建议：

- 结论
- 改动点
- 验证结果
- 风险与回滚
- 下一步命令
