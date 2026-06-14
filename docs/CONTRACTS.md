# CONTRACTS

本文档定义 SysQ 模块之间的数据接口与职责边界。

- `docs/ARCHITECTURE.md` 说明系统地图（两条主链路、分层、过渡态）；
- **本文档说明模块之间通过哪些数据对象交互**；
- `docs/schema/` 说明部分 artifact 的字段级结构；
- `docs/REPO_LAYOUT.md` 说明代码、数据、artifact 的放置规则；
- 代码 API / dataclass / DB schema 是最终实现。

---

## 1. What is a Contract

**Contract** = 模块边界协议 / 逻辑接口协议。描述两个或多个模块之间的数据对象是什么、谁生产、谁消费、不可违反什么规则。

**Runtime artifact** = 每次运行产生的文件、DB record、parquet、CSV、JSON、report。Contract 不直接产生文件，但 runtime artifact 应符合 contract。

**Validator / audit** = 检查 runtime artifact 是否符合 contract 的工具。可未来实现，contract 本身不依赖 validator。

层次关系：

```
ARCHITECTURE.md    → 系统地图、模块职责、过渡态
CONTRACTS.md       → 模块之间通过什么数据对象交互、边界在哪
docs/schema/       → 部分 artifact 的字段级 schema
代码实现           → 最终保证（dataclass、DB schema、API params）
```

---

## 2. Global Rules

- **UI / monitoring 只读**——不写 ledger，不下单，不改策略。
- **Research artifact 不能直接进入 Production**。
- **Legacy path 只能作为 compatibility**，不扩展新依赖。
- **不把目标态写成当前事实**。当前迁移中的路径必须在文档中标注。
- **Contract-backed runtime artifacts** 一旦被 downstream、UI、monitoring 或 promotion 流程消费，不得随意删除、重命名或改变语义。清理旧产物前必须确认 consumer、迁移路径和回滚方式。
- **修改 contract**：普通字段补充走 PR review；改变接口语义、生命周期或跨模块边界时应补 ADR。
- 以下对象不能被当作临时文件随意清理：

  ```
  daily/{date}/
  experiments/
  data/audit/
  research analytics index
  approved model manifest
  ledger snapshots
  ```

---

## 3. Contract Map

| Contract | Producer | Consumer | Grain | Primary Use | Write Owner |
|---|---|---|---|---|---|
| Data Readiness | data sync、readiness check | DailyRunner、train、backtest、UI | per target_date / per data_domain / per universe | 判断数据是否满足执行要求 | data sync pipeline |
| Universe | data sync、universe build | Research、Backtest、Signal、UI | per universe_id / per date | 定义可用股票池 | data sync pipeline |
| Feature | feature engineering | Model training、Prediction | per feature_set_id / per as_of_date / per instrument | 模型和信号的数值输入 | research pipeline |
| Label | label generation | Model training、Signal evaluation | per label_id / per label_date / per instrument | 训练目标和评估口径 | research pipeline |
| Model | training pipeline | Predictor、DailyRunner | per model_id / per model_version | 可复用的模型 artifact | training pipeline |
| Prediction / Signal | Predictor、Research、DailyRunner | BacktestEngine、SignalEvaluator、allocation | per strategy_id + signal_id + signal_date + instrument | 预测强度或排序 | Predictor / DailyRunner |
| Signal Expression | research / combination layer | Predictor、allocation | per expression_id | 多 signal 变换组合 | research |
| Strategy Allocation | allocation engine、DailyRunner | Order Intent generation | per strategy_id + rebalance_date | signal → target portfolio | DailyRunner |
| Order Intent | DailyRunner / allocation | Execution bridge | per execution_date + strategy_id | 想交易什么 | DailyRunner |
| Execution | Execution Backend、broker | Ledger、postclose | per execution_id | 实际或模拟成交 | Execution Backend |
| Portfolio State / Ledger | LedgerService、postclose | DailyRunner、UI、monitoring | per account_id + execution_date + instrument | 账户状态与执行流水 SOT | LedgerService |
| Reconciliation | postclose reconciliation job | Monitoring、UI | per account_id + execution_date | 内部 vs 外部状态对账 | reconciliation job |
| Research Analytics | Research pipeline、BacktestEngine | Research UI、promotion review | per experiment / per run / per signal+label | IC/RankIC/实验比较 | research pipeline |
| Daily Ops Read Model | DailyRunner、postclose、report gen | Ops UI、monitoring | per execution_date + strategy_id | UI 只读展示 daily 状态 | DailyRunner |
| Promotion Evidence | candidate promotion pipeline | Production approval | per candidate / per strategy | 晋级证据审计轨迹 | promotion pipeline |

