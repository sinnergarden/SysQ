# UC_TEMPORARY_REQUESTS: Temporary Requests

## Status
experimental

## Source
**新增 use case**，不在 `docs/USE_CASES.md` 现有 UC 编号中。
设计留 TODO，后续逐步明确。

## User Goal
处理临时的、非 canonical 的需求：新增指标、临时图表、一次性实验、小范围 UI 面板改动。这些请求不应自动成为正式 use case，但需要可追踪。

## Scope
包含：
- 新增 metrics 或分析
- 临时图表或数据导出
- 实验性对比
- 小范围 UI 调整
- 一次性数据调查

不包含：
- 长期功能（必须有对应的 use case 文档）
- 架构/核心逻辑变更
- 生产路径行为变更

## Inputs
取决于具体请求

## Outputs
取决于具体请求

## Canonical Entrypoints
无固定入口。临时请求通常在 `scripts/dev/` 下实现，或在现有入口上加 `--experimental-*` 参数。

## Key Artifacts
- 临时产物应放在 `scratch/` 或 `outputs/`，不占用 canonical artifact 路径

## Required Checks
- TBD: 临时请求完成后应有清理步骤（产物是否归档/删除）

## Owner Agent
main_agent

## Allowed Paths
取决于具体请求，但默认限于：
- `scripts/dev/`
- `scratch/`
- `notebooks/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`
- `qsys/ops/daily_runner.py`
- `deploy/`

## Open Questions
- （已定）所有命令必须对应一个 use case。agent 发现用户请求不在任何 use case 中时，必须先与用户确认是否为临时请求。若是，注册为 UC_TEMPORARY_REQUESTS。同一临时请求执行超过 2 次，必须补文档并考虑收束为正式 use case。
