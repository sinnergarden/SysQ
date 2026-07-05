# Use Case Index — SysQ v0.1

> 本文档是 use case registry 的索引入口。
> 每个 use case 的详细定义在对应 `docs/requirements/domains/{domain}.md` 中。
> Harness 映射见 `harness_map.yaml`，系统 vision 见 `00_sysq_vision.md`。

---

## 原则

- 所有命令必须对应一个 use case。agent 发现请求不在任何 use case 中时，必须先与用户确认是否为临时请求。若是，注册为 UC_TEMPORARY_REQUESTS；同一临时请求执行超过 2 次，必须补文档并考虑收束为正式 use case。
- 新增 use case 前请阅读 `02_usecase_template.md`。

---

## 索引

| ID | 名称 | Domain | 状态 | Owner |
|----|------|--------|------|-------|
| UC_DAILY_OPS | Daily Operations | daily_ops | stable | operator_agent |
| UC_DAILY_INFERENCE_RUN | Daily Inference Run | daily_ops | draft | operator_agent |
| UC_RESEARCH_BACKTEST | Research Backtest | research | stable | research_agent |
| UC_MODEL_TRAINING | Model Training | model_training | stable | builder_agent |
| UC_UI_ANALYSIS | UI Analysis | ui_analysis | draft | ui_agent |
| UC_CANDIDATE_PROMOTION | Candidate Promotion | promotion | stable | operator_agent |
| UC_DIAGNOSTICS | Diagnostics | diagnostics | draft | reviewer_agent |
| UC_STOCK_FUNDAMENTAL_RESEARCH | Stock Fundamental Research | stock_research | draft | stock_research_agent |
| UC_TEMPORARY_REQUESTS | Temporary Requests | temporary_requests | experimental | main_agent |

> **注意**: UC_SINGLE_STOCK_REVIEW 已融合到 UC_UI_ANALYSIS 首批交付（单股视角 review），
> 定义见 `domains/ui_analysis.md` 中 UC_UI_SINGLE_STOCK_REVIEW（status=merged）。

---

## 类别说明

| Domain | 说明 |
|--------|------|
| daily_ops | 数据同步 + 每日生产运行链路 |
| research | 信号研究 + 分析 + 回测 |
| model_training | 模型训练与 artifact 管理 |
| ui_analysis | 只读可视化层（含 single-stock review） |
| promotion | 晋级候选、shadow/prod pointer |
| diagnostics | 质量检查与 harness 验证 |
| stock_research | 基本面/消息面 agent 研究（prompt-based） |
| temporary_requests | 临时/实验性请求 |
