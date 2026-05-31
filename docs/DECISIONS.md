# DECISIONS

本文件用于汇总关键架构与工程决策记录。

定位说明：

- ADR 记录的是“长期有效的决策”。
- 功能细节记录在 `archive/docs/features/`（历史设计），不是 ADR。
- ADR 与功能文档关系见 [archive/docs/README.md](../archive/docs/README.md)。

当前决策列表：

1. [001-separate-signal-and-strategy.md](adr/001-separate-signal-and-strategy.md)
2. [002-use-tushare-free-first.md](adr/002-use-tushare-free-first.md)
3. [003-modular-monolith.md](adr/003-modular-monolith.md)
4. [004-factor-objects-over-feature-sets.md](adr/004-factor-objects-over-feature-sets.md)
5. [005-protected-core-boundary.md](adr/005-protected-core-boundary.md) — Protected Core 定义与修改规则 (2026-05-23)
6. [006-strategy-lifecycle.md](adr/006-strategy-lifecycle.md) — 策略 Research→Candidate→Shadow→Production 生命周期 (2026-05-23)
7. [007-artifact-contract.md](adr/007-artifact-contract.md) — 统一信号/订单/执行/快照产物契约 (2026-05-23)

## 什么时候新增 ADR

满足以下任一条件时新增 ADR：

- 模块边界或依赖方向发生变化。
- 公共 API 设计原则发生变化。
- 交易执行基线、数据策略、存储策略发生长期变化。
- 一个决策预计影响多个后续功能。

新增决策时，请使用以下命名规则：

- `docs/adr/NNN-short-title.md`

并在本文件中补充索引。
