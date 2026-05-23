# ADR 007: Artifact Contract

**状态**：已采纳 (Accepted)
**日期**：2026-05-23

## 背景 (Context)

SysQ 中多个流程产生和消费各类产物（预测、订单、成交、快照等）。当前这些产物的格式和字段在不同策略、不同脚本之间不完全一致，导致：

1. **难以跨策略比较**：不同策略的输出产物字段定义不同。
2. **UI 消费困难**：前端工具需要稳定的字段结构才能正确渲染。
3. **Agent 理解成本高**：AI 助手在处理不同策略产物时需要分别了解各自的格式约定。
4. **自动验证困难**：缺乏统一的 schema 约束，无法自动检查产物完整性。

需要一套稳定的 artifact contract，所有策略在 Research 阶段之上的输出必须遵循。

## 决策 (Decision)

我们决定定义以下六种标准 artifact 类型。所有策略晋升至 Candidate 及以上时，必须按此契约输出。

### 通用规则

- 所有策略，无论内部模型类型如何，晋升到 Candidate 以上时必须输出这些契约。
- 缺失字段必须显式标记为 `null` / `not_available` / `not_applicable`。不得静默省略必填字段。
- 文件命名和目录约定必须足够稳定，供 UI 和 review agent 使用。
- CSV/JSON 导出是 artifacts 而非账户状态的事实标准。SQLite ledger 仍是账户/现金/持仓/成交的事实标准。

---

## Artifact 定义

### 1. SignalArtifact

**目的**：记录策略在特定交易日的信号输出——每个股票的分数、排名、归一化分数和目标权重。

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日 |
| `strategy_id` | STRING | 策略唯一标识 |
| `candidate_id` | STRING | 候选版本标识 |
| `model_version` | STRING | 模型版本 |
| `signal_version` | STRING | 信号版本 |
| `data_cutoff` | DATE | 数据截止日期 |
| `instrument` | STRING | 股票代码 |
| `score` | FLOAT | 综合评分（已 blend） |
| `rank` | INTEGER | 当日全市场排名 |
| `raw_prediction` | FLOAT | 模型原始预测值 |
| `normalized_score` | FLOAT | 归一化后分数 |
| `target_weight_raw` | FLOAT | Cap 前的原始目标权重 |
| `created_at` | TIMESTAMP | 产物生成时间 |
| `config_hash` | STRING | 配置哈希 |
| `feature_schema_version` | STRING | 特征 schema 版本 |

**可选字段**：`universe`、`top_n`、`buffer_hold`、`buffer_buy`、`single_stock_cap`

**文件命名约定**：`signals_{trade_date}_{strategy_id}.csv`

---

### 2. OrderIntentArtifact

**目的**：记录从信号到交易计划的转换结果——每个股票的目标权重和期望交易量。

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日 |
| `strategy_id` | STRING | 策略标识 |
| `account_id` | STRING | 目标账户 |
| `instrument` | STRING | 股票代码 |
| `side` | STRING | BUY / SELL / HOLD |
| `target_weight` | FLOAT | 目标权重 |
| `current_weight` | FLOAT | 当前权重 |
| `target_quantity` | INTEGER | 目标持仓量 |
| `current_quantity` | INTEGER | 当前持仓量 |
| `delta_quantity` | INTEGER | 需调整数量（正=买，负=卖） |
| `reason` | STRING | 调整原因（如 rebalance_to_target_weight） |
| `constraints` | JSON | 约束条件（如价格限制、最小交易单位） |
| `created_at` | TIMESTAMP | 产物生成时间 |

**可选字段**：`limit_price`、`order_type`、`status`

**文件命名约定**：`order_intents_{trade_date}_{strategy_id}.csv`

---

### 3. ExecutionArtifact

**目的**：记录实际模拟或真实执行结果——每个订单的成交明细。

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日 |
| `run_id` | STRING | 运行标识 |
| `strategy_id` | STRING | 策略标识 |
| `account_id` | STRING | 账户标识 |
| `order_id` | STRING | 订单 ID |
| `fill_id` | STRING | 成交 ID |
| `instrument` | STRING | 股票代码 |
| `side` | STRING | 买卖方向 |
| `quantity` | INTEGER | 成交数量 |
| `price` | FLOAT | 成交价格 |
| `commission` | FLOAT | 手续费 |
| `stamp_tax` | FLOAT | 印花税 |
| `slippage` | FLOAT | 滑点成本 |
| `status` | STRING | filled / partial / pending / canceled |
| `reason` | STRING | 执行说明 |
| `created_at` | TIMESTAMP | 产物生成时间 |

