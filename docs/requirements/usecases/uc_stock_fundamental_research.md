# UC_STOCK_FUNDAMENTAL_RESEARCH: Stock Fundamental / News Research

## Status
draft

## User Goal
利用 LLM agent 对 SysQ 候选池中的股票进行基本面/消息面研究：阅读财报 PDF、公告、新闻、行业信息，输出结构化股票研究 memo，辅助人工判断候选股票是否值得关注。

这个 use case **不直接替代量化信号**。它负责**解释和验证**候选股票，而不是直接给交易指令。

## Scope
包含：
- 读取 SysQ 输出的候选股票列表或单票
- 财报 PDF 解析与核心指标提取
- 公告/新闻/消息面分析
- 行业信息参考
- 输出结构化 stock research memo
- prompt template 管理

不包含：
- 自动交易决策
- 替代量化信号流程
- 实时监控（非定时任务，按需执行）
- 量化特征计算（复用 UC_RESEARCH_BACKTEST 结果）

## Inputs
- SysQ 候选股票列表（来自 signal basket 或 candidate list）
- 财报 PDF（从公开渠道获取）
- 公告文本
- 新闻摘要
- 行业信息
- 人工补充假设

## Outputs
- 结构化 stock research memo（markdown）
- 结论：watch / pass / candidate

## Stock Research Memo 必须包含以下字段

```
# Stock Research Memo — {ts_code}

## 公司一句话业务
{一句话描述公司主营业务}

## SysQ 量化信号来源
- 信号 ID 和日期
- 信号得分
- 在该策略中的排名
- 信号变化趋势（如有）

## 财报核心变化
### 收入
{同比/环比变化，主要驱动因素}

### 利润
{净利润、毛利率、净利率变化}

### 现金流
{经营现金流、自由现金流趋势}

## 估值与预期
- PE / PB / 行业对比
- 分析师一致预期（如有）
- 估值分位

## 消息面催化
{近期公告、新闻、政策信息}

## 主要风险
{业务风险、估值风险、流动性风险等}

## 与量化信号的一致性
量化信号与基本面判断的方向是否一致？若不一致，可能的原因。
例如：量化信号正面但基本面恶化 → 可能是因为动量延续而非价值改善。

## 结论
watch / pass / candidate

## 关键判断来源标注
每个重要判断标注信息来源（财报 page、公告链接、新闻出处）。
```

## Canonical Entrypoints
TBD — 当前为 prompt-based agent 工作流，没有 Python entrypoint。
远期可考虑 `scripts/research/run_stock_research.py --ts-code <code>`。

## Key Artifacts
- `research_memos/{ts_code}/{date}/stock_research_memo.md`
- `prompts/stock_research/final_stock_memo.md` — prompt template

## Required Checks
- TBD: stock research memo schema check
- TBD: prompt output format validation

## Owner Agent
stock_research_agent

## Allowed Paths
- `prompts/stock_research/`
- `docs/requirements/`
- `research_memos/`

## Forbidden Paths
- `qsys/ledger/`
- `qsys/backtest/`
- `qsys/trader/`
- `qsys/broker/`
- `qsys/signal/`（读 signal data 可以，改不行）
- `qsys/ops/daily_runner.py`
- `deploy/`

## Open Questions
- 是否需要独立 CLI 入口？还是通过 agent shell 直接使用 prompt template 即可？
- 财报 PDF 的获取路径是什么（tushare 财报接口？手动下载？）
- research_memo 目录是否应该 gitignore（避免大文件污染仓库）？
