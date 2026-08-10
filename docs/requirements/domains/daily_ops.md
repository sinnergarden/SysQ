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
- 模型 pointer（`artifacts/registry/models/{strategy_id}/shadow.json` 或 legacy pointer）
- promotion pointer（`data/research/promotions/shadow.yaml`）

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
- `scripts/run_daily_batch.py` — 批量 wrapper，非独立 canonical entrypoint

对齐 `docs/USE_CASES.md` §7。

### Key Artifacts
- `data/trade.db` — ledger SOT
- `daily/{date}/pre_open/signals/` — signal basket
- `daily/{date}/post_close/` — reconciliation, MTM
- `runs/{date}/{run_id}/` — 完整运行产物

### Required Checks
- `harness/checks/check_model_resolution_boundary.py`
- `harness/checks/check_no_latest_model_resolution.py`
- TBD: daily artifact schema check
- TBD: preopen/postclose stage integrity check

### Owner Agent
operator_agent

### Allowed Paths
- `scripts/data_sync.py`
- `scripts/run_daily.py`
- `qsys/ops/`
- `configs/strategies/`
- `harness/checks/`
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
- signal_date / execution_date（执行日必须是信号日后的下一开市日）
- strategy_id / feature_list_id
- 策略配置中的显式 model bundle（必须解析到具体 model hash/path，禁止 latest）
- calibration artifact（如有）

### Outputs
- `outputs/{signal_date}/{strategy_id}/{run_id}/candidate_run.json`

CandidateRun 是不可覆盖的研究候选产物，至少包含：
- data/signal/execution date 及 next-open-session 语义
- model bundle、每个模型及 scaler/meta 文件的 SHA-256、训练区间、label horizon、权重
- universe/config/feature-list/feature-snapshot/candidate hash、Git 状态、数据新鲜度和覆盖率
- 每个模型的 ordered feature-list hash，并与 pinned center/scale index 顺序严格一致
- 每只股票的分模型 score/rank、blend score/rank、模型排名分歧
- universe snapshot 语义、逐特征缺失率/有效唯一值及剔除原因汇总

`current_constituents_snapshot` 只允许 `signal_date` 等于 artifact 创建时按
权威交易日历和收盘 cutoff 推导出的最近已完成交易日。当前尚未实现 PIT
constituent provider，因此历史推理不可用；传入 `pit_constituents_snapshot` 也必须
非零失败且不得生成 CandidateRun。生成器必须 hash 实际成分集合，并验证
feature snapshot 的股票集合与成分快照完全一致。

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

### Operator Runbook

```bash
# 先同步 signal_date=T 的 canonical/Qlib。对 CSI800 的 apply 会解析最近
# 一个已发布交易日 T-1，只检查并回补截至 T-1 的两融缺口，再刷新受影响
# 的 Qlib symbols；不会等待次日 08:30 的 T 日 margin_detail。
python scripts/data_sync.py \
  --config configs/data/csi800_daily_sync.yaml \
  --apply

# 自动选择已完成的最近交易日，并输出供人工研究的 Top 200 候选。
python scripts/run_daily.py \
  --strategy financial_rc \
  --mode infer \
  --signal-date auto \
  --top-k 200

# 使用命令输出的 artifact 路径做独立契约复核。
python harness/checks/check_inference_artifact.py \
  --artifact outputs/<signal_date>/financial_rc/<run_id>/candidate_run.json
```

margin repair 或运行前 readiness check 任何非零退出均视为阻断，不能沿用旧候选。
`--skip-margin-repair` 仅用于诊断，不属于 financial_rc 的标准日常运行路径。
financial_rc 的训练与推理都必须使用同一 `feature_availability` 契约：普通特征
取 T 日收盘快照，两融原始输入严格取 T-1 开市日；CandidateRun 必须记录实际
`margin.as_of_date`，artifact checker 从交易日历独立复核。
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
- post-close CandidateRun 必须满足 data_date = signal_date < execution_date，execution_date 必须是下一开市日。
- current constituents snapshot 只允许最近已完成交易日；PIT provider 未实现，因此历史推理暂不可用。
- 缺模型文件、maturity、数据新鲜度、特征覆盖或可交易性门槛时非零失败，不输出候选。
- 当前成分股 snapshot 可用于实时筛选，但不得伪称历史 PIT universe。
