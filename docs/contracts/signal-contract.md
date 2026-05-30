# Signal Contract

## Purpose

某个 strategy / model / signal expression 在某个 signal_date 对某个 instrument 给出了什么信号？这个信号是否 out-of-sample？它能否被 backtest / preopen / UI 消费？

## Producers

- Predictor
- RollingResearchRunner
- DailyRunner preopen
- StrategyCandidate adapter
- signal generator
- signal expression / combination layer

## Consumers

- BacktestEngine
- SignalEvaluator
- ExperimentIndex
- DailyRunner preopen
- Research UI（只读）
- Ops UI（只读）
- Candidate/Shadow dashboard（只读）

## Storage / Transport

Signal 可以存储于：

- SignalStore（`qsys/signal/store.py`）
- experiments artifact
- parquet / CSV
- DuckDB research analytics store
- daily preopen signal artifact

当前不假设唯一存储介质。SignalStore 是目标主线，研究侧可先用 experiments artifact 过渡。

## Grain

```
strategy_id + signal_id + signal_date + instrument
```

如有 model 预测，则还要能追溯：

```
model_id / model_version / feature_set / run_id
```

## Required Fields

| 字段 | 含义 |
|------|------|
| `strategy_id` | 策略标识 |
| `signal_id` | 信号标识（如 alpha_v1、alpha_v1_technical_composite） |
| `signal_date` | 信号来源日期，不是执行日期 |
| `instrument` | 标的代码 |
| `score` | 信号值（预测或排序依据） |
| `rank` | 截面排名 |
| `universe_id` | 标的池标识 |
| `run_id` | 产生信号的 run_id |
| `model_id` | 使用的模型标识 |
| `model_version` | 模型版本 |
| `feature_set` | 特征集标识 |
| `signal_expression` | 信号表达式（如 `raw(s1) + 0.2 * zscore(s2)`） |
| `is_oos` | 是否 out-of-sample |
| `generated_at` | 生成时间 |

## Optional Fields

| 字段 | 含义 |
|------|------|
| `raw_score` | 归一化前的原始预测值 |
| `zscore` | 截面标准化值 |
| `industry_neutral_score` | 行业中性化后值 |
| `market_neutral_score` | 市值中性化后值 |
| `coverage_flag` | 该 instrument 是否在模型覆盖范围内 |
| `missing_reason` | 缺失原因 |
| `transform_chain` | 应用的变换链 |
| `combination_weight` | 组合权重 |

## Invariants

- signal_date 是信号来源日期，不是执行日期。
- daily preopen 消费的 signal 必须基于最近已收盘数据。
- research signal 必须标记是否 OOS。
- signal 必须可追溯 run_id / model / feature set / expression。
- 不允许把 training in-sample signal 当成 Candidate/Shadow 的可交易 signal。
- signal 可以缺失，但缺失必须可解释。
- signal contract 不负责下单，只负责表达排序或预测强度。

## UI / Monitoring Usage

Research UI 应能看：

- signal distribution
- coverage
- IC / RankIC link
- signal comparison
- model version 对比

Ops UI 应能看：

- 当日 strategy signal
- top / bottom
- missing instruments
- signal freshness
- 是否进入 plan

## Current Legacy Compatibility

当前 alpha_v1 signal 通过 `run_alpha_v1_daily.py` 和旧策略路径产生，写 `daily/{date}/pre_open/signals/`。新信号路径（`StrategyCandidate` → `DailyRunner`）尚未完全就绪。迁移期间两套路径共存，但旧路径不扩张新依赖。

## Versioning

Signal 字段可随 SignalArtifact schema 演进。新增字段不应破坏旧信号读取。旧 CSV/JSON signal 文件保留不删，通过 artifact path 追溯。