**可选字段**：`gross_amount`、`net_amount`、`source`

**文件命名约定**：`executions_{run_id}.csv`

---

### 4. PortfolioSnapshot

**目的**：记录特定时间点的组合快照，用于 MTM 和 PnL 归因。

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日 |
| `account_id` | STRING | 账户标识 |
| `strategy_id` | STRING | 策略标识 |
| `cash` | FLOAT | 现金余额 |
| `market_value` | FLOAT | 市值合计 |
| `total_asset` | FLOAT | 总资产 = cash + market_value |
| `daily_pnl` | FLOAT | 当日盈亏 |
| `daily_return` | FLOAT | 当日收益率 |
| `position_count` | INTEGER | 持仓数量 |
| `turnover` | FLOAT | 当日换手率 |
| `created_at` | TIMESTAMP | 快照生成时间 |

**可选字段**：`cumulative_pnl`、`cumulative_pnl_pct`、`initial_capital`、`details`（持仓明细）

**文件命名约定**：`snapshot_{trade_date}_{account_id}.json`

---

### 5. CandidateReport

**目的**：记录候选策略从研究晋升到候选的完整评估报告。

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `candidate_id` | STRING | 候选标识 |
| `strategy_id` | STRING | 所属策略 |
| `research_id` | STRING | 来源研究 ID |
| `hypothesis` | TEXT | 核心假设 |
| `feature_set` | STRING | 特征集 |
| `label` | STRING | 标签定义 |
| `model` | STRING | 模型描述 |
| `train_window` | STRING | 训练窗口 |
| `validation_result` | JSON | 验证结果 |
| `backtest_result` | JSON | 回测结果 |
| `risk_summary` | TEXT | 风险摘要 |
| `known_issues` | TEXT | 已知问题 |
| `promotion_decision` | STRING | 晋升决定 |
| `next_action` | STRING | 下一步操作 |

**文件命名约定**：`candidate_report_{candidate_id}.md`

---

### 6. RunManifest

**目的**：记录每次运行的元信息，实现可追溯性和审计。

**必填字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `run_id` | STRING | 运行标识 |
| `trade_date` | DATE | 交易日 |
| `stage` | STRING | 运行阶段（preopen / postclose / train / backtest） |
| `strategy_id` | STRING | 策略标识 |
| `account_id` | STRING | 账户标识 |
| `git_commit` | STRING | 代码版本 |
| `config_hash` | STRING | 配置哈希 |
| `data_version` | STRING | 数据版本 |
| `model_version` | STRING | 模型版本 |
| `signal_version` | STRING | 信号版本 |
| `input_artifacts` | JSON | 输入产物路径列表 |
| `output_artifacts` | JSON | 输出产物路径列表 |
| `status` | STRING | started / completed / failed |
| `error` | TEXT | 错误信息 |
| `created_at` | TIMESTAMP | 创建时间 |
| `updated_at` | TIMESTAMP | 更新时间 |

**文件命名约定**：`manifest_{run_id}.json`

---

## 影响 (Consequences)

### 正面影响

- **跨策略可比较**：产物格式一致，便于比较不同策略的信号质量、执行效果。
- **UI 可消费**：前端工具可以直接读取标准格式的产物。
- **自动检查**：可基于契约编写 schema 校验。
- **Agent 效率**：AI 助手无需对每个策略重新学习产物格式。

### 代价

- **迁移成本**：现有策略需要逐步适配新契约格式。
- **字段冗余**：某些策略可能不需要所有字段。

### 缓解措施

- 现有策略（如 alpha_v1）逐步适配，不要求一次性完全迁移。
- 可选字段提供了灵活性，策略可根据自身情况填写。
- Candidate 阶段才强制要求完整契约，Research 阶段不受限。

## 后续

- [ ] 编写 artifact validators（可选 stage 检查）。
- [ ] 在生产物料的输出点集成契约格式。
- [ ] 将现有 `plan_meta.json` / `execution_summary.json` 等产物逐步迁移到新契约。
- [ ] 考虑引入 JSON Schema 或 Pydantic 模型作正式 schema 校验（Phase 2 候选）。
