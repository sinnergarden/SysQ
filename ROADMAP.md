# ROADMAP

本文档只回答三个问题：

1. 当前最重要的收束方向是什么；
2. 每个阶段做到什么算完成；
3. 哪些事情暂时不做。

系统设计见 `docs/ARCHITECTURE.md`，操作协议见 `AGENTS.md`，仓库布局见 `docs/REPO_LAYOUT.md`。

---

## Current Focus

当前只优先推进三条主线：

1. **Daily Operations Hardening**
   - 入口收束；
   - ledger 统一；
   - systemd 切换；
   - 最小只读 Ops UI。

2. **Research Pipeline Productization**
   - 统一评估口径；
   - Research Analytics / DuckDB；
   - Research UI；
   - 实验结果可比较。

3. **Promotion & Production Preparation**
   - Candidate/Shadow dashboard；
   - MiniQMT 只读桥接；
   - execution / reconciliation contract。

除非用户明确要求，agent 不应主动推进 Current Focus 之外的大功能。

---

## Phase 0 — Foundation（已完成）

目标：最小闭环跑通，系统从脚本集合进入框架化阶段。

完成结果：

- raw → qlib → train → backtest → daily predict 已跑通；
- SQLite ledger 主线已建立，但 legacy account store 与 shadow files 仍在迁移收束期；
- RollingResearchRunner v2 matrix mode 已落地；
- Protected Core、Strategy Lifecycle、Artifact Contract 已有 ADR；
- ARCHITECTURE / AGENTS / ROADMAP 已形成 current truth 主导航。

---

## Phase 1 — Daily Operations Hardening（当前主攻）

目标：让 daily ops 从"能跑"变成"每天可检查、可回滚、可维护"。

关键结果：

| 结果 | 完成判定 | 当前进展 |
|---|---|---|
| 入口收束 | systemd 不再依赖 legacy shell wrapper，主链路使用 `run_daily.py` / `run_daily_batch.py` | ✅ DailyRunner 4 模式通过 8-gate 验证；`--trade-date auto`、`--no-notify`、signal_basket 修复、reconciliation_result 写入。**systemd unit 替换 pending operator confirmation** |
| Ledger 统一 | `trade.db` 成为唯一账户状态 SOT，`real_account.db` 和 `shadow/` 不再作为写入事实源 | 🟡 `audit_state_paths.py`（只读审计）已就绪；三态共存仍在；迁移尚待启动 |
| Daily artifact 稳定 | preopen / postclose 每日产物路径稳定，关键报告可重建 | 🟡 target path debug-chain artifact contract 已验证（signal_basket CSV + reconciliation_result.json + checker 54/54）；systemd cutover 后仍需生产路径观察 |
| 最小 Ops UI | 可只读查看 data readiness、latest date、daily plan、shadow/ledger 状态、postclose report | ❌ 尚未启动（Phase 2 计划）|
| 异常处理明确 | 空 plan、账户异常、数据不齐、MTM 异常有明确阻断或降级策略 | 🟡 checker 级别处理已就绪；SOP 已覆盖故障流程；operator 通知自动化待完成 |

本阶段不做：

- 无人值守实盘下单；
- 大规模 UI 复杂交互；
- 新增无约束入口脚本。

---

## Phase 2 — Research Pipeline Productization

目标：让研究结果可横向比较、可复现、可进入 Candidate 评估。

关键结果：

| 结果 | 完成判定 |
|---|---|
| 评估口径统一 | strict eval / backtest / rolling research 指标口径一致 |
| Research Analytics | signal、label、IC、RankIC、experiment index 可通过 DuckDB 或等价查询层检索 |
| Research UI | 可比较模型版本、信号组合、IC/RankIC、回测表现 |
| ResearchSpec 稳定 | 实验配置可序列化、可复跑、可索引 |
| feature set 观察 | baseline / extended / 资金流 / PIT / 估值增量可解释 |

本阶段不做：

- 自动晋级 production；
- 复杂多资产框架；
- 为了实验方便绕过数据泄露检查。

---

## Phase 3 — Promotion & Production Preparation

目标：让 Candidate/Shadow 到小资金 Production 的路径可审计、可回滚。

关键结果：

| 结果 | 完成判定 |
|---|---|
| Candidate gate | 重训后可自动跑 strict eval + rolling backtest + baseline 对比 |
| Shadow dashboard | 可持续查看 shadow 表现、持仓、换手、回撤、reconciliation gap |
| MiniQMT 只读桥接 | 可读取账户、持仓、委托、成交 |
| 执行结果回流 | WSL ← Windows 的 execution result contract 稳定 |
| 回滚轨迹 | 晋级、上线、回滚有最小审计记录 |

本阶段不做：

- 无人值守自动下单；
- 未经人工确认的真实交易；
- 未经 dashboard 验证的策略上线。

---

## Phase 4 — Automation & Advanced Observability

目标：把高频流程沉淀为可复用命令，增强告警和自动化。

关键结果：

| 结果 | 完成判定 |
|---|---|
| Daily ops monitoring | readiness、plan diff、MTM、reconciliation 有稳定告警 |
| Broker monitoring | broker sync、position gap、cash gap 可观测 |
| Workflow commands | `preopen-plan`、`feature-audit`、`rolling-eval` 可复用 |
| 长任务状态 | 阶段、关键数字、产物位置结构化输出 |

---

## Global Non-goals

- 不做无人值守自动实盘下单，除非 broker bridge、ledger、reconciliation、manual approval 全部稳定；
- 不做跨市场多资产统一引擎；
- 不为了"看起来先进"新增入口脚本；
- 不把 UI 做成交易操作台，早期 UI 只读；
- 不让 Research artifact 直接进入 Production。
