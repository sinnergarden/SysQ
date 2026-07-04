# QSys 投研全链路 Use Cases — DEPRECATED

> ⚠️ **This document is deprecated.**
>
> Canonical use case registry now lives in:
> - [`docs/requirements/01_usecase_index.md`](requirements/01_usecase_index.md) — index of all active use cases
> - [`docs/requirements/harness_map.yaml`](requirements/harness_map.yaml) — machine-readable mapping
> - [`docs/requirements/domains/`](requirements/domains/) — detailed use case definitions by domain
>
> Historical content has been superseded by docs/requirements/.
See git history before PR #212 for the old full document.
> Do not add new use cases here.

---

## 1. 文档目标

本文档定义 QSys 投研平台从数据、特征、标签、信号研究、信号分析、策略回测、候选晋级到日常运行的核心 Use Case。

QSys 的目标是形成一套可重复、可追踪、可组合的投研工作流：

```text
Data → Feature → Label → Signal Research → Signal Analytics → Backtest → Candidate Promotion → Shadow (UC-8) → Production (UC-9)
```

每个 Use Case 应满足以下原则：

- 通过标准配置描述任务。
- 通过统一 CLI 或核心 Pipeline 执行。
- 产物通过标准 Store、Manifest 和稳定 ID 追踪。
- 上下游通过引用对象连接，而不是通过手工拼路径连接。
- 业务逻辑沉入 `qsys/` 模块，`scripts/` 仅负责命令行调度。
- 标准配置与 Manifest 应有 schema 校验；未知字段应默认拒绝。
- 核心 Pipeline 应具备幂等语义：相同配置和相同输入数据应产生可追踪的稳定产物，或在未显式 overwrite 时安全拒绝覆盖。

---

*[historical content continues below — preserved for reference]*
