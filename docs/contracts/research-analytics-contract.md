# Research Analytics Contract

## Purpose

如何横向比较不同 signal / model / strategy 的历史表现？如何查询 IC / RankIC / label / backtest summary / experiment index？

Research Analytics 是只读分析层，不参与 production 状态写入，不替代 `data/trade.db`，不参与 broker execution。

## Producers

- RollingResearchRunner
- SignalEvaluator
- BacktestEngine
- ExperimentIndex builder
- research report generator

## Consumers

- Research UI（只读）
- strategy development SOP
- Candidate promotion review
- model comparison
- monitoring / regression check

## Storage / Transport

- DuckDB 是适合的 research analytics store，当前已在 `scripts/research/query_experiment_duckdb.py` 和 `qsys/signal/expression.py` 中使用。
- parquet / CSV / JSON index 也可作为过渡介质。
- DuckDB 不替代 ledger，不参与 broker execution，不存真实账户状态。

## Grain

```
per experiment
per run
per signal_id + date
per model_version
per strategy_id
per evaluation window
```

## Required Fields

| 字段 | 含义 |
|------|------|
| `experiment_id` | 实验标识 |
| `run_id` | 运行标识 |
| `strategy_id` | 策略标识 |
| `signal_id` | 信号标识 |
| `model_version` | 模型版本 |
| `feature_set` | 特征集标识 |
| `label_id` | 标签标识 |
| `eval_start` | 评估窗口开始 |
| `eval_end` | 评估窗口结束 |
| `metrics` | 评估指标（见下） |
| `artifact_paths` | 产物路径回溯 |
| `created_at` | 创建时间 |

Metrics（第一版最小集）：

- IC / RankIC / ICIR
- group_return（分组收益）
- long_short_return
- turnover
- max_drawdown
- annual_return
- cost_assumption

## Invariants

- research analytics 是只读分析层。
- 不写 production ledger。
- 不直接触发交易。
- 指标必须能追溯到 run_id 和 artifact_paths。
- 不同实验的 comparison 必须记录 eval window 和 cost assumption。
- label 必须有清晰 horizon 和 shift 语义，防止未来数据泄露。

## UI / Monitoring Usage

Research UI 应读取：

- experiment list
- signal comparison
- model comparison
- IC / RankIC trend
- backtest summary
- feature coverage
- regression alert

## Current Legacy Compatibility

当前 research 结果分散在 `experiments/` 目录中，signal_eval_index / backtest_index 等 CSV 文件已被 RollingResearchRunner 和 query_experiment_duckdb.py 消费。这些 CSV 在迁移期间继续有效，但应逐步统一到 DuckDB 查询视图。

## Versioning

实验索引和 metrics 字段可随研究需求扩展。新增字段不应破坏旧实验读取。DuckDB 查询保留原始 CSV 作为只读 fallback。先做 schema-on-read，不做 schema-on-write 约束。
