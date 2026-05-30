# ROADMAP

本文档描述 SysQ 的阶段目标和关键结果。系统设计见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，操作协议见 [AGENTS.md](AGENTS.md)。

---

## Phase 0 — Foundation（已完成）

最小闭环已跑通，框架稳定化基本收尾。

**关键结果**：
- 数据链路：raw → qlib → train → backtest → daily cross-sectional predict 已跑通
- Ledger 主线：SQLite ledger 已取代 JSON/CSV 成为影子/仿真账户状态的事实标准
- 框架治理：ADR-005（Protected Core）、ADR-006（Strategy Lifecycle）、ADR-007（Artifact Contract）已采纳，AGENTS 协议已发布
- 研究管线：RollingResearchRunner v2 matrix mode 已落地，signal generator / transform / combination 独立可换
- 文档体系：ARCHITECTURE、AGENTS、ROADMAP、features/、adr/ 分层就绪

---

## Phase 1 — Daily Operations Hardening（进行中）

**目标**：生产主链路收束，从"能跑"进化到"可运营"。

| 关键结果 | 优先级 |
|---------|--------|
| Old entry（`run_preopen.sh` / `run_daily_trading.py`）切换为 `run_daily.py` + `run_daily_batch.py` | 高 |
| systemd 指向新入口，废弃 shell wrapper | 高 |
| 三态存储（`trade.db` + `real_account.db` + `shadow/`）统一为单一 ledger SOT | 高 |
| BacktestEngine 与 DailyRunner 执行语义（撮合、成本、持仓演算）靠拢 | 中 |
| 明确空 plan、账户异常、数据不齐时的处理策略 | 中 |
| `order_intents` 产物契约定稿，作为 WSL → MiniQMT bridge 的固定输入 | 中 |

---

## Phase 2 — Research Pipeline Productization（进行中）

**目标**：研究结果可公平比较、可对接 Candidate 评估。

| 关键结果 | 优先级 |
|---------|--------|
| rolling 默认参数（训练窗、测试窗、滚动步长）固化 | 高 |
| strict eval / backtest / rolling backtest 口径一致，输出统一对比面板 | 高 |
| Research Analytics 层（DuckDB）用于 IC/RankIC/实验索引横向查询 | 中 |
| ExperimentSpec / ResearchSpec 稳定序列化，对接 UI | 中 |
| extended feature set 消融研究：baseline / 资金流 / PIT / 估值 / 组合增量 | 低 |

---

## Phase 3 — Candidate → Shadow → Production Automation（规划中）

**目标**：模型晋级自动化，回滚可审计。

| 关键结果 | 优先级 |
|---------|--------|
| 重训后自动跑 strict eval + rolling backtest + baseline 对比 | 高 |
| 晋级、上线、回滚留下最小审计轨迹 | 高 |
| MiniQMT bridge 最小可用：支持账户/持仓/委托/成交读取 | 中 |
| 订单生命周期对象定稿：pending / partial_fill / filled / canceled / rejected | 中 |
| 定义 WSL → Windows 执行结果回流契约，替代手工 CSV | 低 |

---

## Phase 4 — Observability & Automation（规划中）

**目标**：系统状态可观测，异常可告警，高频流程可复用。

| 关键结果 | 优先级 |
|---------|--------|
| Research analytics 查询层（signal / label / IC / 实验索引）可用 | 中 |
| Daily ops 监控：数据 readiness、plan diff、MTM 异常、reconciliation 告警 | 中 |
| Workflow / plugin layer 首发：`preopen-plan`、`feature-audit`、`rolling-eval` 三个 command | 低 |
| 长任务进度表达结构化：阶段 / 关键数字 / 产物位置，低噪音 | 低 |

---

## 不做

- Phase 4 完成前不接入自动实盘下单
- Phase 4 完成前不做跨市场多资产统一引擎
- 不为了"看起来先进"而继续无约束增加脚本入口
