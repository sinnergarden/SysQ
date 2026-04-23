# FEATURES

本目录用于存放“功能级”文档。

与 ADR 的关系：

- 本目录记录“功能实现细节”。
- ADR 记录“长期架构与工程决策”。
- 功能改动若触发长期规则变化，需同步补 ADR。

## 使用规则

- 每个新功能先建文档，再开始实现。
- 文档文件路径：`docs/features/<feature_name>.md`。
- 文档模板：`docs/features/new_feature.md`。
- 功能细节写在这里，`ARCHITECTURE.md` 只保留系统大结构。
- Feature 文档应尽量短小、可执行，避免写成架构总论。

## 当前文件

- `feature_system.md`：Qsys 特征系统长期说明，定义 raw / feature engineering / bin / model 的工程边界与推进方式
- `factor_governance_and_research_migration.md`：因子对象、variant lineage、bundle、experiment 与工程化迁移的大需求文档
- `factor_governance_pr_plan.md`：把因子治理大需求拆成 phase、实现边界、测试与后续 PR 计划
- [new_feature.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/features/new_feature.md)
- `daily_signal_monitoring.md`：daily ops 推荐篮子质量监控需求，覆盖 1d/2d/3d vintage 跟踪与盘后信号质量摘要。
- `qsys_workflow_layer.md`：workflow / plugin 抽象层的首版设计与文档映射。
- `qsys_workflow_adapter_plan.md`：首批 commands 的 adapter 设计与 PR 拆分建议。
- `miniqmt_bridge_and_production_ops.md`：研究+生产双定位、WSL 生产运行形态与 MiniQMT 桥接方向。
- `mainline_rolling_and_readiness.md`：三条主线对象的固定 rolling 研究入口、artifact contract、UI 识别约定，以及最小 readiness / coverage / degradation 闭环。
- `mainline_strategy_tuning_exclusion_round.md`：`feature_173` vs `feature_254_trimmed` 的策略层排除实验收口 notes，记录最优接法、相对强弱与最终业务判断。

## 当前进度备注

- daily ops 已补充 `signal basket`、`signal quality` 和 `daily ops manifest` 三类结构化产物，后续 MiniQMT 接入应继续复用这套输出骨架。
- workflow layer 已完成文档与 skeleton 起草，后续应沿 `preopen-plan` adapter -> `order_intents` -> `miniqmt broker adapter` 主线推进。
- 因子治理主线已新增 `factor_governance_and_research_migration.md`，后续涉及 factor / variant / bundle / experiment 的长期改造，默认先以该文档为需求基线。