---

## 4. Data & Universe Contracts

### 4.1 Data Readiness Contract

**Purpose**: 定义数据可用性验证结果，作为 gate / audit stamp，判断某个 target_date / data_domain / universe 是否满足 train、backtest、preopen、postclose 的执行要求。

**Boundary**: 只回答"数据是否就绪"，不承载行情数据本身。

**Producers**: data sync pipeline（`scripts/ops/sync_csi800_daily.py`）、readiness check。

**Consumers**: DailyRunner preopen（判断是否能生成 plan）、DailyRunner postclose（判断是否能做 MTM / reconciliation）、model train / refresh、backtest / rolling research gate、Ops UI、monitoring、daily report。

**Grain**: `per target_date / per data_domain (raw / qlib / calendar / instrument) / per universe`

**Minimal Fields**: `target_date`, `data_domain`, `universe_id`, `latest_raw_date`, `latest_qlib_date`, `status` (ready / degraded / blocked), `blocking_scope` (train / backtest / preopen / postclose / all), `missing_rate`, `reason`, `run_id`, `generated_at`

**Invariants**:
- preopen 必须基于 T-1 已收盘数据。
- blocked 不得生成假推荐，必须阻断对应流程。
- degraded 可继续但必须写入报告。
- readiness 不能只看文件存在，还要看日期、coverage、calendar。

**Not Responsible For**:
- 不承载行情数据本身。
- 不计算 feature。
- 不生成 signal。
- 不生成订单。

### 4.2 Universe Contract

**Purpose**: 定义某个日期、某个 universe_id 下的可用股票池。IC / RankIC / 回测结果是否可比，强依赖 universe。

**Boundary**: 只定义股票池 membership 和过滤规则，不决定权重。

**Producers**: data sync pipeline、universe build job。

**Consumers**: Research、BacktestEngine、SignalEvaluator、DailyRunner preopen、UI。

**Grain**: `per universe_id / per date`

**Minimal Fields / Concepts**: `universe_id`, `date`, instrument list, `membership_source`, `tradable_flag`, suspension / ST / limit-up-down filtering policy, `generated_at`, `run_id`

**Invariants**:
- signal、label、IC、RankIC、backtest 必须记录或可追溯 universe_id。
- 不同 universe 的结果不可直接横向比较，除非明确声明。
- tradable universe 与 research universe 可以不同，但必须命名清楚。

**Not Responsible For**:
- 不负责生成 feature。
- 不决定 portfolio 权重。
- 不负责成交判断。

---

## 5. Research Input Contracts

### 5.1 Feature Contract

**Purpose**: 定义 feature set 如何被 research / model / signal 复用。Feature 是模型和信号的输入，不直接代表可交易信号。

**Boundary**: 只描述 feature 的身份、口径、覆盖和 PIT 约束，不定义 label 或模型。

**Producers**: feature engineering pipeline（`qsys/` 内特征计算模块）。

**Consumers**: model training、Prediction、signal research。

**Grain**: `per feature_set_id / per as_of_date / per instrument`

**Minimal Fields / Concepts**: `feature_set_id`, `feature_version`, `as_of_date`, `instrument`, `feature_names`, `coverage`, missing policy, PIT flag, normalization policy, `run_id`

**Invariants**:
- feature 必须避免未来数据泄露。
- PIT / 非 PIT 必须明确标注。
- `feature_set_id` 和 version 必须可追溯。
- coverage 和 missing policy 必须可观察。

