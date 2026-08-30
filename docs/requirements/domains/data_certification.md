# Domain: Data Certification

## Domain Scope

对已经落盘的 source evidence 做只读认证，回答某个 source × field × instrument ×
date 交集是否具备可复核的 PIT 信任链。不负责采集、修复、研究、daily 调度或数据写入。

## UC_PIT_DATA_CERTIFICATION

### Status

draft

entrypoint 已实现；首个真实 `CERTIFIED` baseline 前不升级 stable。

### Source

正式治理 use case；从 UC_DAILY_OPS 的 evidence producer 和 UC_DIAGNOSTICS 的只读检查
边界中拆出 certifier，避免“同步成功”被误当成“历史基线已认证”。

### User Goal

给定显式 source、字段、股票集合和日期区间，对已有 fetch receipts、canonical mutation、
Qlib readback 和 terminal watermark 做交集验证，产出不可变 certification artifact。

### Scope

包含：

- 只读解析 `data/audit/audit.db`、per-run immutable receipt 和后续 coverage exception Parquet；
- 使用 bounded、gap-only supporting audit 离线检查既有 historical raw/Qlib 覆盖；
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
- 显式 baseline request、96-feature dependency assertion、coverage evidence run IDs；
- 可选 mutation run IDs 只做存在性断言，实际交集始终覆盖完整 mutation ledger。
- mutation 只有在每个相交 dependency scope 的 selected-evidence coverage、字段 watermark 日期范围
  和 aware UTC `updated_at >= ingested_at` 都成立时才为 `ACCOUNTED`；UNKNOWN 或任一字段未满足均重审。
- terminal coverage 必须回链 receipt 中精确 normalized field link；声明 source manifest 时三处
  backlink 均须非空一致。formal income/shareholder dependency 还必须由 baseline request、research
  generator config 与 signal `feature_source_lineage` 三方绑定同一 immutable artifact/manifest。
  income identity 必须包含 artifact id、source run/terminal SHA、scope/cutoff、完整历史起点与
  availability/transform contract；shareholder manifest 必须反向绑定 holder/top10 两文件，且
  source run/terminal SHA 必须回链 selected trusted watermark。旧 snapshot v1（仅
  `source_state`/`bootstrap_audit`）不是 source evidence，不能认证。

### Outputs

- `<output-root>/<baseline_id>/<audit_id>/audit_scope.json`；
- 同目录的 `coverage.parquet`、`exceptions.parquet`、`evidence_snapshot.json`、`audit_receipt.json`；
- `CERTIFIED`、`BLOCKED` 或 `REAUDIT_REQUIRED`，并保留可定位的 fail-closed exceptions。
- 仅从 `CERTIFIED` receipt 导出的 portable DataPack 目录；它包含审计证明、所选 raw payload、
  被消费股票的 canonical 文件、PIT universe、corporate actions、income artifact+manifest 与
  shareholder holder/top10+manifest；sidecar 固定落在 `data/sidecars/{income,shareholder}/`，
  固定不含 Qlib。

### Canonical Entrypoints

- `scripts/research/certify_pit_baseline.py`（已实现，只读）。
- 同一入口的 `--export-datapack-from` / `--verify-datapack` 模式。

只读 supporting tool：

- `scripts/ops/audit_raw_to_qlib_coverage.py`。historical mode 只消费本地 raw/Qlib
  与显式 `--suspension-evidence` trusted SourceAudit terminal receipt；不得调用 Tushare。
  receipt 的 hash/run/scope/range 必须回链 `audit.db` trusted watermark，并以
  success/empty 单-symbol shards 完整覆盖精确审计区间；success payload 的 hash、symbol
  与事件范围须离线复核。该工具只保留
  actionable gap details，OK 单元格仅累计 counters，并以 `--max-gap-details` 硬上限
  fail closed。该工具不替代 canonical certifier，也不得修改被审计数据。

