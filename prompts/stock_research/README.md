# Stock Research Prompts

> 本目录存放股票基本面/消息面研究的 prompt template。
> 对应 use case: `UC_STOCK_FUNDAMENTAL_RESEARCH`（`docs/requirements/usecases/uc_stock_fundamental_research.md`）。

## 文件说明

- `final_stock_memo.md` — 最终输出格式模板，agent 按此结构输出结构化的股票研究 memo。
- （远期）`earnings_analysis.md` — 财报分析专用 prompt。
- （远期）`news_impact.md` — 消息面影响分析 prompt。

## 使用方式

当前阶段为 prompt-based agent 工作流：
1. main_agent 根据候选清单或用户请求，决定需要研究哪只股票。
2. stock_research_agent 收集输入材料（财报 PDF、公告、新闻）。
3. 按 `final_stock_memo.md` 模板输出结构化 memo。
4. memo 保存到 `research_memos/{ts_code}/{date}/stock_research_memo.md`。

## 制原则

- 每个重要判断必须标注来源（财报 page、公告链接）。
- 不编造数据。找不到的数据显式标注 `[NOT FOUND]`。
- 结论保守：watch（可观察）/ pass（不关注）/ candidate（值得进一步考虑），不使用强烈买入建议。