**Not Responsible For**:
- 不定义 label。
- 不定义模型训练方式。
- 不直接决定买卖。

### 5.2 Label Contract

**Purpose**: 定义训练、评估和 IC / RankIC 计算所用的 label / forward return 口径。IC / RankIC 本质是 F(signal, label)，不能只有 signal contract 没有 label contract。

**Boundary**: 只定义 label 口径和约束，不生成 signal，不定义 portfolio。

**Producers**: label generation pipeline。

**Consumers**: model training、SignalEvaluator、Research Analytics (IC/RankIC)、ExperimentIndex。

**Grain**: `per label_id / per label_date / per instrument`

**Minimal Fields**: `label_id`, `label_date`, `instrument`, `label_value`, `horizon`, `shift`, `return_type`, `price_basis`, `universe_id`, `generated_at`, `run_id`

**Optional Concepts**: winsorized_value, normalized_value, industry_neutral_value, vol_adjusted_value, missing_reason

**Invariants**:
- `label_date` 必须清晰定义。
- `horizon` 和 `shift` 必须显式记录。
- 禁止未来数据泄露。
- 用于 IC / RankIC 的 label 必须能和 signal_date 对齐。
- 不同 `label_id` 的 IC / RankIC 不可直接混比，除非 horizon / shift / universe / price_basis 一致。
- label 可以缺失，但缺失必须可解释。

**Not Responsible For**:
- 不生成 signal。
- 不定义 portfolio。
- 不负责成交。

---

## 6. Model & Signal Contracts

### 6.1 Model Contract

**Purpose**: 定义一个可复用模型 artifact 的身份、训练口径和可追溯信息。

**Boundary**: 只描述模型的来源和认证状态，不直接生成订单或写 portfolio state。

**Producers**: training pipeline（`qsys/research/`、RollingResearchRunner）。

**Consumers**: Predictor、DailyRunner preopen、Research UI。

**Grain**: `per model_id / per model_version`

**Minimal Fields / Concepts**: `model_id`, `model_version`, `train_start`, `train_end`, `feature_set_id`, `label_id`, `universe_id`, `algorithm`, hyperparams summary, `artifact_path`, `approved_stage` (research / candidate / production), `run_id`

**Invariants**:
- daily ops 不认"最新模型目录"，只认显式 approved manifest。
- model 必须追溯 `feature_set` 和 `label`。
- 训练区间必须和 evaluation 区间分离。
- Candidate / Production 模型必须有固定版本，不能指向"最新"。

**Not Responsible For**:
- 不直接生成订单。
- 不直接写 portfolio state。
- 不代表 signal 一定可交易。

### 6.2 Prediction / Signal Contract

**Purpose**: 定义某个 strategy / model 在 signal_date 对 instrument 产生的预测强度或排序。

**Boundary**: Prediction 是模型原始输出；Signal 是经过标准化、组合或变换后的值。当前共用同一 contract，但语义上可区分。

**Producers**: Predictor、RollingResearchRunner、DailyRunner preopen、signal expression layer。

**Consumers**: BacktestEngine、SignalEvaluator、ExperimentIndex、allocation engine、Research UI、Ops UI。

**Grain**: `per strategy_id + signal_id + signal_date + instrument`

**Minimal Fields**: `strategy_id`, `signal_id`, `signal_date`, `instrument`, `score`, `universe_id`, `run_id`, `model_id`, `model_version`, `feature_set`, `signal_expression`, `is_oos`, `generated_at`

**Rank**: 不是必须物化的字段。可由 score + universe 在消费时派生；若物化，必须记录 ranking universe 和 `rank_direction`（ascending / descending）。

**Label relation**: 用于 evaluation 的 signal 必须能关联 `label_id` / `horizon`，可直接通过字段或通过 run_id 追溯。

**Invariants**:
- `signal_date` 是信号来源日期，不是 `execution_date`。
- daily preopen 消费的 signal 必须基于最近已收盘数据。
- 不允许把 in-sample training signal 当成 Candidate/Shadow 可交易 signal。
- signal contract 不负责下单，只表达预测强度或排序。

