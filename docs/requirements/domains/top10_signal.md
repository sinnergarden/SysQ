# Domain: S180 Top10 Signal Operations

## Domain Scope

把严格 PIT CSI1800、单一 180 日模型的训练与每日原始分数推理收束为一个研究候选生产流程。该 domain 只编排既有模型训练与 artifact-only inference 能力，不包含财报、回测、promotion、shadow/prod、交易、broker 或 ledger。

## UC_TOP10_SIGNAL_RUN

### Status

stable

### Source

由操作者批准，将重复执行的最新 S180 raw Top10 从临时研究流程升级为正式 Use Case。

### User Goal

操作者每天只运行一个命令。系统自动选择最近完成交易日、加载对应日期的 PIT CSI1800 快照、判断距离最近一次合规模型训练是否达到 20 个交易日；到期才刷新成熟 180 日标签并训练，否则直接复用显式、内容寻址的模型 bundle。最终输出未经横截面 z-score 的 Top10，并以一个质量门报告 PASS/BLOCKED。

### Scope

包含：

- 读取严格 PIT CSI1800 历史成分和按日期发布的当前成分快照；
- 每 20 个交易日或显式 `--force-retrain` 刷新成熟标签并训练单个 S180 LightGBM；
- 每日使用显式 bundle hash 推理，按 `raw_prediction` 排序生成 Top10；
- 输入 fingerprint 相同则复用，不覆盖 immutable artifact；
- run lock、阶段状态、模型 registry、内容 hash、统一质量门和原子 JSON 发布；
- 失败后保留上一有效模型，下一次从已发布阶段继续。

不包含：

- 财报下载、基本面判断或 Hermes 文件；
- 新实验、回测、信号改造、z-score 排序或组合构建；
- candidate promotion、shadow/prod pointer、下单、持仓、broker、trader、ledger；
- scheduler/systemd 部署。

### Inputs

- `configs/strategies/s180_top10.yaml`；
- canonical/qlib 最新完成行情与 `v3a_plus_liquidity_financial_rc` 特征；
- `data/research/universes/csi1800_pit_v2/` 历史成分；
- `data/research/universes/csi1800_pit_daily/{decision_date}/membership.parquet`；
- `configs/labels/fwd_ret_180d_raw_pit_csi1800.yaml`；
- 内容寻址模型 bundle registry。

### Outputs

- `data/research/models/bundles/s180_top10/{bundle_hash}.json`；
- `data/research/top10/s180_top10/model_registry.json`；
- `runs/top10/s180_top10/{decision_date}/state.json`；
- `outputs/{signal_date}/s180_top10/{run_id}/candidate_run.json`；
- 同目录 `top10_run.json`。

所有路径按显式日期/hash 解析；禁止 `latest`、mtime 或 symlink。

### Canonical Entrypoints

```bash
python scripts/run_daily.py \
  --strategy s180_top10 \
  --mode top10 \
  --signal-date auto
```

`--force-retrain --reason <原因>` 只覆盖 20 交易日调度判断，不绕过标签成熟、PIT、特征、模型或 artifact 质量门。底层 `--mode train` / `--mode infer` 保留用于诊断，不是日常操作入口。

### Key Artifacts

- `model_registry.json` 是按 `as_of_date` 排序的显式 immutable bundle 索引，不是 latest pointer；每项都绑定 bundle 文件 SHA-256。同一日期的数据修复重训以单调递增 `revision` 保留旧 bundle，推理明确选择最大 revision。
- `candidate_run.json` 保存逐股票 raw prediction、模型/特征/universe lineage 和排除原因。
- `top10_run.json` 是终态清单；只有所有内置质量门通过后才原子写入 `status=complete`。
- `state.json` 只用于阶段恢复，不能代替终态 artifact 验证。

### Required Checks

- `harness/checks/check_usecase_registry.py`；
- `harness/checks/check_label_maturity.py`；
- `harness/checks/check_inference_artifact.py`；
- `harness/checks/check_top10_signal_artifact.py`。

### Owner Agent

operator_agent

### Allowed Paths

- `scripts/run_daily.py`
- `qsys/signal/top10_run.py`
- `qsys/signal/model_blend_inference.py`
- `qsys/model/financial_rc_trainer.py`
- `qsys/model/registry.py`
- `configs/strategies/s180_top10.yaml`
- `configs/labels/fwd_ret_180d_raw_pit_csi1800.yaml`
- `harness/checks/check_top10_signal_artifact.py`
- `docs/requirements/`
- `.claude/skills/sysq-daily/SKILL.md`
- `tests/`

### Forbidden Paths

- `qsys/broker/`
- `qsys/trader/`
- `qsys/ledger/`
- `qsys/backtest/`
- `qsys/ops/daily_runner.py`
- `deploy/`
- `research_memos/`
- 任何财报或 Hermes 路径

### Open Questions

- scheduler/systemd 如何触发本 UC 属于后续 deploy 变更，不在本 PR。
- 本 UC 只发布研究候选；若未来进入 shadow，必须经过独立 promotion UC 和回测证据。

### Lookahead / Leakage Contract

- `decision_date` 是运行锚点时最近完成的开市日；配置的全截面 lag 为 1，因此 `data_date=signal_date` 是其前一开市日。
- 180 日 forward return 只在训练时读取；训练窗口最后样本必须满足 181 个交易日成熟约束。
- 历史训练每一行按该行 `trade_date` 过滤 PIT membership；当前推理只使用显式 `decision_date` 日快照。
- inference 路径不读取 LabelStore，训练路径不会把 validation/未来标签带入推理 artifact。
