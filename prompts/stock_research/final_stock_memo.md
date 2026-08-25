# Qsys S180 信号可靠性审计 — {ts_code} {name}

> 模型排序仍是主要 alpha 来源；本 memo 只审计基本面是否支持信号及其失效风险，不产生买卖建议，不改变 Top10 排名。

## Artifact 与时间边界

- Top10 run identity: `{run_identity}`
- Top10 artifact: `{top10_artifact}`
- Top10 artifact SHA-256: `{top10_artifact_sha256}`
- Model bundle SHA-256: `{model_bundle_hash}`
- Signal / data / decision date: `{signal_date}` / `{data_date}` / `{decision_date}`
- Audit as-of date: `{audit_as_of_date}`
- 原始 180 日预测 / 排名: `{raw_prediction}` / `{rank}`

## 公司一句话业务

{一句话描述主营业务与主要盈利驱动}

## 模型逻辑的基本面验证

{财报和经营信息是否支持盈利预期变化、产业景气、资金重估或趋势延续。不要假设模型只寻找低估值股票。}

### 收入与利润质量

{收入、利润、毛利率、非经常性损益的变化与驱动，标注报告期、发布日期、页码。}

### 现金流、资产负债、应收与存货

{经营现金流与利润匹配度，杠杆/流动性，应收和存货异常，标注报告期、发布日期、页码。}

### 估值所反映的预期

{估值是否可能由增长、产业趋势或重估解释；仅判断是否有“严重透支且缺少兑现路径”的明确证据，不得因 PE/PB 高直接否定。}

## 模型失效风险

1. {财报是否与上涨逻辑冲突}
2. {盈利增长是否缺乏真实支撑}
3. {会计、监管、治理或资产负债异常}
4. {主题炒作是否缺少可持续业绩支撑}

## 未来 180 日负面催化

1. {有明确来源的潜在负面事件；没有则写“未发现明确证据”，不得编造}

## 证据时间分层

| 关键事实 | 发布日期 | 范围 | 来源类型 | 文件标题与可复核路径 |
|---|---|---|---|---|
| {事实} | {YYYY-MM-DD} | model_known / audit_only | {financial_report 等} | {标题、财报页码、公告或链接} |

`audit_only` 证据只能用于本次审计，不得声称模型在 `data_date` 已知。

## 信号可靠性审计

- 基本面支持度 (`fundamental_support`): supported / mixed / conflicted / insufficient_evidence
- 信号可信度 (`signal_confidence`): high / medium / low / unknown
- 风险等级 (`risk_level`): low / medium / high / critical / unknown
- 对模型信号的影响 (`signal_impact`): none / monitor / reduce_confidence / strongly_challenge
- 非估值挑战依据 (`challenge_basis`): {允许为空；conflicted / strongly_challenge 时必须填写非估值依据}
- 信号后新风险 (`post_signal_risks`): {仅列 audit_only 新信息；没有则为空列表}

### 四项财务质量检查

- earnings_quality: supportive / neutral / warning / unknown — {摘要}
- cashflow_quality: supportive / neutral / warning / unknown — {摘要}
- balance_sheet_quality: supportive / neutral / warning / unknown — {摘要}
- accounting_or_oneoff: supportive / neutral / warning / unknown — {摘要}

### 结论依据

{用证据解释模型发现的是真实盈利趋势、短期市场情绪，还是可能的基本面陷阱。不得输出买卖建议、目标价或重新排序。}

---

*生成时间: {datetime}*
*审计信息截止: {audit_as_of_date}*