**Not Responsible For**:
- 不负责 label 计算。
- 不负责 portfolio allocation。
- 不负责成交。

### 6.3 Signal Expression / Combination Contract

**Purpose**: 定义多个 signal 如何被变换和组合（如 raw(signal1) + 0.2 × zscore(signal2)）。

**Boundary**: 底层可拆为 generator / transform / combination；用户研究侧优先通过 expression / config abstraction 使用。

**Producers**: research / combination layer。

**Consumers**: Predictor、allocation engine、Research UI。

**Grain**: `per expression_id`

**Minimal Concepts**: `expression_id`, `input_signals`, `transform_chain`, `weights`, normalization scope, `universe_id`, `run_id`

**Invariants**:
- 组合信号必须可追溯输入 signal。
- zscore / rank / neutralization 等变换必须记录 scope。
- 不同 transform scope 下的 signal 不可直接混比。

**Not Responsible For**:
- 不定义交易规则。
- 不写订单。
- 不写账户状态。

---

## 7. Strategy & Order Contracts

### 7.1 Strategy Allocation Contract

**Purpose**: 定义 signal 如何转成 target portfolio / target weights。

**Boundary**: Allocation 只决定目标组合，不代表真实成交。

**Producers**: allocation engine、DailyRunner preopen。

**Consumers**: Order Intent generation、backtest comparison、shadow comparison。

**Grain**: `per strategy_id + rebalance_date`

**Minimal Concepts**: `strategy_id`, `allocation_id`, `rebalance_date`, `input_signal_id`, `target_universe`, `target_weights`, `top_k`, `max_position_weight`, `turnover_limit`, `cash_buffer`, risk constraints, `run_id`

**Invariants**:
- allocation 只决定目标权重，不代表真实成交。
- 约束条件必须记录，否则回测和 daily ops 不可比。
- allocation 应可在 backtest / shadow / production 之间复用语义。

**Not Responsible For**:
- 不负责撮合。
- 不负责成交。
- 不写 ledger。

### 7.2 Order Intent Contract

**Purpose**: 定义 target portfolio / rebalance decision 生成的订单意图，即"想交易什么"。

**Boundary**: OrderIntent 是计划输入，不是成交结果，不是账户状态 SOT。

**Producers**: DailyRunner preopen / allocation engine。

**Consumers**: execution bridge (WSL→Windows)、postclose comparison、shadow simulation、UI。

**Grain**: `per execution_date + strategy_id + instrument`

**Minimal Concepts**: `order_intent_id`, `strategy_id`, `execution_date`, `instrument`, `side`, `target_qty` / `target_weight`, `estimated_price`, `reason`, `source_signal_id`, `allocation_id`, `run_id`

**Invariants**:
- order intent 必须可追溯到 signal / allocation。
- order intent 不等于 fill。
- UI 可展示 order intent，不能直接提交真实订单。

**Not Responsible For**:
- 不代表成交。
- 不修改 portfolio state。
- 不做 broker reconciliation。

---

## 8. Execution & Portfolio Contracts

### 8.1 Execution Contract

**Purpose**: 定义 order intent 如何被 simulated execution 或 broker execution 转换为 fills / execution report。

**Boundary**: Execution 不决定目标仓位，只执行 order intent。

**Producers**: Execution Backend（simulated）、broker bridge（real）。

**Consumers**: LedgerService、postclose pipeline、reconciliation、UI。

**Grain**: `per execution_id`

**Minimal Concepts**: `execution_id`, `order_intent_id`, `execution_mode` (simulated / broker / manual), `order_status` (pending / partial_fill / filled / canceled / rejected), `fill_qty`, `fill_price`, `fee`, `executed_at`, `broker_order_id`, `run_id`

**Invariants**:
- simulated execution 和 broker execution 应共享核心状态语义。
- execution 不决定 target weights，只执行 order intent。
- partial_fill / rejected / canceled 必须显式表达。
- execution result 是 postclose / ledger commit 的输入。

