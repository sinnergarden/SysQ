# Domain: Stock Fundamental Signal Audit

## Domain Scope

对已经由严格 PIT CSI1800 S180 模型筛选出的 Top10 做基本面信号可靠性审计。财报、公告、新闻和行业信息用于识别模型失效风险，不重新构建投资决策。

不包含：重新选股/排序/过滤、自动交易、目标价或合理价值判断、量化特征计算、修改信号或模型。

## UC_STOCK_FUNDAMENTAL_RESEARCH

### Status

draft

### Source

新增 use case；与 UC_TOP10_SIGNAL_RUN 解耦，作为其只读下游消费者。

### User Goal

判断 S180 Top10 的模型逻辑是否得到基本面支持，识别重大风险和未来 180 日负面催化，区分真实盈利趋势、短期市场情绪与基本面陷阱。模型排序仍是主要 alpha 来源。

### Scope

包含：

- 读取并验证 immutable `top10_run.json` 与 `candidate_run.json`
- 保留模型原始排名与 raw prediction
- 财报 PDF、公告、新闻和行业资料审计
- 收入/利润/现金流/资产负债/应收/存货/非经常损益检查
- 估值预期透支检查（估值仅作背景，不构成单独否决）
- 输出结构化个股 memo 与 Top10 批次审计 artifact

不包含：

- 重新选股、重排、删除或过滤 Top10
- 买卖建议、目标价、合理价格或仓位建议
- 用长期价值投资逻辑替代量化趋势逻辑
- 修改 signal、feature、model、strategy 或交易链路

### Inputs

- 已通过 `check_top10_signal_artifact.py` 的 `top10_run.json`
- 对应 `candidate_run.json` 及其 hash
- run identity、signal/data/decision/execution dates、model bundle hash
- 财报、公告、新闻与行业材料及其发布日期
- audit as-of date

### Time Semantics

- `model_known`: `published_date <= data_date`
- `audit_only`: `data_date < published_date <= audit_as_of_date`
- `published_date > audit_as_of_date` 禁止使用
- `audit_only` 证据必须显式隔离，不得冒充模型输入；实时审计允许其影响信号可信度判断
- `audit_as_of_date` 不得晚于本次 Top10 的 `execution_date` 或真实当前日期

### Outputs

- `research_memos/{ts_code}/{signal_date}/stock_research_memo.md`
- `research_memos/s180_top10/{signal_date}/{run_identity}/top10_fundamental_audit.md`
- `research_memos/s180_top10/{signal_date}/{run_identity}/top10_fundamental_audit.json`
- 每只股票必须给出：
  - `fundamental_support`: supported / mixed / conflicted / insufficient_evidence
  - `signal_confidence`: high / medium / low / unknown
  - `risk_level`: low / medium / high / critical / unknown
  - `signal_impact`: none / monitor / reduce_confidence / strongly_challenge
  - `major_risks` 与带发布日期的 evidence
  - 四项 `financial_quality_checks`：盈利质量、现金流质量、资产负债质量、会计/非经常损益
  - `challenge_basis`：冲突判断只能基于财务冲突、现金流、资产负债、治理、负面催化或无业绩主题，不允许估值单独否决
  - `post_signal_risks`：隔离 data date 后新出现的风险

这些字段仅表示信号可靠性，不是交易或仓位指令。

### Canonical Entrypoints

Prompt-based agent workflow；必须由 Top10 artifact 驱动，不接受手工改写的股票清单。

### Prompt Templates

- `prompts/stock_research/final_stock_memo.md`

### Required Checks

- `harness/checks/check_top10_signal_artifact.py`
- `harness/checks/check_stock_research_memo.py`

### Owner Agent

stock_research_agent

### Allowed Paths

- `.claude/skills/sysq-stock-research/`
- `prompts/stock_research/`
- `docs/requirements/`
- `harness/checks/check_stock_research_memo.py`
- `tests/harness/`
- `research_memos/`
- read-only `outputs/` and `data/research/top10/`

### Forbidden Paths

- `qsys/ledger/`
- `qsys/backtest/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/signal/`（只读）
- `qsys/model/`（只读）
- `qsys/ops/daily_runner.py`
- `deploy/`

### Failure Contract

缺少可靠材料时输出 `insufficient_evidence` / `unknown`。checker 失败时修复审计 artifact；不得通过更改 Top10、模型分数或排名绕过。
