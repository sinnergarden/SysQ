# Domain: Research Backtest

## Domain Scope
量化研究与回测链路：特征集、标签、信号研究、信号分析、信号组合、信号驱动回测、实验比较。
不包含：模型生产化训练（model_training domain）、candidate 晋级（promotion domain）。

## UC_RESEARCH_BACKTEST

### Status
stable

### Source
`docs/USE_CASES.md` UC-2（Feature List）、UC-3（Label Config）、UC-4（Signal Research）、
UC-5（Signal Analytics）、UC-6（Signal Combination）、UC-7（Signal Backtest）。

### User Goal
研究员可以定义实验（特征集、标签、模型参数），运行滚动训练/预测，评估信号质量，基于信号运行回测，并比较不同实验的结果。

### Scope
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

### Inputs
- 研究配置 YAML（`configs/research/*.yaml`）
- 特征配置（`configs/features/*.yaml`）
- 标签配置（`configs/labels/*.yaml`）
- 行情数据（canonical / qlib_bin）

### Outputs
- `data/research/signals/{signal_id}/{signal_run_id}/predictions.parquet`
- `data/research/signals/{signal_id}/{signal_run_id}/manifest.json`
- `data/research/experiments/{experiment_id}/`
- `data/research/backtests/{run_id}/{backtest_id}/`
- 信号评估 metrics

### Canonical Entrypoints
- `scripts/run_research.py` — 信号研究 + 信号组合（UC-4/6）
- `scripts/run_signal_analytics.py` — 信号只读分析（UC-5）
- `scripts/research/backtest_from_signal.py` — 信号驱动回测（UC-7）

### Supporting Tools
- `scripts/research/compute_labels.py` — 标签计算（UC-3）

### Legacy Entrypoints
- `scripts/research/run_backtest.py` — 旧版回测入口，待收束

### Key Artifacts
- `data/research/signals/` — SignalStore
- `data/research/labels/` — LabelStore
- `data/research/experiments/` — 实验索引
- `data/research/backtests/` — 回测产物

### Financial RC 60d/180d Cache-to-Backtest Runbook

60d 与 180d 必须分别运行研究配置，使训练标签分别使用 61 与 181 个交易日的
成熟期。不得为了组合方便把两个标签塞进同一个滚动配置；pipeline 会采用声明
标签中的最大 maturity lag，从而把 60d 训练窗口也推迟到 181 日。

```bash
# 1. 分别产生滚动 OOS 信号并写入 SignalStore。
python scripts/run_research.py \
  --config configs/research/60d/_60d_v3a_growth_financial.yaml
python scripts/run_research.py \
  --config configs/research/60d/_180d_v3a_growth_financial.yaml

# 2. 从两个明确的 SignalRun 物化 0.5/0.5 组合 cache，再回测组合产物。
python scripts/research/backtest_from_signal.py \
  --signal-id fwd_ret_60d_raw__daily_zscore \
  --signal-run-id <60d_signal_run_id> \
  --signal-id-2 fwd_ret_180d_raw__daily_zscore \
  --signal-run-id-2 <180d_signal_run_id> \
  --blend-weight 0.5 \
  --materialize-blend \
  --blend-output-signal-id financial_rc_60d180d_equal \
  --blend-output-signal-run-id <reviewed_blend_run_id> \
  --start-date <execution_start> \
  --end-date <execution_end> \
  --top-n 200
```

物化组合采用 `(trade_date, data_date, instrument)` inner join，组合 manifest 必须
保留两个 source signal/run id 与权重。当前 csi800 历史研究仍使用 current
constituents snapshot，存在幸存者偏差；在 PIT universe provider 接通前，这类
回测只能用于流程烟测和探索，不能宣称无偏 OOS 或用于晋级。

### Required Checks
- TBD: research artifact schema check
- TBD: label maturity gate check
- TBD: backtest lineage check

### Owner Agent
research_agent

### Allowed Paths
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

### Forbidden Paths
- `qsys/ledger/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/ops/daily_runner.py`
- `deploy/`

### Open Questions
- （已定）IC 计算统一路线：rolling 过程中模型和信号层都存档，通过 SignalStore 做信号组合，然后统一算 IC/metrics 以及运行回测。
