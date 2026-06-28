Qsys 财报叙事信息收集与 LLM 抽取 Agent 模版

0. 任务目标

本任务不是让 LLM 主观判断股票好坏，而是把历史财报、季报、半年报、年报、业绩预告、业绩快报、重大公告中的文本信息，抽取为可审计、可回测、可 PIT 对齐的结构化特征。

目标用途：

* 补充现有 v3a+liquidity 模型缺失的“基本面确认 / 新产品 / 新市场 / 供需矛盾 / 管理层展望”信息。
* 改善两类错误：
    1. 低分但未来大涨：模型没看到产业反转、产品突破、订单爆发、供需紧张。
    2. 高分但未来弱/亏：高量高位高估值，但缺少业绩和订单确认。

核心原则：

* 只抽取事实和管理层表述，不直接预测股价。
* 所有字段必须带来源、公告日、证据片段和置信度。
* 所有数据必须可按 ann_date <= trade_date 做 PIT merge。
* 不能使用报告期 end_date 直接回填。
* 不做自由文本 alpha，不做黑箱 LLM 看好/看空。

⸻

1. 数据范围

1.1 股票范围

默认：

universe = CSI800 / 当前研究股票池
period = 2020-01-01 ~ 2025-12-31

如做 PoC，先选：

sample_size = 100 stocks
documents = 年报 + 半年报 + 三季报 + 一季报 + 业绩预告 + 业绩快报 + 重大公告

1.2 文档类型

优先级从高到低：

1. 年报
2. 半年报
3. 一季报 / 三季报
4. 业绩预告
5. 业绩快报
6. 投资者关系活动记录 / 调研纪要
7. 重大合同 / 中标 / 订单公告
8. 产能投产 / 项目建设公告
9. 产品认证 / 注册 / 获批公告
10. 并购 / 定增 / 股权激励 / 减持 / 处罚 / 诉讼公告

优先使用交易所、巨潮、Tushare、已有公告源。若有 HTML/text，优先使用 HTML/text；PDF 作为备选。尽量避免 OCR。

⸻

2. 数据落库要求

每份文档落库为：

document_id
ts_code
stock_name
document_type
title
ann_date
report_period
source_url
source_file_path
text_hash
raw_text_path
parsed_text_path
fetch_time

要求：

* ann_date 必须存在。
* 若 ann_date 缺失，文档不能用于训练，只能进入人工检查队列。
* text_hash 用于去重和版本追踪。
* 如果同一公告有修订版，保留修订关系。

⸻

3. LLM 抽取总原则

LLM 只做信息抽取，不做投资建议。

每个字段必须输出：

{
  "value": "...",
  "confidence": 0.0,
  "evidence_quote": "...",
  "section": "...",
  "page": null,
  "source_ann_date": "YYYYMMDD",
  "source_document_id": "..."
}

若没有明确证据，输出：

{
  "value": null,
  "confidence": 0.0,
  "evidence_quote": "",
  "section": "",
  "missing_reason": "not_disclosed"
}

禁止根据常识脑补。

⸻

4. 核心 Schema：fundamental_catalyst_schema

用于抽取“基本面催化”和“成长确认”。

{
  "new_product_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "是否出现新产品、新型号、新技术、新服务。0=无，1=提及但不明确，2=明确推进，3=已商业化/放量"
  },
  "new_market_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "是否进入新客户、新行业、新区域、海外市场。0=无，1=提及，2=明确拓展，3=已有收入/订单贡献"
  },
  "customer_breakthrough_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "是否获得关键客户、头部客户、认证、定点、导入。"
  },
  "order_growth_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "订单、在手订单、合同、需求增长情况。"
  },
  "capacity_expansion_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "产能扩张、产线投产、项目达产、募投项目推进。"
  },
  "main_business_improvement_signal": {
    "type": "integer",
    "range": [-2, 3],
    "meaning": "主营业务是否改善。负数表示恶化，正数表示改善。"
  },
  "non_recurring_profit_risk": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "利润是否主要来自非经常性损益、资产处置、投资收益、补贴。"
  },
  "management_outlook_score": {
    "type": "integer",
    "range": [-2, 2],
    "meaning": "管理层对未来经营展望。-2明显悲观，0中性，+2明显乐观。"
  },
  "risk_warning_score": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "经营风险、需求下滑、价格下跌、客户流失、产能过剩等风险。"
  }
}

⸻

5. 核心 Schema：supply_demand_schema

用于抽取“供需矛盾”和“景气度”。

