# Use Case Index — SysQ v0.1

> 本文档是 use case registry 的索引入口。
> 每个 use case 的详细定义在 `usecases/uc_*.md`。
> harness 映射见 `harness_map.yaml`。
> 系统 vision 见 `00_sysq_vision.md`。
> Canonical entrypoints 对齐 `docs/USE_CASES.md` §7。

---

## 原则

- 每个正式 use case（status=stable）入口以 `docs/USE_CASES.md` §7 为准。
- Entrypoint 输入输出变更（新增参数、扩展 schema 等）必须确保向后兼容。
- 所有命令必须对应一个 use case。agent 发现请求不在任何 use case 中时，必须先与用户确认是否为临时请求。若是，注册为 UC_TEMPORARY_REQUESTS；同一临时请求执行超过 2 次，必须补文档并考虑收束为正式 use case。

---

## 索引

| ID | 名称 | 类别 | 状态 | 入口（对齐 USE_CASES.md §7） | Owner |
|----|------|------|------|------------------------------|-------|
| UC_DAILY_OPS | Daily Operations | A — Daily Ops | stable | `scripts/data_sync.py`, `scripts/run_daily.py` | operator_agent |
| UC_RESEARCH_BACKTEST | Research Backtest | B — Research Backtest | stable | `scripts/compute_labels.py`, `scripts/run_research.py`, `scripts/run_signal_analytics.py`, `scripts/run_backtest.py` | research_agent |
| UC_MODEL_TRAINING | Model Training | C — Model Training | stable | `scripts/run_daily.py --mode train` | builder_agent |
| UC_UI_ANALYSIS | UI Analysis | D — UI Analysis | draft | `scripts/run_research_ui_api.py` | ui_agent |
| UC_CANDIDATE_PROMOTION | Candidate Promotion | F — Candidate Promotion | stable | `scripts/promote_candidate.py` | operator_agent |
| UC_DIAGNOSTICS | Diagnostics | G — Diagnostics | draft | `scripts/checks/`, `harness/checks/` | reviewer_agent |
| UC_STOCK_FUNDAMENTAL_RESEARCH | Stock Fundamental Research | H — Stock Fundamental Research | draft | TBD（prompt-based workflow） | stock_research_agent |
| UC_TEMPORARY_REQUESTS | Temporary Requests | I — Temporary | experimental | ad-hoc scripts | main_agent |

> **注意**:
> - UC_SINGLE_STOCK_REVIEW 已融合到 UC_UI_ANALYSIS 首批交付（单股视角 review），不再作为独立 use case。文档保留供参考。
> - `docs/USE_CASES.md` 中的 UC-W（Live vs Backtest Reconciliation，state: FUTURE）未映射到新 registry，需确认为独立 UC 或归入 UC_DIAGNOSTICS。

---

## 类别说明

```
A — Daily Ops       → 数据同步 + 每日生产运行链路
B — Research BT     → 信号研究 + 分析 + 回测 + 标签计算
C — Model Training  → 模型训练（通过 run_daily.py --mode train）
D — UI Analysis     → 只读可视化层（含 single-stock review）
F — Candidate       → 晋级 → shadow → prod
G — Diagnostics     → 质量检查
H — Stock Research  → 基本面/消息面 agent 研究（prompt-based）
I — Temporary       → 临时/实验性请求
```

## 完整详情

参见 `usecases/` 下对应文件。
