# Domain: Model Training

## Domain Scope
模型训练与 artifact 管理：定时全量训练、模型 artifact 持久化、shadow pointer 写入。
不包含：研究阶段的快速实验训练（research domain）、prod pointer 写入（promotion domain）。

## UC_MODEL_TRAINING

### Status
stable

### Source
`docs/USE_CASES.md` 无独立 UC 编号。模型训练在 USE_CASES.md 中是 UC-4 信号研究的一部分，
通过 `scripts/run_daily.py --mode train` 调度。本文档将其独立为 use case 便于分配 owner 和治理。

### User Goal
系统按周（或按需）自动训练模型，产出模型 artifact 并写入 shadow pointer。

### Scope
包含：
- 定时（每周）全量训练
- 模型 artifact 持久化（model.pkl, meta.yaml, config_snapshot, features.json）
- shadow pointer 写入（`artifacts/registry/models/{strategy}/shadow.json`）
- 训练 metrics 记录

不包含：
- 研究阶段的快速实验训练
- prod pointer 写入
- 训练过程的回测验证

### Inputs
- 策略配置
- 特征集 ID
- 训练日期窗口
- 行情/特征数据

### Outputs
- `experiments/alpha_v1_models/{timestamp}/` — 模型 artifact 目录
- `artifacts/registry/models/{strategy}/shadow.json` — shadow pointer
- `runs/{date}/{run_id}/training_result.json` — 训练结果

### Canonical Entrypoints
- `scripts/run_daily.py --mode train` — 通过 DailyRunner 调度

### Key Artifacts
- `artifacts/registry/models/{strategy}/shadow.json`
- `experiments/alpha_v1_models/{timestamp}/`

### Required Checks
- `harness/checks/check_no_latest_model_resolution.py`
- `harness/checks/check_model_resolution_boundary.py`
- TBD: training artifact completeness check

### Owner Agent
builder_agent

### Allowed Paths
- `qsys/model/`
- `qsys/ops/model_registry.py`
- `qsys/ops/model_resolver.py`
- `configs/strategies/`
- `harness/checks/`
- `tests/`

### Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`
- `deploy/`

### Open Questions
- 无
