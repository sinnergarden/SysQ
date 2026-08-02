# Domain: Diagnostics

## Domain Scope
质量检查与系统健康：数据质量、信号 schema、产品 schema、lookahead 静态检查、harness 边界检查。
不包含：自动修复、每日运行监控（daily_ops domain）、生产级告警（Phase 4）。

## UC_DIAGNOSTICS

### Status
draft

### Source
`docs/USE_CASES.md` UC-12（Research Diagnostics & Candidate Explanation）的超集（新增 harness 边界检查维度）。

### User Goal
对系统运行状态做只读检查，发现数据质量、信号质量、ledger 状态、artifact schema 的异常。

### Scope
包含：
- 数据质量检查（coverage、空值、异常值）
- 信号 schema 检查
- label schema 检查
- 订单意图检查
- portfolio snapshot 检查
- lookahead 静态检查
- 框架稳定性检查
- harness 边界检查（use case registry、模型解析边界）

不包含：
- 自动修复问题
- 每日运行状态监控
- 生产级监控告警

### Inputs
- 各类运行产物（signal、label、order intents、portfolio snapshot）
- 文件系统路径

### Outputs
- 检查报告（控制台、结构化 JSON）

### Canonical Entrypoints
- `scripts/checks/` 下各 checker
- `harness/checks/` 下各 check

### Key Artifacts
- 无独立持久化 artifact（检查结果 stdout / JSON）

### Required Checks
- 自身就是 checks

### Owner Agent
reviewer_agent

### Allowed Paths
- `scripts/checks/`
- `harness/checks/`
- `qsys/data/health.py`
- `qsys/common/`
- `.claude/skills/`
- `AGENTS.md`
- `docs/requirements/`

### Forbidden Paths
- `qsys/ledger/`（只读 SQL 可以，改 schema 不行）
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`

### Open Questions
- 无
