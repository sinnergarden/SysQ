# QSys 投研全链路 Use Cases

## 1. 文档目标

本文档定义 QSys 投研平台从数据、特征、标签、信号研究、信号分析、策略回测、候选晋级到日常运行的核心 Use Case。

QSys 的目标是形成一套可重复、可追踪、可组合的投研工作流：

```text
Data → Feature → Label → Signal Research → Signal Analytics → Backtest → Candidate Promotion → Daily Trading
```

每个 Use Case 应满足以下原则：

- 通过标准配置描述任务。
- 通过统一 CLI 或核心 Pipeline 执行。
- 产物通过标准 Store、Manifest 和稳定 ID 追踪。
- 上下游通过引用对象连接，而不是通过手工拼路径连接。
- 业务逻辑沉入 `qsys/` 模块，`scripts/` 仅负责命令行调度。
- 标准配置与 Manifest 应有 schema 校验；未知字段应默认拒绝。
- 核心 Pipeline 应具备幂等语义：相同配置和相同输入数据应产生可追踪的稳定产物，或在未显式 overwrite 时安全拒绝覆盖。

---

## 2. 核心对象

| 对象 | 含义 |
|---|---|
| Data Artifact | 原始行情、规范化行情、指数行情、指数成分、Qlib 数据等基础数据产物。 |
| Feature List | 一组明确选择的模型输入特征。Feature List 是实验输入的一部分，应可版本化、可追踪。 |
| Label | 模型训练或信号评估使用的目标变量，如未来收益、风险调整收益、方向分类标签等。 |
| SignalRun | 一次信号生成结果。包含完整时间序列预测分数，存储在 SignalStore。 |
| SignalRunRef | 对某个 SignalRun 的稳定引用，至少包含 `signal_id` 与 `signal_run_id`。 |
| Experiment | 一次信号研究实验。负责组织 rolling windows、generator、transform、SignalRun、评估结果和组合信号。 |
| Signal Evaluation | 对 SignalRun 的 IC、RankIC、ICIR、分段稳定性、市场状态分解等研究层评估。 |
| Signal Analytics | 对已有 SignalRun 和 Label 的只读分析能力，支持跨信号、跨标签比较。 |
| Strategy Config | 将信号转换为目标组合的策略配置，如 top-k、rank weight、buffer、最大权重、调仓频率等。 |
| BacktestRun | 一次策略历史回测结果。包含组合净值、订单、成交、持仓、绩效指标和回测 manifest。 |
| Candidate | 一个可晋级候选。通常由 SignalRunRef、Strategy Config、BacktestRun 和评估证据共同定义。 |
| Promotion Pointer | 指向当前 shadow / production candidate 的指针文件。每日运行读取 pointer，而不是直接读取 latest signal。 |
| DailyRun | 一次日常运行产物，包含当日输入、信号引用、目标组合、订单计划、成交回填、账户快照和报告。 |

---

## 3. 标准产物结构

QSys 的研究产物分层存储：

```text
data/
├── raw/
│   └── ...                         # 外部原始数据
├── canonical/
│   └── ...                         # 规范化后的连续数据层
└── research/
    ├── labels/
    │   └── <label_id>/
    │       ├── labels.parquet
    │       └── manifest.json
    ├── signals/
    │   └── <signal_id>/
    │       └── <signal_run_id>/
    │           ├── predictions.parquet
    │           └── manifest.json
    ├── experiments/
    │   └── <experiment_id>/
    │       ├── signal_research_manifest.json
    │       ├── rolling_windows.csv
    │       ├── matrix_jobs.csv
    │       ├── cross_signal_index.csv
    │       └── analytics/
    │           ├── ic_matrix.csv
    │           ├── rank_ic_matrix.csv
    │           └── daily_ic_*.csv
    ├── backtests/
    │   └── <strategy_run_id>/
    │       └── <backtest_id>/
    │           ├── manifest.json
    │           ├── metrics.json
    │           ├── daily_summary.csv
    │           ├── orders.csv
    │           └── fills.csv
    ├── candidates/
    │   └── <candidate_id>/
    │       └── candidate.yaml
    └── promotions/
        ├── shadow.yaml
        └── production.yaml
```

设计原则：

- `signals/` 是全局 SignalStore，不隶属于某个 experiment 目录。
- `experiments/` 记录研究实验索引、窗口、组合和分析结果。
- `backtests/` 记录组合层验证结果。
- `candidates/` 和 `promotions/` 用于从研究结果进入 shadow / production。

---

# 4. Use Cases

---

## UC-1: Data Sync & Validation

### 目的

同步、规范化并校验投研所需的基础数据，包括个股日线、指数行情、指数成分、停牌/涨跌停状态、Qlib 数据等。

