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
- [new_feature.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/features/new_feature.md)
