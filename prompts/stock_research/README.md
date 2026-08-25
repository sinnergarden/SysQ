# Stock Fundamental Signal Audit Prompts

本目录存放 Qsys S180 Top10 基本面信号可靠性审计模板，对应 `UC_STOCK_FUNDAMENTAL_RESEARCH`（`docs/requirements/domains/stock_research.md`）。

## 文件说明

- `final_stock_memo.md`：个股基本面信号审计 memo 模板。
- （远期）`earnings_analysis.md`：财报抽取专用 prompt。
- （远期）`news_impact.md`：消息面影响分析 prompt。

## 使用方式

1. main agent 提供已通过校验的 immutable `top10_run.json`，不得手工改写股票清单。
2. stock research agent 原样保留模型 rank 和 raw prediction。
3. agent 收集截至 audit as-of date 可得的财报、公告和新闻，并区分 `model_known` / `audit_only`。
4. 按 `final_stock_memo.md` 输出每只股票 memo；批次另存机器可检的 JSON 与 Markdown 汇总。
5. 运行 `harness/checks/check_stock_research_memo.py` 验证 Top10 provenance、字段和证据日期。

## 原则

- 模型排序仍是主要 alpha 来源；审计只降低极端错误，不产生第二套选股。
- 重点寻找模型失效证据，不寻找“不喜欢的股票”。
- 高估值仅作预期背景，不能单独否定模型信号。
- 每项重要判断必须有发布日期与来源；找不到时标注 `insufficient_evidence`。
- 禁止买卖建议、目标价、合理价格，以及 watch / pass / candidate 结论。