**Not Responsible For**:
- 不生成 signal。
- 不决定 target weights。
- 不负责长期 portfolio accounting。

### 8.2 Portfolio State / Ledger Contract

**Purpose**: 定义账户、持仓、成交、现金、快照等状态对象的语义边界。不要求每笔交易生成独立文档；交易仍由 LedgerService 以 SQLite transaction 方式提交。

**Boundary**: 只定义状态语义和写入边界，不生成交易计划，不计算 signal。

**Producers**: LedgerService、DailyRunner postclose、Execution Backend、broker reconciliation job。

**Consumers**: DailyRunner preopen（只读）、postclose reconciliation、Ops UI、risk monitor。

**Grain**: `per account_id + strategy_id + execution_date + instrument`

**Minimal Concepts**: `AccountSnapshot`, `PositionSnapshot`, `ExecutionFill`, `CashEvent`, `PortfolioSnapshot`, `ReconciliationResult`。`OrderIntent` 是计划输入，不是账户状态 SOT。

**Minimal Fields**: `account_id`, `strategy_id`, `execution_date`, `snapshot_time`, `cash`, `market_value`, `total_asset`, `instrument`, `quantity`, `available_quantity`, `cost_basis`, `last_price`, `order_id`, `fill_id`, `side`, `fill_qty`, `fill_price`, `fee`, `run_id`, `source_run_id`

**Invariants**:
- `data/trade.db` 是目标账户状态与执行流水 SOT。
- preopen 只能读取已确认的上一状态。
- postclose 才能提交 execution / portfolio snapshot。
- broker snapshot 与内部 ledger 的差异必须通过 reconciliation 暴露。
- `data/meta/real_account.db` 和 `shadow/` 是 legacy compatibility，不得扩展新依赖。
- 不允许策略 adapter 直接写生产账户状态。
- 所有状态变更必须可追溯 `run_id` 或 `source_run_id`。

**Not Responsible For**:
- 不生成交易计划。
- 不计算 signal。
- 不做 research analytics。

### 8.3 Reconciliation Contract

**Purpose**: 定义内部 ledger 与 broker / execution report / shadow state 之间如何对账。

**Boundary**: 只暴露 gap，不自动修正。

**Producers**: postclose reconciliation job。

**Consumers**: Monitoring、Ops UI、notification。

**Grain**: `per account_id + execution_date`

**Minimal Concepts**: `reconciliation_id`, `account_id`, `strategy_id`, `execution_date`, `internal_snapshot`, `external_snapshot`, `position_gap`, `cash_gap`, `missing_fill`, `status` (matched / warning / blocked), `reason`, `run_id`

**Invariants**:
- production / broker mode 下 reconciliation 是 postclose 的核心 gate。
- gap 必须显式展示，不得被 UI 隐藏。
- blocked reconciliation 阻断流程。
- reconciliation 不直接修正账户，修正必须走明确流程。

**Not Responsible For**:
- 不自动修改 ledger。
- 不自动下单。
- 不决定策略晋级。

---

## 9. Research Analytics Contract

**Purpose**: 支持 signal / label / IC / RankIC / backtest summary / experiment index 的横向查询和比较。

**Boundary**: 只读分析层，不替代 ledger，不参与 broker execution，不存真实账户状态。

**Producers**: RollingResearchRunner、SignalEvaluator、BacktestEngine、ExperimentIndex builder。

**Consumers**: Research UI（目标态）、strategy development、Candidate promotion review、model comparison、regression check。

**Storage**: DuckDB 是适合的 research analytics store。parquet / CSV / JSON index 可作为过渡介质。

**Grain**: `per experiment / per run / per signal_id + label_id + eval_window`

**Minimal Fields**: `experiment_id`, `run_id`, `strategy_id`, `signal_id`, `label_id`, `horizon`, `shift`, `universe_id`, `model_version`, `feature_set`, `eval_start`, `eval_end`, `metrics`, `artifact_paths`, `created_at`