### 典型输入

```yaml
data_sync_id: csi800_daily_sync
universe: csi800
indices:
  - 000906.SH
  - 000300.SH
date_range:
  start_date: "2018-01-01"
  end_date: "2025-12-31"
mode: incremental
tasks:
  daily_bar: true
  index_ohlcv: true
  index_constituents: true
  qlib_bin: true
validation:
  enabled: true
  fail_on_error: true
  checks:
    trading_calendar_coverage: true
    duplicate_rows: true
    missing_bars: true
    ohlc_legal: true
    adjustment_continuity: true
    suspension_limit_status: true
```

### 标准入口

目标入口：

```bash
python scripts/data_sync.py --config configs/data/csi800_daily_sync.yaml
```

### 输出

```text
data/raw/...
data/canonical/...
~/.qlib/qlib_data/...
data/reports/data_quality/<data_sync_id>/data_quality_report.json
```

### 当前状态

已有多个分散脚本可完成部分流程：

```text
run_update.py
sync_csi800_daily.py
create_instrument_universe.py
dump_bin.py
```

### 缺口

- 缺少统一数据同步入口。
- 缺少标准数据质量报告。
- 需要把已有数据脚本收敛到 `scripts/data_sync.py`。
- 需要统一 data sync manifest。

---

## UC-2: Feature List Management

### 目的

定义模型训练使用的特征集合，使实验输入明确、可复现、可比较。

QSys 使用显式 Feature List 模式：研究配置只使用配置中列出的 feature。

### 典型输入

```yaml
feature_list_id: momentum_price_volume_v1
description: "Momentum + price-volume features"
features:
  - ret_5d
  - ret_20d
  - stock_minus_index_ret_5d
  - turnover_rate
  - volume_shock_5
  - illiquidity
```

### 标准入口

目标能力：

```python
FeatureListRegistry.load("momentum_price_volume_v1")
```

目标配置目录：

```text
configs/features/<feature_list_id>.yaml
```

### 输出

```text
list[str] qlib field expressions
```

### 当前状态

已有：

```text
FeatureLibrary
get_clean_features()
```

### 缺口

- 缺少外部 YAML feature list registry。
- 缺少从 `feature_list_id` 到 qlib expression list 的标准解析器。
- 需要让研究配置通过 `feature_list_id` 引用 feature 集合。

---

## UC-3: Label Configuration & Computation

### 目的

通过配置定义 Label，计算并持久化到 LabelStore，供模型训练和信号评估使用。

### 典型输入

```yaml
label_id: fwd_ret_10d_cs_zscore_clip3
formula:
  type: forward_return
  horizon: 10
  price: close
normalization:
  type: cs_zscore
  clip: 3.0
universe: csi800
date_range:
  start_date: "2018-01-01"
  end_date: "2025-12-31"
```

### 标准入口

目标入口：

```bash
python scripts/compute_labels.py --config configs/labels/fwd_ret_10d_cs_zscore_clip3.yaml
```

### 输出

```text
data/research/labels/<label_id>/labels.parquet
data/research/labels/<label_id>/manifest.json
```

### 当前状态

已有：

```text
compute_labels.py
LabelStore
```

### 缺口

- 需要支持 Label YAML 配置。
- 需要 Label Registry 管理公式、horizon、normalization、universe。
- 需要 manifest 固化 Label 配置和生成时间范围。

---

## UC-4: Signal Research

### 目的

给定 Feature List、Label、Generator、Transform 和 rolling calendar，训练模型并滚动生成标准 SignalRun，同时完成信号层评估。

该 Use Case 只处理信号研究，不做策略回测。

### 典型输入

```yaml
experiment_id: lgbm_csi800_10d_2024_2025
calendar:
  start_date: "2024-01-01"
  end_date: "2025-12-31"
  step_days: 5
  train_window_days: 756
feature_list_id: momentum_price_volume_v1
labels:
  - label_id: fwd_ret_10d_cs_zscore_clip3
generators:
  - generator_id: lgbm_10d_n200
    type: lightgbm_single_label
    params:
      label_id: fwd_ret_10d_cs_zscore_clip3
      universe: csi800
      n_estimators: 200
transforms:
  - transform_id: raw
    type: identity
  - transform_id: cs_zscore
    type: daily_zscore
```

Transform 可用于表达信号或特征后处理链，例如 winsorize、neutralize、daily_zscore。

超参搜索属于 Signal Research 的 matrix experiment，可通过多个 generator config 或参数网格展开：

