# UC_RESEARCH_BACKTEST: Research Backtest

## Status
stable

## User Goal
研究员可以定义实验（特征集、标签、模型参数），运行滚动训练/预测，评估信号质量，基于信号运行回测，并比较不同实验的结果。

## Scope
包含：
- 特征集定义与解析
- 标签定义与计算
- 滚动 OOS 训练/预测
- 信号评估（IC / RankIC / ICIR）
- 基于信号的策略回测
- 实验索引与比较

不包含：
- 模型训练生产化（见 UC_MODEL_TRAINING）
- 策略晋级生产（见 UC_CANDIDATE_PROMOTION）
- UI 层面的回测比较（见 UC_UI_ANALYSIS）

## Inputs
- 研究配置 YAML（`configs/research/*.yaml`）
- 特征配置（`configs/features/*.yaml`）
- 标签配置（`configs/labels/*.yaml`）
- 行情数据（canonical / qlib_bin）

## Outputs
- `data/research/signals/{signal_id}/{signal_run_id}/predictions.parquet`
- `data/research/signals/{signal_id}/{signal_run_id}/manifest.json`
- `data/research/experiments/{experiment_id}/`
- `data/research/backtests/{run_id}/{backtest_id}/`
- 信号评估 metrics

## Canonical Entrypoints

| Entrypoint | 职责 | 对应 UC | Inputs | Outputs / Artifacts |
|-----------|------|---------|--------|---------------------|
| `scripts/run_research.py --config <path>` | 信号研究 + 信号组合 | UC-4/6 | 研究配置 YAML | `data/research/signals/`, `data/research/experiments/` |
| `scripts/run_signal_analytics.py --experiment-id <id>` | 信号只读分析 | UC-5 | experiment_id / signal_run_ref + label_id | IC/RankIC/ICIR 矩阵 |
| `scripts/run_backtest.py --config <path>` | 信号驱动回测 | UC-7 | SignalRunRef + 策略配置 | `data/research/backtests/` |
| `scripts/compute_labels.py --config <path>` | 标签计算 | UC-3 | 标签配置 YAML | `data/research/labels/` |

对齐 `docs/USE_CASES.md` §7。

## Key Artifacts
- `data/research/signals/` — SignalStore
- `data/research/labels/` — LabelStore
- `data/research/experiments/` — 实验索引
- `data/research/backtests/` — 回测产物

## Required Checks
- TBD: research artifact schema check
- TBD: label maturity gate check
- TBD: backtest lineage check

## Owner Agent
research_agent

## Allowed Paths
- `qsys/research/`
- `qsys/signal/`
- `qsys/label/`
- `qsys/feature/`
- `qsys/evaluation/`
- `qsys/backtest/`
- `qsys/analysis/`
- `configs/research/`
- `configs/features/`
- `configs/labels/`
- `scripts/research/`
- `tests/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/ops/daily_runner.py`
- `deploy/`

## Open Questions
- （已定）IC 计算统一路线：rolling 过程中模型和信号层都存档，通过 SignalStore 做信号组合，然后统一算 IC/metrics 以及运行回测。后续应逐步收束到单一路径。
