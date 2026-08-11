# Domain: Model Training

## Domain Scope
模型训练与 artifact 管理：定时全量训练、模型 artifact 持久化、可选 pointer 写入。
不包含：研究阶段的快速实验训练（research domain）、prod pointer 写入（promotion domain）。

## UC_MODEL_TRAINING

### Status
stable

### Source
`docs/USE_CASES.md` 无独立 UC 编号。模型训练在 USE_CASES.md 中是 UC-4 信号研究的一部分，
通过 `scripts/run_daily.py --mode train` 调度。本文档将其独立为 use case 便于分配 owner 和治理。

### User Goal
系统按周（或按需）自动训练模型，产出可复现的模型 artifact；只有配置明确授权时才写 pointer。

### Scope
包含：
- 定时（每周）全量训练
- 模型 artifact 持久化（model.pkl, meta.yaml, config_snapshot, features.json）
- 可选 shadow pointer 写入（`pointer_write_mode=shadow`）
- `pointer_write_mode=none` 的 research bundle（不得隐式晋级）
- 训练 metrics 记录
- ordered feature list、实际有效训练窗口、universe/label/training snapshot hash
- label artifact 与当前训练 universe 的 membership coverage
- 股东侧车 source snapshot hash、announcement-date PIT freshness contract/profile

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
- `data/research/models/{experiment_id}/{model_hash}/` — immutable research model bundle

### Canonical Entrypoints
- `scripts/run_daily.py --strategy <id> --mode train --trade-date <as-of>` — 通过 DailyRunner 调度

### Key Artifacts
- `artifacts/registry/models/{strategy}/shadow.json`
- `experiments/alpha_v1_models/{timestamp}/`

### Required Checks
- `harness/checks/check_no_latest_model_resolution.py`
- `harness/checks/check_model_resolution_boundary.py`
- TBD: training artifact completeness check
- `harness/checks/check_shareholder_data_freshness.py`

financial_rc 训练前必须验证训练窗口末端的两份股东侧车均满足覆盖率与横截面
stale-days 阈值；对训练期逐日横截面中位数取最大值，防止长窗口把一次源中断
稀释掉。超过 row 阈值或缺少 stale-days 的样本行必须剔除并记录数量。任何来源
回补都会改变训练输入，旧模型一律视为受影响而重新训练，不允许仅重跑 inference。
当前 snapshot 的 membership 或历史边界改变时，60d/180d label artifact 也必须重建；
只校验 label 总股票数不够，因为旧成分与新成分可能恰好一换一。训练必须按精确
current-universe 交集计算覆盖率，低于策略阈值时 fail-closed，并把缺失成员写入 lineage。

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
- 历史训练 universe 仍是 current constituents snapshot；PIT constituent provider 未实现。

### Future Work

Replace alpha_v1-specific weekly training scripts with a generic training entrypoint.
Target shape:

- ``model_id``
- ``label_id``
- ``feature_list_id``
- ``universe``
- ``train_start`` / ``train_end``
- ``output_artifact_dir``
- ``registry_pointer_write_mode``: `none` / `shadow` / `candidate`

Do **not** add new feature-combination-specific train scripts (e.g. ``run_alpha_vX_weekly_train.py``).
