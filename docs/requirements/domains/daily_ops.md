# Domain: Daily Operations

## Domain Scope
每日生产运行链路：数据同步、盘前推理、盘后执行、MTM、ledger 写入、通知。
不包含：模型训练（model_training domain）、研究回测（research domain）、promotion 流程（promotion domain）。

## UC_DAILY_OPS

### Status
stable

### Source
`docs/USE_CASES.md` UC-1（Data Sync）、UC-8（Shadow Trading）、UC-9（Production Trading，远期）。

### User Goal
每日自动执行数据同步 → 信号推理 → 交易计划 → 盘后对账的全链路。operator 可查看每日状态和产物，异常时有阻断和通知。

### Scope
包含：
- 数据同步（csi800 daily）
- canonical CSI1800 单日采集实现（`qsys/data/collector.py`）；该实现只允许由
  `scripts/data_sync.py` 或 `scripts/ops/sync_csi800_daily.py` 调用，不将整个
  `qsys/data/` 目录纳入 daily allowed paths。
- 从一个显式 trusted source run 离线 bootstrap immutable income PIT sidecar；
  该模式复核 terminal receipt、watermark backlink、field links 与 raw payload SHA，
  不调用供应商，也不会被 normal daily 隐式触发。
- 盘前推理（signal → prediction → plan）
- 盘后执行（simulated fills → ledger → MTM）
- 通知（Telegram）
- manifest 产物写入

不包含：
- 实盘 broker 下单（见 promotion domain）
- 模型训练（见 model_training domain）
- 研究回测（见 research domain）

### Inputs
- 行情数据（canonical daily / qlib_bin）
- 策略配置
- 模型 pointer（仅 `artifacts/registry/models/{strategy_id}/shadow.json`）
- promotion pointer（`data/research/promotions/shadow.yaml`，必须绑定 strategy/config/model hash）

### Outputs
- `data/canonical/daily/` — 规范行情数据
- `daily/{trade_date}/pre_open/signals/signal_basket_*.csv`
- `daily/{trade_date}/post_close/reconciliation_result.json`
- `runs/{trade_date}/{run_id}/` 各阶段产物
- Ledger 写入（`data/trade.db`）
- Telegram 通知

### Canonical Entrypoints
- `scripts/data_sync.py` — 数据同步
- `scripts/run_daily.py --mode preopen|postclose` — 盘前/盘后
- `scripts/run_daily_batch.py` — 批量 wrapper，只调度 shadow pointer 真正指向的策略
- `qsys/data/collector.py` — 仅作为上述 canonical sync entrypoint 的采集实现；
  不作为独立运营入口，也不改变 deploy/systemd 边界。
- `scripts/data_sync.py --build-income-sidecar-from-run-id ... --apply` — 仅供
  certification/bootstrap 明确调用的离线 supporting mode；不是新入口，normal daily
  不构建全量 sidecar。

对齐 `docs/USE_CASES.md` §7。

### Key Artifacts
- `data/trade.db` — ledger SOT
- `daily/{date}/pre_open/signals/` — signal basket
- `daily/{date}/post_close/` — reconciliation, MTM
- `runs/{date}/{run_id}/` — 完整运行产物

### Required Checks
- `harness/checks/check_model_resolution_boundary.py`
- `harness/checks/check_no_latest_model_resolution.py`
- `tests/test_source_audit.py`：source/field scope、PIT timestamps、mutation hash、连续水位
- `tests/test_data_sync_csi1800.py`：required endpoint/scope、Qlib readback、terminal ownership
- `tests/test_daily_incremental_fastpath.py`：历史请求 exact-shard identity、payload 校验与 resume
- `tests/ops/test_universe_history.py`：canonical history mutation 的保守 scope 与 crash recovery
- `validate_daily_stage_manifest()`：独立复核日期、策略、stage status；子进程 exit 0 不能代替 manifest
- preopen 内部任一阶段失败必须非零退出，且成功后才写 active attempt

### Owner Agent
operator_agent

### Allowed Paths
- `scripts/data_sync.py`
- `scripts/run_daily.py`
- `scripts/run_daily_batch.py`
- `scripts/ops/sync_csi800_daily.py`
- `qsys/data/collector.py`
- `qsys/data/_merge_helpers.py`
- `qsys/data/adapter.py`
- `qsys/data/income_sidecar.py`
- `qsys/data/storage.py`
- `qsys/data/source_audit.py`
- `qsys/ops/`
- `configs/strategies/`
- `harness/checks/`
- `docs/ops/DAILY_OPS_SOP.md`
- `tests/`