{
  "demand_shortage_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "是否出现供不应求、订单饱满、客户排队、产能瓶颈。"
  },
  "price_increase_signal": {
    "type": "integer",
    "range": [-2, 3],
    "meaning": "产品售价趋势。负数为降价，正数为涨价。"
  },
  "inventory_pressure_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "库存压力。0无压力，3明显库存积压。"
  },
  "gross_margin_driver": {
    "type": "string",
    "enum": ["price_up", "cost_down", "mix_upgrade", "scale_effect", "price_down", "cost_up", "competition", "unknown", "not_disclosed"]
  },
  "industry_cycle_position": {
    "type": "string",
    "enum": ["early_recovery", "acceleration", "boom", "late_cycle", "downturn", "unknown"]
  },
  "capacity_bottleneck_signal": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "是否存在产能瓶颈或产能利用率高位。"
  },
  "order_visibility_score": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "未来订单能见度。0无，3明确披露长期订单/饱满排产。"
  }
}

⸻

6. 业绩预告 / 快报 Schema

用于 forecast、express 和公告文本。

{
  "forecast_type_score": {
    "type": "number",
    "mapping": {
      "预增": 2,
      "略增": 1,
      "扭亏": 1.5,
      "续盈": 0.5,
      "预减": -1,
      "略减": -1,
      "首亏": -2,
      "续亏": -2,
      "不确定": 0
    }
  },
  "profit_change_reason_main": {
    "type": "string",
    "enum": [
      "main_business_growth",
      "product_price_up",
      "order_growth",
      "capacity_release",
      "cost_down",
      "non_recurring_gain",
      "asset_disposal",
      "subsidy",
      "investment_income",
      "impairment",
      "demand_down",
      "price_down",
      "cost_up",
      "unknown"
    ]
  },
  "main_business_profit_confirmed": {
    "type": "integer",
    "range": [0, 1],
    "meaning": "利润增长是否由主营业务驱动。"
  },
  "forecast_quality_score": {
    "type": "integer",
    "range": [-2, 2],
    "meaning": "预告质量。主营改善为正，非经常性收益为弱或负。"
  }
}

⸻

7. 调研纪要 / 投资者关系 Schema

用于投资者关系活动记录、调研公告。

{
  "institution_attention_score": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "机构关注度。根据调研机构数量、调研频率、问题深度判断。"
  },
  "hot_question_topic": {
    "type": "array",
    "items": "string",
    "meaning": "机构反复追问的主题，如订单、产能、价格、海外、AI、客户认证。"
  },
  "management_answer_clarity": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "管理层回答是否具体。0空泛，3有明确数据/时间表/客户/产能。"
  },
  "order_or_customer_disclosure": {
    "type": "integer",
    "range": [0, 3],
    "meaning": "是否披露订单、客户、认证、导入进展。"
  },
  "outlook_change_vs_previous": {
    "type": "integer",
    "range": [-2, 2],
    "meaning": "相比上一次调研或报告，展望是否改善。"
  }
}

⸻

8. LLM 提问模板

8.1 单文档抽取 Prompt

你是一个面向量化回测的财报信息抽取器。你只从原文抽取结构化事实，不做投资建议，不预测股价，不进行主观发挥。
请从以下上市公司公告/财报文本中，按指定 JSON schema 抽取信息。
要求：
1. 只使用文本中明确出现的信息。
2. 每个非空字段必须给出 evidence_quote。
3. 如果没有证据，value 置为 null 或 0，并说明 missing_reason。
4. 不允许用行业常识补充。
5. 不允许使用公告日之后的信息。
6. 输出必须是合法 JSON。
7. source_ann_date 必须等于输入元数据中的 ann_date。
8. 如果文本中是套话、泛泛表述，confidence 不得超过 0.4。
9. 如果有明确订单、价格、客户、产能、产品、认证、投产、供需表述，confidence 可以高于 0.7。
输入元数据：
- ts_code: {{ts_code}}
- stock_name: {{stock_name}}
- document_type: {{document_type}}
- title: {{title}}
- ann_date: {{ann_date}}
- report_period: {{report_period}}
待抽取 schema：
{{schema_json}}
文档文本：
{{document_text}}
请输出 JSON。

8.2 多期对比 Prompt

