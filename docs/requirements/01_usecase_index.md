# Use Case Index — SysQ v0.1

> 本文档是 use case registry 的索引入口。
> 每个 use case 的详细定义在 `usecases/uc_*.md`。
> harness 映射见 `harness_map.yaml`。
> 系统 vision 见 `00_sysq_vision.md`。

---

## 索引

| ID | 名称 | 类别 | 状态 | 入口 | Owner |
|----|------|------|------|------|-------|
| UC_DAILY_OPS | Daily Operations | A — Daily Ops | stable | `scripts/run_daily.py`, `scripts/run_daily_batch.py` | operator_agent |
| UC_RESEARCH_BACKTEST | Research Backtest | B — Research Backtest | stable | `scripts/run_research.py`, `scripts/research/backtest_from_signal.py`, `scripts/run_signal_analytics.py` | research_agent |
| UC_MODEL_TRAINING | Model Training | C — Model Training | stable | `scripts/run_daily_batch.py --mode train` | builder_agent |
| UC_UI_ANALYSIS | UI Analysis | D — UI Analysis | draft | `scripts/run_research_ui_api.py` | ui_agent |
| UC_SINGLE_STOCK_REVIEW | Single Stock Review | E — Single Stock Review | draft | TBD | research_agent |
| UC_CANDIDATE_PROMOTION | Candidate Promotion | F — Candidate Promotion | stable | `scripts/promote_candidate.py` | operator_agent |
| UC_DIAGNOSTICS | Diagnostics | G — Diagnostics | draft | `scripts/checks/`, `harness/checks/` | reviewer_agent |
| UC_STOCK_FUNDAMENTAL_RESEARCH | Stock Fundamental Research | H — Stock Fundamental Research | draft | TBD (prompt-based) | stock_research_agent |
| UC_TEMPORARY_REQUESTS | Temporary Requests | I — Temporary | experimental | ad-hoc scripts | main_agent |

---

## 类别说明

```
A — Daily Ops       → 每日生产运行链路
B — Research BT     → 历史回放研究链路
C — Model Training  → 模型训练与 artifact 管理
D — UI Analysis     → 只读可视化层
E — Single Stock    → 单票级 debug
F — Candidate       → 晋级 → shadow → prod
G — Diagnostics     → 质量检查
H — Stock Research  → 基本面/消息面 agent 研究
I — Temporary       → 临时/实验性请求
```

## 完整详情

参见 `usecases/` 下对应文件。