**Metrics 第一版**: IC、RankIC、ICIR、group_return、long_short_return、turnover、max_drawdown、annual_return、cost_assumption

**Invariants**:
- IC / RankIC / ICIR 必须记录 `signal_id`、`label_id`、`horizon`、`shift`、eval_window、`universe_id`。
- 不同实验比较必须记录 cost assumption。
- 不写 production ledger。
- 不直接触发交易。
- 指标必须可追溯 `run_id` 和 `artifact_paths`。

**Not Responsible For**:
- 不负责训练模型。
- 不负责下单。
- 不负责账户状态。

---

## 10. Daily Ops / UI Read Model Contract

**Purpose**: 定义 Ops UI 和 monitoring 如何只读展示 daily 状态，避免 UI 到处读 raw 文件、ledger、shadow、experiments。

**Boundary**: 只负责展示，不写 ledger，不下单，不改策略，不绕过 DailyRunner。

**Producers**: DailyRunner、preopen pipeline、postclose pipeline、report generator、ledger readonly view。

**Consumers**: Ops UI、monitoring、notification、human operator。

**Storage**: daily report JSON（已有 `daily_ops_digest_*.json`）、daily evidence artifact。未来可演进为独立 read model。

**Grain**: `per execution_date + strategy_id + account_id + run_id`

**Minimal Fields**: `execution_date`, `strategy_id`, `stage` (candidate / production), `execution_mode` (shadow / broker / simulated / manual), `run_id`, `data_readiness_status`, `model_freshness_status`, `plan_status`, `order_intent_count`, `expected_turnover`, `account_snapshot_status`, `postclose_status`, `reconciliation_status`, `blocking`, `reason`, `artifact_paths`, `generated_at`

**Invariants**:
- UI read model **只读**。
- UI 不写 ledger，不下单，不改策略。
- UI 不绕过 DailyRunner。
- UI 不直接依赖 legacy shadow files 作为长期接口。
- blocking 状态必须清晰，阻止性异常必须显式传递。
- 所有展示项必须能回跳 artifact path 或 `run_id`。

**Not Responsible For**:
- 不负责实际交易。
- 不负责修正账户。
- 不负责改变策略配置。

---

## 11. Promotion Evidence Contract

**Purpose**: 定义一个策略从 Research 进入 Candidate/Shadow，再进入 Production 前需要保留的最小证据和审计轨迹。

**Boundary**: 只定义晋级所需的证据集合，不自动晋级，不替代人工判断。

**Producers**: candidate promotion pipeline、research report generator。

**Consumers**: Production approval process、audit。

**Grain**: `per candidate / per strategy_id`

**Minimal Concepts**: `candidate_id`, `strategy_id`, `research_run_ids`, signal_eval_summary, backtest_summary, cost_assumption, baseline_comparison, shadow_run_summary (如有), risk_notes, artifact_paths, `approval_status`, `approved_by`, `approved_at`

**Invariants**:
- Research 结果不能直接进入 Production。
- Candidate/Shadow 必须有可复现的 OOS signal evaluation 和 backtest evidence。
- Production approval 必须有人类确认。
- 晋级、上线、回滚都必须留下最小审计轨迹。

**Not Responsible For**:
- 不自动晋级。
- 不自动下单。
- 不替代人工判断。

---

## 12. Global Invariants

以下规则跨所有 contract：

- **UI / monitoring 只读**——不写 ledger，不下单，不改策略。
- **Research artifact 不能直接进入 Production**。
- **Candidate/Shadow 必须可追溯** signal、label、model、feature、backtest 和 daily evidence。
- **Production 不认"最新模型目录"**，只认显式 approved manifest。
- **`data/meta/real_account.db` 和 `shadow/` 只作为 legacy compatibility**，不扩展新依赖。
- **`data/trade.db` 是目标 Account State / Execution Ledger SOT**。
- **所有跨阶段对象必须可追溯 `run_id`**。
- **不同 universe / label horizon / cost assumption 下的指标不可直接混比**。
- **Contract-backed runtime artifacts** 不得随意删除、重命名或改变语义。清理前必须确认 consumer 和回滚方式。
- **修改 contract**：普通字段走 PR review；改变接口语义、生命周期或跨模块边界时应补 ADR。

