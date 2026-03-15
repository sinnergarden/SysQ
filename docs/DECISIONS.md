# DECISIONS

本文件用于汇总关键架构与工程决策记录。

定位说明：

- ADR 记录的是“长期有效的决策”。
- 功能细节记录在 `docs/features/`，不是 ADR。
- ADR 与功能文档关系见 [docs/README.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/README.md)。

当前决策列表：

1. [001-separate-signal-and-strategy.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/adr/001-separate-signal-and-strategy.md)
2. [002-use-tushare-free-first.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/adr/002-use-tushare-free-first.md)
3. [003-modular-monolith.md](file:///Users/liuming/Documents/trae_projects/SysQ/docs/adr/003-modular-monolith.md)

## 什么时候新增 ADR

满足以下任一条件时新增 ADR：

- 模块边界或依赖方向发生变化。
- 公共 API 设计原则发生变化。
- 交易执行基线、数据策略、存储策略发生长期变化。
- 一个决策预计影响多个后续功能。

新增决策时，请使用以下命名规则：

- `docs/adr/NNN-short-title.md`

并在本文件中补充索引。