你是一个财报叙事变化分析器。请比较同一家公司不同报告期的表述变化，只输出结构化变化，不做投资建议。
目标：
判断公司在新产品、新市场、订单、产能、价格、供需、管理层展望方面，相比上一期是否改善。
要求：
1. 只比较输入文档中出现的信息。
2. 必须给出本期 evidence_quote 和上一期 evidence_quote。
3. 如果变化不明确，输出 no_clear_change。
4. 输出合法 JSON。
5. 不允许使用公告日之后的信息。
输入：
公司：{{ts_code}} {{stock_name}}
上一期文档：
- ann_date: {{prev_ann_date}}
- report_period: {{prev_report_period}}
- text: {{prev_text}}
本期文档：
- ann_date: {{curr_ann_date}}
- report_period: {{curr_report_period}}
- text: {{curr_text}}
输出字段：
{
  "new_product_change": -2到2,
  "new_market_change": -2到2,
  "order_visibility_change": -2到2,
  "capacity_progress_change": -2到2,
  "price_trend_change": -2到2,
  "demand_supply_change": -2到2,
  "management_outlook_change": -2到2,
  "risk_warning_change": -2到2,
  "summary": "不超过100字",
  "evidence": [...]
}

⸻

9. 输出表结构

每次抽取结果落库：

narrative_feature_table
document_id
ts_code
stock_name
ann_date
report_period
document_type
schema_version
new_product_signal
new_market_signal
customer_breakthrough_signal
order_growth_signal
capacity_expansion_signal
main_business_improvement_signal
non_recurring_profit_risk
management_outlook_score
risk_warning_score
demand_shortage_signal
price_increase_signal
inventory_pressure_signal
gross_margin_driver
industry_cycle_position
capacity_bottleneck_signal
order_visibility_score
forecast_type_score
profit_change_reason_main
main_business_profit_confirmed
forecast_quality_score
institution_attention_score
management_answer_clarity
order_or_customer_disclosure
outlook_change_vs_previous
llm_confidence_avg
evidence_json
created_at
text_hash

⸻

10. PIT Merge 规则

对任意 trade_date：

只允许使用 ann_date <= trade_date 的最新 narrative feature。

推荐衍生字段：

narrative_stale_days = trade_date - ann_date
has_recent_narrative_90d = narrative_stale_days <= 90
has_recent_narrative_180d = narrative_stale_days <= 180

不同文档类型可分别保留：

latest_annual_narrative_*
latest_semiannual_narrative_*
latest_quarterly_narrative_*
latest_forecast_narrative_*
latest_investor_relation_narrative_*

不要把不同类型文档简单覆盖。可以后续再做加权聚合。

⸻

11. 初始特征建议

第一批不要超过 15 个：

new_product_signal
new_market_signal
customer_breakthrough_signal
order_growth_signal
capacity_expansion_signal
demand_shortage_signal
price_increase_signal
order_visibility_score
main_business_improvement_signal
non_recurring_profit_risk
management_outlook_score
risk_warning_score
forecast_quality_score
narrative_stale_days
has_recent_narrative_180d

第二批再考虑：

outlook_change_vs_previous
demand_supply_change
management_answer_clarity
institution_attention_score
order_or_customer_disclosure

⸻

12. PoC 评估方法

12.1 覆盖率

输出：

overall coverage
yearly coverage
industry coverage
document_type coverage
missing rate
confidence distribution
stale_days distribution

12.2 信号验证

不训练模型，先做 sanity check：

top bucket vs bottom bucket
mean_ret_180d
median_ret_180d
ret>0
ret>0.3
ret>0.6
ret<0
RankIC

重点子集：

v3a+liquidity score top5%
ret_180d < 0.1 vs ret_180d > 0.6
missed super winners:
ret_180d > 1.0 AND score_rank_pct > 0.5

12.3 模型验证

只有 PoC 通过后，才训练：

baseline = v3a+liquidity
candidate = v3a+liquidity+narrative_v0
label = 180d delayed

验收标准：

180d IC +0.003 以上
或 Top20/Top50 bad rate 明显下降
或 missed super winner capture 改善

⸻

13. 失败条件

如果出现以下情况，停止扩展：

coverage < 20%
confidence 低于 0.5 的样本占比过高
字段方向不稳定
不同年份表现差异极大
加入模型后 IC/TopK 无增量
LLM 抽取无法复现
PIT 无法保证

⸻

14. 执行约束

* 不让 LLM 预测股价。
* 不让 LLM 自由发挥。
* 不做新闻实时推荐。
* 不用未来信息。
* 不直接用报告期 end_date merge。
* 不用未带 evidence 的字段。
* 不把低置信度字段直接进模型。
* 不一次性全量上线。
* 先小样本 PoC，再扩全市场。