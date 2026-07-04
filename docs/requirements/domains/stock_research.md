# Domain: Stock Fundamental Research

## Domain Scope
基本面/消息面 agent 研究：财报 PDF 解析、公告新闻分析、行业信息参考、输出结构化 stock research memo。
不包含：自动交易决策、替代量化信号、量化特征计算。

## UC_STOCK_FUNDAMENTAL_RESEARCH

### Status
draft

### Source
**新增 use case**，不在 `docs/USE_CASES.md` 现有 UC 编号中。设计留 TODO。

### User Goal
利用 LLM agent 对 SysQ 候选池中的股票进行基本面/消息面研究，输出结构化股票研究 memo，辅助人工判断。

这个 use case **不直接替代量化信号**。它负责解释和验证候选股票，而不是直接给交易指令。

### Scope
包含：
- 读取 SysQ 候选股票列表或单票
- 财报 PDF 解析与核心指标提取
- 公告/新闻/消息面分析
- 行业信息参考
- 输出结构化 stock research memo
- prompt template 管理

不包含：
- 自动交易决策
- 替代量化信号流程
- 实时监控

### Inputs
- SysQ 候选股票列表
- 财报 PDF、公告文本、新闻摘要、行业信息
- 人工补充假设

### Outputs
- 结构化 stock research memo（markdown）
- 结论：watch / pass / candidate

### Canonical Entrypoints
TBD — 当前是 prompt-based agent workflow，没有独立 CLI entrypoint。

### Prompt Templates
- `prompts/stock_research/final_stock_memo.md`

### Key Artifacts
- `research_memos/{ts_code}/{date}/stock_research_memo.md`（`.gitignore` 已配置）

### Required Checks
- TBD: stock research memo schema check
- TBD: prompt output format validation

### Owner Agent
stock_research_agent

### Allowed Paths
- `prompts/stock_research/`
- `docs/requirements/`
- `research_memos/`

### Forbidden Paths
- `qsys/ledger/`
- `qsys/backtest/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/signal/`（读可以，改不行）
- `qsys/ops/daily_runner.py`
- `deploy/`

### Open Questions
- 财报 PDF 的获取路径是什么（tushare 财报接口？手动下载？）