该 entrypoint 必须只读，不得 import/call daily fetch、repair、research runner 或 production
runner。daily evidence 仍由 `UC_DAILY_OPS` 的既有 entrypoint 生产。`scripts/data_sync.py`
另有显式、与 normal sync 互斥的 shareholder history supporting mode 与 offline snapshot
bootstrap mode；前者从精确 SHA 锚定的 trusted base terminal 生产 shareholder evidence，后者
只从指定 trusted terminal 的 verified raw payload 重建 immutable v2 snapshot。两者都不由
certifier 调度。

### Key Artifacts

- `data/audit/audit.db` — 最小 SQLite evidence SOT；
- `data/audit/source_runs/{run_id}/receipt.json` — per-run immutable export；
- `data/raw/evidence/tushare/{endpoint}/{run_id}/{receipt_id}.parquet` — supplier raw response；
- `<output-root>/<baseline_id>/<audit_id>/{audit_scope.json,coverage.parquet,exceptions.parquet,evidence_snapshot.json,audit_receipt.json}`；
- `<explicit-datapack-output>/{manifest.json,checksums.sha256,audit/,contracts/,data/,lineage/}`。

### Required Checks

- `harness/checks/check_usecase_registry.py`
- `harness/checks/check_scripts_entrypoints.py`
- `tests/test_source_audit.py`
- `tests/test_pit_baseline_certification.py`
- `tests/test_raw_to_qlib_coverage.py`
- `tests/ops/test_shareholder_sync.py`
- `tests/research/test_lightgbm_window_cache_identity.py`
- `tests/test_data_sync_csi1800.py`
- `tests/test_financial_replay.py`

### Execution Guidance For Future Audit Tasks

简单模型或执行小工接到 data audit/backfill/certification 任务时，先按写入性质路由：

- fetch、backfill、canonical/Qlib mutation 属于 `UC_DAILY_OPS`，只能从其 canonical
  entrypoint 运行；certifier 不得代为调度或修复；
- certification 只读消费既有 SQLite、immutable receipt、raw payload 与 contract，按
  source × endpoint × field × instrument × date 求交集；
- `published_at` 未知必须为 null，`observed_at` 与 `ingested_at` 不得替代它；
- required endpoint 的 empty/partial/failure、无法解释的 requested-symbol 缺口、null
  required field、canonical/Qlib mismatch 均 fail closed；
- 财务认证的目标语义是 `financial_latest_known_actual_publication_v1`：首次披露公开后使用初值，
  修订披露公开后才切换修订值。`financial_first_available_v1` 可以作为保守研究输入，但不能据此
  声称完整 latest-known PIT；若修订有效日无法由独立 publication evidence 证明，或当前派生层
  尚未按修订事件投影，必须产生 `FINANCIAL_LATEST_KNOWN_REVISION_CAPABILITY_UNVERIFIED` blocker；
- same-key value revision 必须进入 canonical mutation 与 Qlib value readback，不能只看新增日；
- watermark 只能在 fetch/raw payload、canonical commit、Qlib readback、readiness 和
  contiguous-range gates 全部通过后由唯一 terminal owner 推进；legacy/untrusted 永不推进；
- backfill 若开始写 canonical 而缺少对应 source evidence，必须留下保守 mutation scope，
  本 run 不得把 target-day receipt 借给该历史范围，修复后用新 run 重审。
- history scope checkpoint 只用于中断恢复，不是 certification 结果。新 checkpoint 将完成时
  scope 的公共物理列、日期行集合和值按显式截止日生成稳定语义 digest；截止日后的 daily append
  不参与比较，截止日内任一行/值变化只重放相交 scope。旧 whole-file checkpoint 若文件字节
  已变化，必须做一次本地 scope 重放，但继续复用其 verified raw shards，不重新拉取。
- 不把 feature 代码、审计代码或 Git commit 写入数据 digest。新增 feature 若只消费既有字段，
  不失效 source/canonical checkpoint；新增 source 字段由新的 processing contract 和后续
  checkpoint 自然覆盖。最终 DataPack hash 仍只绑定冻结的 certified artifact。

这些是可运行语义检查而非文档存在性检查：producer 改动运行
`tests/test_data_sync_csi1800.py` 与 `tests/ops/test_universe_history.py`，audit store/水位改动运行
`tests/test_source_audit.py`；最终仍运行 registry/agent-doc/script-entrypoint harness。

