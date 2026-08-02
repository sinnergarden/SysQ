# sysq-stock-research

## Purpose
A 股基本面 / 消息面研究 skill：基于财务、公告、新闻生成个股研究 memo（`stock_research_memo.md`），供候选评估参考（UC_STOCK_FUNDAMENTAL_RESEARCH）。

## Inputs
- ts_code
- date
- 数据源（财务、公告、新闻）

## Required reads
- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- UC_STOCK_FUNDAMENTAL_RESEARCH 定义（`docs/requirements/domains/stock_research.md`）
- 输出模板 `prompts/stock_research/final_stock_memo.md`

## Workflow
1. 归类 UC_STOCK_FUNDAMENTAL_RESEARCH。
2. 收集基本面 / 消息面数据（财务、公告、新闻、行业）。
3. 按模板组织研究 memo：公司概况、财务分析、成长性、风险、消息面、结论。
4. 写入 `research_memos/{ts_code}/{date}/stock_research_memo.md`。
5. 标注数据 as-of 日期与不确定度，不做过度确定表述。

## 规则
- 输出必须是结构化 memo（遵循模板），非自由文本。
- 标注每个关键数据的截止日期，避免使用后见之明 / 未来信息。
- 基本面研究是候选评估的参考信息，不直接作为交易信号。

## Never
- 使用未来数据 / 后见之明
- 把研究 memo 当成可执行交易信号
- 写 broker / trader / ledger / signal / daily_runner

## Required checks
```bash
python harness/checks/check_usecase_registry.py
# memo 产出校验：TBD（stock research memo schema check）
```
