# Contracts

本文档目录定义 SysQ 的模块间**数据接口契约和 read model**。

## 定位

- `ARCHITECTURE.md` 讲系统地图（两条主链路、分层、过渡态）。
- **Contracts** 讲模块间稳定数据边界（谁生产、谁消费、粒度、不变量）。
- `docs/schema/` 是 artifact 字段级 schema（SignalArtifact、OrderIntentArtifact 等）。
- `docs/features/` 是功能规格和历史设计，不自动等同于 current truth。

Contracts 的目标是让后续代码、UI、监控和研究分析共享同一套读取口径，避免每个模块各读各的文件。

## 服务对象

Contracts 服务于以下所有场景：

- Research / Backtest
- Daily Ops
- Monitoring / Observability
- UI（只读）
- Research Analytics
- Candidate / Shadow / Production 晋级

## 本批 contract

| 文件 | 定位 |
|------|------|
| `data-readiness-contract.md` | 数据是否可用于 train / backtest / preopen 的判断口径 |
| `signal-contract.md` | signal / prediction 在研究、回测、preopen、UI 之间的基本口径 |
| `research-analytics-contract.md` | Research Analytics 层的边界：IC/RankIC、实验索引、横向比较 |
| `daily-ops-read-model-contract.md` | UI / monitoring 只读 daily ops 状态的 read model |
| `portfolio-state-contract.md` | 账户、持仓、成交、组合状态的核心语义 |

## 使用规则

1. **Contracts 是长期接口契约**，不是阶段性 feature spec。修改必须经过 ADR 或至少 PR Review。
2. 所有 contract 文档**不把目标态写成已完成事实**。当前尚未完成的迁移路径必须在文档中标注。
3. 对 legacy path 只标 compatibility，不扩展新依赖。
4. UI / monitoring 在所有 contract 中保持只读——不写 ledger，不下单，不改策略。
5. 字段只写第一版最小稳定字段，不是全量字段大全。后续逐步演进。