```yaml
generators:
  - generator_id: lgbm_10d_n200
    type: lightgbm_single_label
    params:
      n_estimators: 200
  - generator_id: lgbm_10d_n500
    type: lightgbm_single_label
    params:
      n_estimators: 500
```

### 标准入口

核心模块：

```text
qsys/research/signal_pipeline.py :: SignalResearchPipeline
```

目标 CLI：

```bash
python scripts/run_research.py --config configs/research/lgbm_csi800_10d_2024_2025.yaml
```

### 输出

```text
data/research/signals/<signal_id>/<signal_run_id>/predictions.parquet
data/research/signals/<signal_id>/<signal_run_id>/manifest.json
data/research/experiments/<experiment_id>/signal_research_manifest.json
data/research/experiments/<experiment_id>/rolling_windows.csv
data/research/experiments/<experiment_id>/matrix_jobs.csv
```

### 当前状态

已有：

```text
SignalResearchPipeline
RollingResearchConfig
```

### 缺口

- 缺少 `scripts/run_research.py`。
- Feature List 尚未完全接入 generator 训练流程。
- 需要规范 research config schema。
- 需要明确 matrix experiment 与 hyperparameter search 的配置展开方式。

---

## UC-5: Signal Analytics

### 目的

对已有 SignalRun 和 Label 做只读分析，支持跨信号、跨标签的 IC、RankIC、ICIR 查询和比较。

该 Use Case 不生成新信号，不训练模型，不运行策略回测。

### 典型输入

按 experiment 查询：

```bash
python scripts/run_signal_analytics.py --experiment-id lgbm_csi800_10d_2024_2025
```

显式指定 SignalRunRef：

```bash
python scripts/run_signal_analytics.py \
  --signal-id lgbm_csi800_10d \
  --signal-run-id <signal_run_id> \
  --label-id fwd_ret_10d_cs_zscore_clip3
```

### 标准入口

核心模块：

```text
qsys/research/signal_analytics.py :: SignalAnalytics
```

目标 CLI：

```bash
python scripts/run_signal_analytics.py --experiment-id <experiment_id>
```

### 输出

```text
IC matrix
RankIC matrix
ICIR table
daily IC series
```

可选持久化输出：

```text
data/research/experiments/<experiment_id>/analytics/
  ic_matrix.csv
  rank_ic_matrix.csv
  daily_ic_<signal_id>_<label_id>.csv
```

### 当前状态

已有：

```text
SignalAnalytics
```

### 缺口

- 缺少 `scripts/run_signal_analytics.py`。
- 需要支持从 `experiment_id` 自动解析 SignalRunRef 和 Label。
- 需要统一 analytics 输出路径。

---

## UC-6: Signal Combination

### 目的

在一个 Signal Research Experiment 中，对多个基础信号做组合，生成新的组合 SignalRun，并完成信号层评估。

### 典型输入

作为 UC-4 research config 的一部分：

```yaml
signal_combinations:
  - combine_id: blend_lgbm_alpha
    type: linear_blend
    inputs:
      - source_generator_id: lgbm_10d
        source_transform_id: cs_zscore
        weight: 0.6
      - source_generator_id: alpha_v1
        source_transform_id: cs_zscore
        weight: 0.4
```

### 标准入口

同 UC-4：

```bash
python scripts/run_research.py --config configs/research/blend_lgbm_alpha.yaml
```

### 输出

```text
data/research/signals/<combined_signal_id>/<combined_signal_run_id>/predictions.parquet
data/research/experiments/<experiment_id>/cross_signal_index.csv
```

### 当前状态

已有：

```text
SignalResearchPipeline signal_combinations
```

### 缺口

- 当前主要支持同一 experiment 内组合。
- 跨 experiment 的 SignalRunRef 组合后续扩展。

---

## UC-7: Signal Backtest

### 目的

消费已有 SignalRunRef，按策略配置构造组合并运行历史回测，生成组合层 evidence。

该 Use Case 不训练模型，不生成 SignalRun，不计算 IC。

### 典型输入

```yaml
backtest_id: bt_lgbm_csi800_10d_top20_2024_2025
signal_ref:
  experiment_id: lgbm_csi800_10d_2024_2025
  signal_id: lgbm_csi800_10d
  signal_run_id: <signal_run_id>
strategy:
  strategy_id: top20_weekly
  type: rank_weight_topk
  top_n: 20
  rebalance_freq: weekly
  max_weight: 0.08
  cash_buffer: 0.02
execution:
  price_mode: open
  commission: 0.0003
  stamp_duty: 0.0005
  slippage: 0.001
  min_commission: 5.0
benchmark:
  benchmark_id: 000906.SH
date_range:
  start_date: "2024-01-01"
  end_date: "2025-12-31"
```

### 标准入口

