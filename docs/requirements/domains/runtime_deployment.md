# Domain: Runtime Deployment

面向已验证代码 revision 的 detached runtime 与 systemd user timer 部署治理。
本 domain 不是数据同步、推理或交易业务 domain；它只定义部署边界、人工确认和
可审计验证契约。

## UC_DAILY_RUNTIME_DEPLOYMENT

### Status
draft

### Source
本治理前置 PR；canonical implementation 预留为
`scripts/deploy_csi1800_pit_runtime.py`。

### User Goal
操作员可以把一个明确、可复核的 commit revision materialize 成干净的 detached
runtime，并安装/验证对应的 systemd user timer，而不会误触发数据同步 service、推理、
模型、策略或交易链路。

### Scope
包含：

- 验证显式 40-hex commit revision，并生成不依赖开发 worktree 的 detached runtime；
- 验证 detached runtime 的 commit revision、解释器、unit 内容、timer 与 service 的绑定关系；
- 在人工确认后安装 unit 文件、daemon-reload，并 enable timer；
- 只读验证 revision、runtime 路径、已安装 unit bytes 和 preflight/stdout 结果。

不包含：

- 数据同步业务、`qsys/data/collector.py` 调用或任何 data-sync service 启动；
- 推理、broker、trader、ledger、signal、model、feature、backtest 或 promotion；
- 通过 `latest`、mtime、软链接推断 revision、artifact 或 model；
- 修改实际业务代码或替代 daily canonical sync/inference 入口。

### Inputs

- `--revision <40-hex commit>`：必填，且必须在部署前可由 git 独立验证；
- 已验证的 runtime 目标路径、固定解释器路径和 unit/timer 模板；
- `--confirm-deploy <operator-ack>`：必填的人工确认参数（不得由默认值、环境变量
  或已有 service 状态推断）；
- 可选的只读检查目标；`apply` 不得接受隐式“当前/latest” revision。

### Outputs

- 可独立验证的 materialized detached runtime checkout 及其显式 commit revision；
- 安装到 user unit 目录的 `qsys-csi1800-pit-daily-sync.service` 与 `.timer` bytes
  （仅 timer 被 enable）；
- 命令 stdout 与 preflight verification result，记录本次确认、revision、路径和
  检查结果。

### Canonical Entrypoints

`scripts/deploy_csi1800_pit_runtime.py`。

该入口必须区分 read-only verify 与 `--apply`；`--apply` 必须同时要求显式 revision
和 `--confirm-deploy` operator confirmation，缺少任一参数必须 fail closed。当前基线尚未提供实现，本 UC 先锁定其边界，不把已有
`scripts/data_sync.py` 或 `scripts/ops/sync_csi800_daily.py` 变成部署入口。

### Key Artifacts

- detached runtime checkout（含 explicit commit revision，禁止 `latest`/mtime）；
- 已安装的 `qsys-csi1800-pit-daily-sync.service` 与 `.timer` 文件 bytes，以及
  timer/service 的静态绑定证据；
- 命令 stdout 与 preflight verification result。

### Required Checks

- `harness/checks/check_usecase_registry.py`；
- `harness/checks/check_agent_docs.py`；
- `tests/test_deploy_systemd.py`，且不得通过启动 service 来验证；
- 任何 apply 前后都必须独立验证 timer 已 enable、目标 service 未被 apply 直接启动，
  且 unit 未指向开发 checkout 或可变 revision。

### Owner Agent

operator_agent

### Allowed Paths

- `scripts/deploy_csi1800_pit_runtime.py`；
- `deploy/`；
- `tests/test_deploy_systemd.py`；
- `AGENTS.md`；
- `docs/requirements/`；
- `.claude/skills/sysq-dev/SKILL.md`。

### Forbidden Paths

- `scripts/data_sync.py`、`scripts/ops/sync_csi800_daily.py`、`scripts/run_daily.py`、
  `scripts/run_daily_batch.py`；
- `qsys/data/`、`qsys/signal/`、`qsys/model/`、`qsys/feature/`、`qsys/backtest/`；
- `qsys/broker/`、`qsys/trader/`、`qsys/ledger/`、`qsys/ops/`；
- `data/`、`outputs/` 及任何 strategy/model/signal artifact；
- 任何未经人工确认的 systemd service 启动或 enable。

### Open Questions

- 生产 host 的 runtime 根目录、unit/timer 名称和 operator confirmation 的具体
  审计格式仍需在实现 PR 中固定；
- 解释器路径的 symlink 验证命令需在实现 PR 中固定，但 symlink 只可用于已验证的
  固定解释器路径，不可用于 revision、artifact 或 model 解析。
- P1：后续实现 PR 再决定是否增加持久化 deployment manifest/audit；它们不是本 UC
  当前实现的必备输出，不能替代 stdout/preflight 验证。
