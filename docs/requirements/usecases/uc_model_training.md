# UC_MODEL_TRAINING: Model Training

## Status
stable

## Source
`docs/USE_CASES.md` 无独立 UC 编号。模型训练在 USE_CASES.md 中是 UC-4 信号研究的一部分，
通过 `scripts/run_daily.py --mode train` 调度。USE_CASES.md 是权威来源，本文档是超集补充。
本文档将其独立为 use case 便于分配 owner 和治理。

## User Goal
系统按周（或按需）自动训练模型，产出模型 artifact 并写入 shadow pointer。训练流程稳定、可重现、训练后产物可被 daily ops 消费。

## Scope
包含：
- 定时（每周）全量训练
- 模型 artifact 持久化（model.pkl, meta.yaml, config_snapshot, features.json）
- shadow pointer 写入（`artifacts/registry/models/{strategy}/shadow.json`）
- 训练 metrics 记录

不包含：
- 研究阶段的快速实验训练（见 UC_RESEARCH_BACKTEST）
- prod pointer 写入（见 UC_CANDIDATE_PROMOTION）
- 训练过程的回测验证（在 UC_RESEARCH_BACKTEST 中）

## Inputs
- 策略配置
- 特征集 ID
- 训练日期窗口
- 行情/特征数据
- 训练 endpoint（`scripts/run_alpha_v1_weekly_train.py`）

## Outputs
- `experiments/alpha_v1_models/{timestamp}/` — 模型 artifact 目录
- `artifacts/registry/models/{strategy}/shadow.json` — shadow pointer
- `runs/{date}/{run_id}/training_result.json` — 训练结果

## Canonical Entrypoints

| Entrypoint | 职责 | Inputs | Outputs / Artifacts |
|-----------|------|--------|---------------------|
| `scripts/run_daily.py --mode train` | 模型训练（通过 DailyRunner 调度） | 策略配置、训练日期窗口 | `experiments/{strategy}_models/{timestamp}/`, `artifacts/registry/models/{strategy}/shadow.json` |

通过 `run_daily.py` 统一入口的 train mode 调度。对齐 `docs/USE_CASES.md` §7。

## Key Artifacts
- `artifacts/registry/models/{strategy}/shadow.json` — shadow pointer
- `experiments/alpha_v1_models/{timestamp}/` — 模型文件

## Required Checks
- `harness/checks/check_no_latest_model_resolution.py`（训练后不写 symlink）
- `harness/checks/check_model_resolution_boundary.py`
- TBD: training artifact completeness check

## Owner Agent
builder_agent

## Allowed Paths
- `qsys/model/`
- `qsys/ops/model_registry.py`
- `qsys/ops/model_resolver.py`
- `configs/strategies/`
- `harness/checks/`
- `tests/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/backtest/`
- `deploy/`

## Open Questions
- 无