目标 CLI：

```bash
python scripts/run_backtest.py --config configs/backtest/lgbm_csi800_10d_top20.yaml
```

### 输出

```text
data/research/backtests/<strategy_run_id>/<backtest_id>/manifest.json
data/research/backtests/<strategy_run_id>/<backtest_id>/metrics.json
data/research/backtests/<strategy_run_id>/<backtest_id>/daily_summary.csv
data/research/backtests/<strategy_run_id>/<backtest_id>/orders.csv
data/research/backtests/<strategy_run_id>/<backtest_id>/fills.csv
```

### 当前状态

已有：

```text
BacktestRunner.run_from_signal_cache
```

### 缺口

- `run_backtest.py` 需要支持 config-driven SignalRunRef 模式。
- 需要 benchmark-aware metrics。
- 需要更标准的 backtest manifest schema。

---

## UC-8: Daily Trading

### 目的

支持生产或半生产环境下的每日流程，包括数据更新、模型/信号更新、目标组合生成、订单计划、执行确认和收盘归因。

### 典型模式

```text
postclose: 同步数据、同步 broker、MTM、生成收盘报告
train:     根据策略设置进行模型训练或信号更新
preopen:   读取 promoted candidate，生成目标持仓和订单计划
```

### 典型输入

```yaml
daily_config_id: daily_shadow
mode: shadow
promotion_pointer:
  path: data/research/promotions/shadow.yaml
broker:
  type: qmt
  account_id: <account_id>
execution:
  operator_confirm: true
  price_mode: open
```

### 标准入口

当前入口：

```bash
python scripts/run_daily.py --mode postclose
python scripts/run_daily.py --mode train
python scripts/run_daily.py --mode preopen
```

### 当前状态

已有：

```text
run_daily.py
```

### 缺口

- 需要 promotion pointer 驱动的 daily signal selection。
- 需要标准 TargetPortfolio / OrderIntent。
- 需要 pre-trade risk check。
- 需要 QMT real-fill reconciliation 标准化。

---

## UC-9: Candidate Promotion

### 目的

基于 signal evidence 和 backtest evidence 生成 Candidate，并维护 shadow / production promotion pointer。

### 典型输入

```yaml
candidate_id: cand_lgbm_csi800_10d_top20_202606
signal_ref:
  experiment_id: lgbm_csi800_10d_2024_2025
  signal_id: lgbm_csi800_10d
  signal_run_id: <signal_run_id>
backtest_ref:
  strategy_run_id: top20_weekly
  backtest_id: bt_lgbm_csi800_10d_top20_2024_2025
gating:
  min_rank_ic: 0.01
  min_information_ratio: 0.3
  max_drawdown: 0.25
  max_turnover: 5.0
promotion:
  target: shadow
```

### 标准入口

目标 CLI：

```bash
python scripts/promote_candidate.py --config configs/promotion/cand_lgbm_csi800_10d_top20_202606.yaml
```

### 当前状态

NOT IMPLEMENTED

### 缺口

- 缺少 Candidate manifest。
- 缺少 promotion pointer。
- 缺少 gating rule evaluator。

---

# 5. 复杂 Use Cases

## UC-X: 新增 Feature 并回溯历史

```text
UC-1 → UC-2 → UC-4 → UC-5 → UC-7 → UC-9
```

## UC-Y: 新增模型类型

```text
1. 实现新 generator，满足 RollingSignalGenerator 协议
2. 在 UC-4 research config 中新增 generators[].type
3. 通过 SignalResearchPipeline 生成 SignalRun
4. 通过 SignalAnalytics 和 SignalBacktestPipeline 评估
```

## UC-Z: 新增策略模板

```text
1. 实现新 strategy target builder
2. 在 UC-7 backtest config 中新增 strategy.type
3. 使用 SignalRunRef 运行 SignalBacktestPipeline
```

## UC-W: Live vs Backtest Reconciliation

对比实盘或 shadow daily run 与对应 backtest 的偏差。状态：FUTURE

---

# 6. 标准入口收束目标

目标脚本布局：

```text
scripts/
├── data_sync.py                 # UC-1
├── compute_labels.py            # UC-3
├── run_research.py              # UC-4 / UC-6
├── run_signal_analytics.py      # UC-5
├── run_backtest.py              # UC-7
├── run_daily.py                 # UC-8
├── promote_candidate.py         # UC-9
├── checks/
└── ops/
```

入口原则：

- 同一 Use Case 只保留一个标准入口。
- `scripts/` 只做 CLI 调度。
- 核心业务逻辑位于 `qsys/`。
- 配置文件是实验事实源。
- 产物通过 manifest 和稳定 ID 引用。