### Owner Agent

reviewer_agent

### Allowed Paths

- `scripts/research/certify_pit_baseline.py`
- `scripts/ops/audit_raw_to_qlib_coverage.py`
- `qsys/pit_certification.py`
- `qsys/pit_datapack.py`
- `qsys/ops/data_coverage.py`
- `qsys/ops/shareholder_sync.py`（仅 terminal-backed offline snapshot materializer）
- `qsys/data/_merge_helpers.py`（仅 financial source-event materializer）
- `qsys/data/income_sidecar.py`（仅 immutable income event sidecar）
- `qsys/data/collector.py`（仅 raw financial response projection）
- `qsys/data/_fetch_strategies.py`（仅保留 supplier terminal failure detail）
- `qsys/data/source_audit.py`（仅在 frozen raw reuse 中保留原始 observed_at）
- `qsys/ops/financial_replay.py`（仅 frozen-raw offline replay）
- `qsys/research/generators/lightgbm_single_label.py`
- `qsys/research/matrix_job.py`
- `scripts/data_sync.py`（仅显式 shareholder/announcement evidence supporting mode 与
  offline snapshot bootstrap mode）
- `configs/audit/csi1800_s180_baseline_v1_r1.yaml`
- `configs/audit/csi1800_s180_r3_source_revision_v1.yaml`
- `configs/audit/feature_dependencies/v3a_plus_liquidity_financial_rc_v1.yaml`
- `docs/requirements/`
- `docs/CONTRACTS.md`
- `docs/ops/DAILY_OPS_SOP.md`
- `tests/test_pit_baseline_certification.py`
- `tests/test_raw_to_qlib_coverage.py`
- `tests/ops/test_shareholder_sync.py`
- `tests/research/test_lightgbm_window_cache_identity.py`
- `tests/test_data_sync_csi1800.py`
- `tests/test_financial_replay.py`
- `tests/test_financial_pit.py`
- `tests/test_income_sidecar.py`
- `tests/test_source_revision_certification.py`
- `.claude/skills/sysq-dev/SKILL.md`

### Forbidden Paths

- `scripts/run_daily.py`, `scripts/run_daily_batch.py`
- `qsys/ops/daily_runner.py`
- `qsys/model/`, `qsys/signal/`, `qsys/backtest/`
- `qsys/broker/`, `qsys/trader/`, `qsys/ledger/`

### Operator Command

```bash
python scripts/research/certify_pit_baseline.py \
  --request configs/audit/csi1800_s180_baseline_v1_r1.yaml \
  --audit-db data/audit/audit.db \
  --evidence-run-id <explicit-run-id> \
  --mutation-run-id <explicit-run-id> \
  --output-root data/research/pit_certifications
```

不得猜测 latest/mtime。零 evidence run 仍生成完整 `BLOCKED` 报告并退出 2；输入错误退出 1。
同一 deterministic audit 目录禁止覆盖；所有文件先在 baseline root 唯一 staging 中完成并校验，
再在 flock 下原子发布。watermark 必须能回链 trusted terminal receipt、精确六 gates、raw supplier
payload 以及与 consumed instruments/date 完全一致的 requested scope，否则 coverage 为 missing。

DataPack 导出必须显式给出 certification 目录与全新 output 目录，只接受
`baseline_status=CERTIFIED`。它逐项复核 certification、lineage、raw payload、canonical、universe
和 corporate-action hash 后原子发布。它是可复制目录，不是每日 snapshot；复制或归档后先运行
`--verify-datapack`。Qlib 始终由 canonical 重建，不进入默认包。

相交的 income/shareholder sidecar mutation 只有在 accounting watermark 的 `run_id` 与
`terminal_receipt_sha256` 精确等于该 sidecar manifest source identity 时才可记为
`ACCOUNTED`；后来其他 run 的水位即使覆盖日期也不能替旧 sidecar 抵消 mutation。无交集 mutation
仍保持 `DISJOINT`，缺失/非规范 sidecar dataset 或 field/instrument identity 保持 UNKNOWN 并重审。
