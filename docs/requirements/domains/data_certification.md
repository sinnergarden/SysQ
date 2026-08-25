# Domain: Data Certification

## Domain Scope

对已经落盘的 source evidence 做只读认证，回答某个 source × field × instrument ×
date 交集是否具备可复核的 PIT 信任链。不负责采集、修复、研究、daily 调度或数据写入。

## UC_PIT_DATA_CERTIFICATION

### Status

draft

### Source

正式治理 use case；从 UC_DAILY_OPS 的 evidence producer 和 UC_DIAGNOSTICS 的只读检查
边界中拆出 certifier，避免“同步成功”被误当成“历史基线已认证”。

### User Goal

给定显式 source、字段、股票集合和日期区间，对已有 fetch receipts、canonical mutation、
Qlib readback 和 terminal watermark 做交集验证，产出不可变 certification artifact。

### Scope

包含：

- 只读解析 `data/audit/audit.db`、per-run immutable receipt 和后续 coverage exception Parquet；
- 按 source × field × instrument × date 求 evidence 交集；
- 区分 `published_at`、`observed_at`、`ingested_at`，未知发布时间保持 null；
- 拒绝 gap、缺 endpoint capability、缺 terminal receipt 回链或 legacy/untrusted evidence；
- 产出显式范围、exceptions、consumed evidence identity 的认证结果。

不包含：

- 调用 Tushare 或其他 endpoint；
- 调度 `scripts/data_sync.py`、daily、research、backfill 或 Qlib refresh；
- 修改 canonical、watermark、模型、信号、策略、回测、账户或 ledger；
- 把日期命名 legacy audit JSON、历史 snapshot/hash 或“进程 exit 0”升级为 trusted。

### Inputs

- 显式 source / dataset / fields / instruments / date range；
- `data/audit/audit.db` 中 append-only receipts、mutations、journal、trusted watermarks；
- `docs/requirements/contracts/tushare_daily.yaml` endpoint capability contract；
- 后续 PR 提供的 coverage/exceptions Parquet（本阶段未实现）。

### Outputs

- 后续 canonical certifier 生成的不可变 certification artifact；
- fail-closed exceptions（任何未覆盖交集均不得被 min/max watermark 吞并）。

### Canonical Entrypoints

- `scripts/research/certify_pit_baseline.py`（仅预留登记；本阶段不实现）。

该 entrypoint 必须只读，不得 import/call daily fetch、repair、research runner 或 production
runner。daily evidence 仍由 `UC_DAILY_OPS` 的既有 entrypoint 生产。

### Key Artifacts

- `data/audit/audit.db` — 最小 SQLite evidence SOT；
- `data/audit/source_runs/{run_id}/receipt.json` — per-run immutable export；
- `data/raw/evidence/tushare/{endpoint}/{run_id}/{receipt_id}.parquet` — supplier raw response；
- future certification artifact / coverage exception Parquet — 本阶段未实现，不能写成已交付。

### Required Checks

- `harness/checks/check_usecase_registry.py`
- `tests/test_source_audit.py`

### Execution Guidance For Future Audit Tasks

简单模型或执行小工接到 data audit/backfill/certification 任务时，先按写入性质路由：

- fetch、backfill、canonical/Qlib mutation 属于 `UC_DAILY_OPS`，只能从其 canonical
  entrypoint 运行；certifier 不得代为调度或修复；
- certification 只读消费既有 SQLite、immutable receipt、raw payload 与 contract，按
  source × endpoint × field × instrument × date 求交集；
- `published_at` 未知必须为 null，`observed_at` 与 `ingested_at` 不得替代它；
- required endpoint 的 empty/partial/failure、无法解释的 requested-symbol 缺口、null
  required field、canonical/Qlib mismatch 均 fail closed；
- same-key value revision 必须进入 canonical mutation 与 Qlib value readback，不能只看新增日；
- watermark 只能在 fetch/raw payload、canonical commit、Qlib readback、readiness 和
  contiguous-range gates 全部通过后由唯一 terminal owner 推进；legacy/untrusted 永不推进；
- backfill 若开始写 canonical 而缺少对应 source evidence，必须留下保守 mutation scope，
  本 run 不得把 target-day receipt 借给该历史范围，修复后用新 run 重审。

这些是可运行语义检查而非文档存在性检查：producer 改动运行
`tests/test_data_sync_csi1800.py` 与 `tests/ops/test_universe_history.py`，audit store/水位改动运行
`tests/test_source_audit.py`；最终仍运行 registry/agent-doc/script-entrypoint harness。

### Owner Agent

reviewer_agent

### Allowed Paths

- `scripts/research/certify_pit_baseline.py`
- `qsys/data/source_audit.py`
- `docs/requirements/`
- `docs/CONTRACTS.md`
- `tests/test_source_audit.py`

### Forbidden Paths

- `scripts/data_sync.py`, `scripts/run_daily.py`, `scripts/run_daily_batch.py`
- `qsys/ops/daily_runner.py`
- `qsys/model/`, `qsys/signal/`, `qsys/backtest/`
- `qsys/broker/`, `qsys/trader/`, `qsys/ledger/`

### Open Questions

- coverage/exceptions Parquet 与 certification artifact schema 在后续 certifier PR 定义；
- 在此之前，daily terminal watermark 只证明对应连续范围的 ingestion evidence 闭环，
  不等价于一份历史 PIT baseline certification。