### Forbidden Paths
- `qsys/ledger/`（只读分析，不直接改 schema）
- `qsys/backtest/`
- `qsys/trader/`
- `qsys/broker/`


## UC_DAILY_INFERENCE_RUN

### Status
stable

### Source
新增 use case，响应临时推理/手动 trigger prediction 场景。不在现有 UC 编号中。

### User Goal
手动或临时运行某个 strategy/model 在最新 feature/date 上的 prediction，产出 signal/candidate artifact，用于 shadow 前观察和验证信号。

### Scope
包含：
- 读取数据、读取模型、运行推理
- 生成本地 artifact（candidates、signals）
- 检查 provenance
- 输出可追溯的候选列表

不包含：
- 下单、改持仓、写 ledger
- promotion、修改 broker/trader/production
- 正式 daily shadow

### Inputs
- signal_date / decision_date / execution_date（执行日必须是决策日后的下一开市日）
- strategy_id / feature_list_id
- 策略配置中的显式 model bundle（必须解析到具体 model hash/path，禁止 latest）
- calibration artifact（如有）

### Outputs
- `outputs/{signal_date}/{strategy_id}/{run_id}/candidate_run.json`

CandidateRun 是不可覆盖的研究候选产物，至少包含：
- data/signal/decision/execution date 及 aligned-snapshot/next-open-session 语义
- model bundle、每个模型及 scaler/meta 文件的 SHA-256、训练区间、label horizon、权重
- universe/config/feature-list/feature-snapshot/candidate hash、Git 状态、数据新鲜度和覆盖率
- 每个模型的 ordered feature-list hash，并与 pinned center/scale index 顺序严格一致
- 每只股票的分模型 score/rank、blend score/rank、模型排名分歧
- universe snapshot 语义、逐特征缺失率/有效唯一值及剔除原因汇总

`current_constituents_snapshot` 必须锚定 artifact 创建时按权威交易日历和收盘
cutoff 推导出的 `decision_date`。financial_rc 配置固定
`feature_snapshot_lag_sessions=1`，因此整套 data/signal snapshot 是决策日的上一
开市日；显式日期也只能等于这一边界，不能借此回跑任意历史日期。当前尚未实现
PIT constituent provider；传入 `pit_constituents_snapshot` 必须非零失败且不得
生成 CandidateRun。生成器必须 hash 决策日成分集合，并验证 feature snapshot
的股票集合与成分快照完全一致。

`feature_snapshot_hash` 使用稳定排序后的 instrument、固定顺序 feature 和原始
数值计算；数值采用精确 float-hex 表示，缺失值统一为 null。它与
`feature_list_hash` 分工：后者标识特征名称/顺序，前者标识当次实际输入值。
任何模型实际使用的 feature 若当日截面无变化，或任一 feature 缺失率超过配置
阈值，推理必须 fail closed，不能以总覆盖率通过来掩盖死因子。

LightGBM 使用 positional ndarray 预测。模型 artifact 中已由 SHA-256 pin 的
`center.json` / `scale.json` index 是训练输入顺序的权威记录；生成器必须要求
两者的完整有序列表都与 feature registry 完全相等，不能只比较集合或数量。
CandidateRun 每个模型同时记录与顶层一致的 `ordered_feature_list_hash`。

每次推理进入 `run_candidate_inference()` 时只采样一次 `run_anchor_at`。日期解析、
`created_at`、run ID 和 artifact 的 completed-session 契约必须使用同一个 anchor，
避免跨越 18:00 cutoff 后生成一个无法通过自身 checker 的 artifact。

#### financial_rc 0.5/0.5 迁移说明

迁移前两个脚本表达的是不同产品，而非同一个策略的等价实现：

- `predict_financial_rc.py` 使用 0.3×60d + 0.7×180d，服务 Top5 + trailing-stop 流程；
- `gen_candidate_top200.py` 使用 0.5×60d + 0.5×180d，服务 Top200 人工财报筛选。

本 UC 按操作者确认选择第二种语义作为 canonical human-research workflow。
0.5/0.5 是当前运行契约，**不是**优于 0.3/0.7 的量化证据；在独立、可复现的
blend-weight 与组合构建对照研究完成前，不得据此晋级 shadow/production。

### Canonical Entrypoints
`scripts/run_daily.py --strategy <id> --mode infer --signal-date <date|auto>`。

`scripts/dev/predict_financial_rc.py` 与 `scripts/dev/gen_candidate_top200.py`
仅保留为 deprecated compatibility wrapper，不再拥有模型选择、权重或产物语义。

### Key Artifacts
- `outputs/{signal_date}/{strategy_id}/{run_id}/candidate_run.json`
- `data/research/signals/{signal_id}/{signal_run_id}/`

