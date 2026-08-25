---
name: sysq-stock-research
description: 审计严格 PIT CSI1800 S180 Top10 的基本面信号可靠性和模型失效风险；不重新选股、不改排名、不输出买卖建议。
---

# sysq-stock-research

## Purpose

Qsys S180 Top10 的基本面信号可靠性审计 skill（UC_STOCK_FUNDAMENTAL_RESEARCH）。股票已经由严格 PIT CSI1800、180 日预测模型按原始分数选出；本 skill 只检查模型信号是否得到基本面支持、是否存在模型失效风险。模型排序仍是主要 alpha 来源。

本 skill 不重新选股、不重排或删除 Top10、不以传统价值投资标准替代量化趋势逻辑，也不产生买卖建议。

## Required inputs

- 已通过 `harness/checks/check_top10_signal_artifact.py` 的 immutable `top10_run.json`
- 对应的 `candidate_run.json` 与 SHA-256
- `run_identity`、`signal_date`、`data_date`、`decision_date`、`execution_date`
- `model_bundle_hash`、个股 `rank` 与 `raw_prediction`
- 财报、公告、新闻、行业资料；每项证据必须带发布日期和可复核来源
- `audit_as_of_date`：本次审计真正可获得信息的截止日

## Required reads

- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- `docs/requirements/domains/stock_research.md`
- `prompts/stock_research/final_stock_memo.md`

## Workflow

1. 归类 UC_STOCK_FUNDAMENTAL_RESEARCH，并先验证 `top10_run.json`。
2. 从 Top10 artifact 原样复制股票、模型排名和 `raw_prediction`；不得重新计算、标准化或排序。
3. 收集截至 `audit_as_of_date` 已公开的财报、公告和新闻，优先公司公告、交易所文件和正式财报。
4. 将证据按可用时间分层：
   - `model_known`：发布日期不晚于 `data_date`，可能已被模型输入或市场定价；
   - `audit_only`：在 `data_date` 后、`audit_as_of_date` 前公开，只能用于当次审计，不能声称模型当时已知。
5. 重点寻找模型失效风险，而不是寻找“不喜欢的股票”：
   - 利润增长缺少经营现金流支撑；
   - 应收、存货、毛利率或资产负债出现异常；
   - 非经常性损益、会计保留意见、监管或治理问题；
   - 未来 180 日内明确的负面催化；
   - 上涨主要依赖缺乏业绩支撑、不可持续的主题炒作。
6. 估值只用于解释市场预期是否可能严重透支增长。高 PE/PB 本身不能否定模型信号。
7. 每只股票按模板输出 memo，并给出以下固定字段：
   - `fundamental_support`: `supported | mixed | conflicted | insufficient_evidence`
   - `signal_confidence`: `high | medium | low | unknown`
   - `risk_level`: `low | medium | high | critical | unknown`
   - `signal_impact`: `none | monitor | reduce_confidence | strongly_challenge`
8. 写入 `research_memos/{ts_code}/{signal_date}/stock_research_memo.md`；Top10 批次同时写入 `research_memos/s180_top10/{signal_date}/{run_identity}/top10_fundamental_audit.json` 和同目录 Markdown 汇总。
9. 运行 stock research memo checker；失败时修正审计 artifact，不修改 Top10。

## Batch JSON contract

批次 JSON 顶层必须包含：`schema_version=1`、`artifact_type=s180_top10_fundamental_signal_audit`、`status=complete`、Top10 artifact 路径与 SHA-256、run identity、signal/data/decision dates、model bundle hash、audit as-of date、Markdown 汇总路径与 SHA-256，以及按模型排名排列的十条 `audits`。

每条 audit 必须原样保留 `rank`、`ts_code`、`name`、`raw_prediction`，并包含四个审计枚举、`major_risks`、`memo_path` / `memo_sha256` 和 `evidence`。每条 evidence 必须包含 `source`、`claim`、`published_date`、`availability_scope`。

还必须包含：

- `financial_quality_checks`：完整覆盖 `earnings_quality`、`cashflow_quality`、`balance_sheet_quality`、`accounting_or_oneoff`；每项给出 `status` (`supportive | neutral | warning | unknown`) 与非空摘要；
- `challenge_basis`：若输出 `conflicted` 或 `strongly_challenge`，必须至少给出一项非估值依据：`financial_conflict | cashflow_quality | balance_sheet | accounting_governance | negative_catalyst | theme_without_earnings`；
- `post_signal_risks`：只列 `audit_only` 新信息造成的当前风险，禁止倒推模型在原信号时点必然错误；
- evidence 的 `source_type`、`document_title`、`source_url_or_path`；不能用不可复核的“某资料”。

`audit_as_of_date` 不得晚于 Top10 的 `execution_date` 或真实当前日期。

## Interpretation rules

- `supported` 表示最新可用基本面与模型捕捉的中期重估/盈利趋势相容，不表示值得买入。
- `conflicted` 需要明确证据表明模型方向可能错误，不能仅因估值高或个人不偏好。
- `signal_impact` 只描述基本面证据对模型信号可信度的影响，不是仓位或交易指令。
- 无法获得可靠材料时必须使用 `insufficient_evidence` / `unknown`，不得补写推断为事实。

## Never

- 使用 `audit_as_of_date` 之后的信息，或把 `audit_only` 信息伪装成模型已知信息
- 重新选股、重排、过滤或删除 Top10
- 输出买入、卖出、目标价、合理价格、“值得/不值得买”等投资建议
- 仅因为 PE/PB 较高而否定信号
- 把长期价值投资逻辑当成否定量化趋势信号的默认标准
- 修改 broker / trader / ledger / signal / model / daily_runner

## Required checks

```bash
python harness/checks/check_usecase_registry.py
python harness/checks/check_stock_research_memo.py \
  --top10-artifact <top10_run.json> \
  --audit-artifact <top10_fundamental_audit.json>
```