---

## 13. Legacy Contract Documents

旧的 `docs/contracts/*` 文档已被合并或降级为 legacy reference。文件本身已删除。

| Legacy file | Status | Migrated / Replaced by |
|-------------|--------|------------------------|
| `docs/contracts/agent-operating-contract.md` | deprecated | `AGENTS.md` |
| `docs/contracts/artifact-contract.md` | deprecated | `docs/CONTRACTS.md`, `docs/schema/`, `docs/adr/007-artifact-contract.md` |
| `docs/contracts/data-interface.md` | deprecated | `docs/CONTRACTS.md` Data / Feature / Label / Readiness sections |
| `docs/contracts/data-readiness-contract.md` | deprecated | `docs/CONTRACTS.md` §4.1 Data Readiness Contract |
| `docs/contracts/daily-ops-read-model-contract.md` | deprecated | `docs/CONTRACTS.md` §10 Daily Ops / UI Read Model |
| `docs/contracts/no-lookahead-checklist.md` | deprecated | `docs/ops/RESEARCH_STRATEGY_SOP.md` Data Leakage Checklist |
| `docs/contracts/portfolio-state-contract.md` | deprecated | `docs/CONTRACTS.md` §8.2 Portfolio State / Ledger |
| `docs/contracts/research-analytics-contract.md` | deprecated | `docs/CONTRACTS.md` §9 Research Analytics |
| `docs/contracts/research-artifact-contract.md` | deprecated | `docs/CONTRACTS.md` Research Analytics / Signal / Label / Promotion Evidence |
| `docs/contracts/signal-contract.md` | deprecated | `docs/CONTRACTS.md` §6.2 Prediction / Signal |
| `docs/contracts/strategy-boundary-contract.md` | deprecated | `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, `AGENTS.md` |
| `docs/contracts/strategy-interface.md` | deprecated | `docs/CONTRACTS.md`, `docs/ops/RESEARCH_STRATEGY_SOP.md`, actual `StrategyCandidate` code |
| `docs/contracts/time-semantics.md` | deprecated | `docs/CONTRACTS.md` Invariants, `docs/ops/RESEARCH_STRATEGY_SOP.md` |

这些 legacy contract 文件不再作为 current truth。若未来发现旧文档中仍有未迁移的重要规则，应通过 PR 显式迁移，而不是继续引用 `docs/contracts/*`。


## B. Label Contract

### Purpose

Label 是从原始行情数据到模型训练目标的标准化 artifact。同一个 label_id 下的数据是唯一的 current truth，无论 consumer 是 DNN、LightGBM 还是 IC evaluation。

### Producer / Consumer

| 角色 | 组件 | 操作 |
|------|------|------|
| Producer | `scripts/research/compute_labels.py` | 创建 label parquet + manifest |
| Consumer | `DnnMultitaskGenerator` | 训练时从 LabelStore 读取 |
| Consumer | `LightGBMAlphaV1Generator` | 训练时从 LabelStore 读取 |
| Consumer | `rolling_runner.evaluate()` | IC 评估时从 LabelStore 读取 |

### Grain

一行 = 一个（trade_date, instrument）的 label_value。

### Required Fields

| 字段 | 类型 | 描述 |
|------|------|------|
| `trade_date` | str | 交易日 (YYYY-MM-DD) |
| `instrument` | str | 股票代码 |
| `label_id` | str | Label 标识符（见下面 Label IDs） |
| `horizon` | int | 预测天数 |
| `label_value` | float | 归一化后的值 |

### Label IDs

All forward return labels use **adjusted close** (`$close * $factor`) as the price basis.
`$close` is the raw (unadjusted) close from the Tushare ``daily`` API.
`$factor` is the cumulative adjustment factor from the Tushare ``adj_factor`` API.
The ``raw`` suffix means **no normalization** — it does **not** mean raw (unadjusted) price.

| label_id | horizon | formula (price_basis=adjusted_close) | normalization | clip |
|----------|---------|-|-|-|
| `fwd_ret_5d_xsz_clip3` | 5 | shift(-5) / adjusted_close - 1 → cs_zscore | per-date cs_zscore | [-3, 3] |
| `fwd_ret_5d_cs_zscore_clip3` | 5 | shift(-5) / adjusted_close - 1 → cs_zscore | per-date cs_zscore | [-3, 3] |
| `fwd_ret_5d_raw` | 5 | shift(-5) / adjusted_close - 1 | none | none |
| `fwd_ret_10d_xsz_clip3` | 10 | shift(-10) / adjusted_close - 1 → cs_zscore | per-date cs_zscore | [-3, 3] |
| `fwd_ret_10d_cs_zscore_clip3` | 10 | shift(-10) / adjusted_close - 1 → cs_zscore | per-date cs_zscore | [-3, 3] |
| `fwd_ret_10d_raw` | 10 | shift(-10) / adjusted_close - 1 | none | none |
| `fwd_ret_20d_xsz_clip3` | 20 | shift(-20) / adjusted_close - 1 → cs_zscore | per-date cs_zscore | [-3, 3] |
| `fwd_ret_20d_raw` | 20 | shift(-20) / adjusted_close - 1 | none | none |
| `fwd_ret_60d_raw` | 60 | shift(-60) / adjusted_close - 1 | none | none |
| `fwd_ret_120d_raw` | 120 | shift(-120) / adjusted_close - 1 | none | none |
| `fwd_ret_180d_raw` | 180 | shift(-180) / adjusted_close - 1 | none | none |

Generator 默认消费 `xsz_clip3` / `cs_zscore_clip3` 系列。`raw` 系列用于 research generator 的直接训练（generator 内部做 zscore 归一化）。

### Manifest Semantics

每个 label 目录下 `manifest.json` 包含以下字段：

```json
{
  "artifact_type": "label",
  "label_id": "fwd_ret_5d_xsz_clip3",
  "row_count": 200000,
  "columns": ["trade_date", "instrument", "label_id", "horizon", "label_value"],
  "horizon": 5,
  "universe": "csi300",
  "prediction_start": "2023-06-01",
  "prediction_end": "2026-06-01",
  "formula": "shift(-5) / close - 1, then per-date cs_zscore",
  "normalization": "cross-sectional zscore",
  "clip": 3.0,
  "coverage": 0.95,
  "created_at": "2026-05-31T12:00:00+00:00",
  "git_commit": "abc1234"
}
```

### Invariants

- 同 label_id 的数据在同一 universe 内 **只存一份**，不按模型或实验重复存储。
- label_value 是 **已归一化的值**（xsz_clip3 系列为每日横截面 zscore + clip）。generator 不再做二次归一化。
- Label 数据一旦发布，**应被视为只读**。如需修改 label 定义，应使用新的 label_id。
- 每次覆盖写入（`--overwrite`）会更新数据文件和 manifest，但不保证前向兼容。

### Current Usage

| label_id | 使用方 |
|----------|--------|
| `fwd_ret_5d_xsz_clip3` | DNN multitask (score_5d target), LightGBM alpha_v1 (5d target), IC evaluation |
| `fwd_ret_20d_xsz_clip3` | DNN multitask (score_20d target), LightGBM alpha_v1 (20d target), IC evaluation |

### Failure Behavior

- 当 `rolling_runner.run()` 检测到 config.labels 中某个 label_id 不存在时：**fail fast**，提示先运行 `compute_labels.py`。
- 当 label 的 universe 与 config 不符时：fail fast。
- 当 label 的日期覆盖不足时：fail fast。
- Generator 在训练时若 label_value 全为 NaN：**raise ValueError**。

### 计算脚本

```bash
# 生成 CSI300 2023-06 → 2026-06 的全部 label
python scripts/research/compute_labels.py \\
    --universe csi300 --start 2023-06-01 --end 2026-06-01 \\
    --horizons 5 20 --overwrite
```