### Required Checks
- `harness/checks/check_daily_inference_ready.py`
- `harness/checks/check_inference_artifact.py`
- `harness/checks/check_shareholder_data_freshness.py`

### Operator Runbook

```bash
# 先同步 canonical/Qlib。对 CSI800 的 apply 会补齐 T-1 两融，同时逐日
# catch-up 股东人数/前十大股东公告并以 ann_date 做 PIT；任一来源不新鲜即阻断。
python scripts/data_sync.py \
  --config configs/data/csi800_daily_sync.yaml \
  --apply

# 自动选择最近已完成决策日的上一开市日作为整套 feature snapshot，
# 并输出供人工研究的 Top 200 候选。
python scripts/run_daily.py \
  --strategy financial_rc \
  --mode infer \
  --signal-date auto \
  --top-k 200

# 使用命令输出的 artifact 路径做独立契约复核。
python harness/checks/check_inference_artifact.py \
  --artifact outputs/<signal_date>/financial_rc/<run_id>/candidate_run.json

# 排查历史中断并列出必须重建的缓存、模型、候选与研究产物。
python harness/checks/check_shareholder_data_freshness.py \
  --as-of-date <signal_date> \
  --output runs/data_audit/shareholder_impact.json
```

margin repair 或运行前 readiness check 任何非零退出均视为阻断，不能沿用旧候选。
`--skip-margin-repair` 仅用于诊断，不属于 financial_rc 的标准日常运行路径。
financial_rc 的训练与推理都必须使用同一 `feature_availability` 契约：普通特征
与两融原始输入严格取同一个 signal/data date。由于两融次一开市日才视为完整，
决策日 T 使用完整的 T-1 aligned snapshot，并为 T+1 生成候选；CandidateRun
必须记录 `decision_date`、snapshot lag 和同日 `margin.as_of_date`，artifact
checker 从交易日历独立复核。
股东人数与前十大股东侧车采用 `ann_date <= data_date` 的 backward as-of 规则；
`end_date` 只表示报告期，禁止用作可得性日期。日常同步维护 checked-through state，
但空响应也必须被审计；全市场覆盖率、横截面 stale-days 中位数任一超阈值则全局
fail-closed，单只股票 stale-days 超阈值则从 eligible universe 排除。CandidateRun
必须 pin 两份源文件 SHA、截至 data_date 的 canonical snapshot hash，以及派生特征
freshness profile。修复历史缺口后必须重建相关 feature cache、重训模型并重跑候选，
不能只替换 parquet 后继续使用旧模型。
当前指数成分的 membership start 不能充当特征历史起点。日常同步会检查每个当前
成分股是否具备 1461 个日历日的 canonical lookback（上市不足者从上市日算起）；
新纳入成分若只有纳入日之后的数据，必须先回补历史、对这些 symbols 执行 Qlib
`dump_fix`，并把 Qlib instrument registry 的数据可见起点对齐到 canonical；否则
bin 文件虽已写入，Qlib 仍会隐藏纳入日前的历史，readiness 必须 fail。CandidateRun
必须逐只列出所有 ineligible 股票、
原因和缺失特征，artifact checker 复算 drop-reason 汇总，禁止只报告一个总数。
该产物只进入人工财报/基本面复核，不直接生成订单或修改 ledger。

### Owner Agent
operator_agent

### Allowed Paths
- `scripts/run_daily.py`
- `configs/strategies/`
- `outputs/`
- `data/research/signals/`
- `data/research/models/`
- `qsys/signal/`
- `qsys/data/adapter.py`
- `qsys/feature/`
- `scripts/dev/`
- `harness/checks/`
- `docs/requirements/`
- `.claude/skills/`
- `tests/`

### Forbidden Paths
- `qsys/broker/`
- `qsys/trader/`
- `qsys/ledger/`
- `deploy/`
- `qsys/ops/daily_runner.py`

### Safety Semantics
- `infer` 是 artifact-only 分支，在创建 DailyRunner、promotion snapshot 或 account context 前返回。
- 盘中不能使用当日未完成收盘；默认 18:00 后才允许当日成为 completed signal date。
- CandidateRun 必须满足 data_date = signal_date <= decision_date < execution_date；execution_date 必须是 decision_date 的下一开市日。
- current constituents snapshot 只锚定最近已完成的 decision_date；signal_date 必须匹配配置的 bounded snapshot lag，PIT provider 未实现，因此任意历史推理仍不可用。
- 缺模型文件、maturity、数据新鲜度、特征覆盖或可交易性门槛时非零失败，不输出候选。
- 当前成分股 snapshot 可用于实时筛选，但不得伪称历史 PIT universe。
