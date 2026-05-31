# AGENTS

本文档是 SysQ 仓库给 AI 助手的操作协议。  
系统设计见 docs/ARCHITECTURE.md，当前优先级见 ROADMAP.md。

---

## 1. 当前工作模式

SysQ 当前处于 Framework Stabilization + 投研研发迭代阶段。

默认采用 人主导、AI 辅助执行：

- 人负责方向判断、验收标准、高风险裁决。
- AI 负责读代码、提出方案、实施小步改动、跑测试、同步文档、汇报风险。
- 不固定长期 Builder / Reviewer / Operator 角色；按任务临时承担实现、审查或运行职责。
- 单轮尽量只解决一个主题，避免跨层大改。

---

## 2. 系统意识

Qsys 有两条主链路：

- Research / Backtest Chain：面向历史回放，评估 feature / model / signal / strategy 是否稳定、有增量。
- Daily Ops Chain：面向未来逐日推进，基于已批准策略生成计划、记录执行、对账归档。

核心原则：

- Research 结果不能直接进入 Production。
- Candidate / Shadow 是仿真验证阶段，不是真实交易阶段。
- Production 必须经过人工确认、broker 对账和 artifact 记录。
- data/trade.db 是目标账户状态与执行流水 SOT。
- data/meta/real_account.db 和 shadow/ 当前仍是 legacy compatibility path，不能擅自删除。
- run_daily.py / run_daily_batch.py 是当前 systemd 入口（systemd cutover 完成）。旧 legacy 入口不再被 systemd 调用。

---

## 3. 文档事实优先级

发生冲突时，按以下顺序判断：

```
实际代码调用链 / 测试结果 → docs/ARCHITECTURE.md → ROADMAP.md → docs/adr/ → docs/features/ 和历史 runbook
```

不得把目标态写成当前事实。  
不得把历史 feature 文档自动视为 current truth。

---

## 4. Required Reading by Task

开始改动前，先查阅对应文档。不要跳过。

| Task | Must read first |
|------|----------------|
| 系统架构、模块边界、主链路调整 | `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md` |
| 新增/修改数据对象、artifact、read model、字段语义 | `docs/CONTRACTS.md`, `docs/schema/`, `docs/REPO_LAYOUT.md` |
| 新增策略、feature、label、model、signal、allocation | `docs/ops/RESEARCH_STRATEGY_SOP.md`, `docs/CONTRACTS.md`, `docs/REPO_LAYOUT.md` |
| 运行或修改 daily ops、preopen、postclose、systemd 入口 | `docs/ops/DAILY_OPS_SOP.md`, `docs/ARCHITECTURE.md`, `docs/REPO_LAYOUT.md` |
| 修改 ledger、portfolio、execution、broker、MiniQMT | `docs/CONTRACTS.md`, `docs/ARCHITECTURE.md`, `docs/ops/DAILY_OPS_SOP.md` |
| 新增/移动/删除文件、目录、产物、实验结果 | `docs/REPO_LAYOUT.md`, `AGENTS.md` |
| 新增 UI、monitoring、dashboard、read-only API | `docs/CONTRACTS.md`, `docs/REPO_LAYOUT.md`, `ROADMAP.md` |
| 修改路线图、阶段目标、优先级 | `ROADMAP.md`, `docs/ARCHITECTURE.md` |
| 修改 agent 行为、安全边界、协作方式 | `AGENTS.md`, `docs/ARCHITECTURE.md` |
| 清理 legacy path、旧入口、shadow、real_account.db | `docs/ARCHITECTURE.md`, `docs/REPO_LAYOUT.md`, `docs/ops/DAILY_OPS_SOP.md` |

补充规则：

- 若任务涉及多个类别，必须读取所有相关文档。
- 若文档之间冲突，以实际代码调用链和测试结果为最高事实源，然后参考 `docs/ARCHITECTURE.md` 的 Current Transition State。
- 不得把 `docs/features/` 中的历史设计自动当作 current truth。
- 不得在未阅读对应 SOP / CONTRACT / REPO_LAYOUT 的情况下新增路径、改 artifact、切入口、动 ledger、改 broker bridge。

---

## 5. 默认可直接做的事

AI 可以直接处理：

- 文档修订、错别字、链接修正、结构小调整；
- 非公共 API 的 bug fix；
- 测试补充和测试稳定性改进；
- 只读代码审查、grep、日志分析；
- 小范围 research / signal / feature / model 实验；
- 不改变语义的重构；
- 在独立分支上做低风险改动并运行测试。

改动后必须说明：

- 改了什么；
- 为什么改；
- 如何验证；
- 是否影响 Research / Daily Ops / Production。

---

## 6. 必须先讨论的事

以下事项不能自动改，必须先说明影响并等待确认：

- 数据库 schema 变更；
- ledger / account / position / cash / order / fill 语义变化；
- data/trade.db、data/meta/real_account.db、shadow/ 的迁移、删除或默认路径切换；
- systemd、production entry、broker bridge、MiniQMT 相关改动；
- 交易规则变更，例如手续费、T+1、最小交易单位、滑点、撮合规则；
- 公共接口删除、重命名或行为变化；
- Protected Core 的核心语义变更；
- 大量删除文件、移动目录或不可逆清理；
- 真实下单、撤单、成交确认、持仓修正、现金修改。

不确定是否高风险时，按高风险处理。

---

## 7. Protected Core

以下路径属于 Protected Core，默认只允许只读分析、测试补充、日志改善和文档说明。

- qsys/ledger/
- qsys/backtest/
- qsys/trader/
- qsys/ops/daily_runner.py
- qsys/broker/
- qsys/strategy/alpha_v1/
- production daily entry 相关脚本
- scripts/ops/ 核心运营脚本
- systemd service / timer 配置
- artifact contract schema

若必须修改 Protected Core，必须先给出：

- 修改原因；
- 影响范围；
- 回滚方式；
- 最小验证命令；
- 是否影响 production daily ops。

---

## 8. 新功能与文档规则

不是所有新功能都必须先写长 feature 文档。

必须先补设计文档的情况：

- 影响架构边界；
- 引入新的长期接口；
- 修改数据 schema 或 artifact contract；
- 影响 Candidate / Shadow / Production 流程；
- 影响 ledger、daily entry、broker bridge、systemd；
- 引入新的策略生命周期规则。

普通 research 实验、小 bugfix、小脚本、小文档修正，可以直接实现并在 PR 里说明。

如需文档：

- 功能规格写入 docs/features/；
- 长期架构决策写入 docs/adr/；
- artifact 字段约束写入 docs/schema/。

---

## 9. 测试要求

提交前默认执行：

```bash
python -m compileall qsys scripts tests
python -m unittest discover tests
```

可按改动范围增加最小回归：

| 改动范围 | 测试命令 |
|---|---|
| qsys/trader/ | python -m unittest tests/trader/ |
| qsys/backtest/ | python -m unittest tests/backtest/ |
| qsys/ledger/ | python -m unittest tests/ledger/ |
| qsys/research/ | python -m unittest tests/research/ |
| qsys/signal/ | python -m unittest tests/signal/ |
| qsys/ops/ | python -m unittest tests/ops/ |

如果测试不存在或无法运行，必须明确说明原因，不能假装已验证。

---

## 10. 日期与数据语义

- signal_date：信号来源日期，通常是最近一个已收盘交易日。
- execution_date：实际计划执行日期。
- 盘前推荐必须基于 T-1 收盘。
- 数据 readiness 不满足时，流程应显式失败，不给假推荐。
- 训练、回测、推理必须避免未来数据泄露。

---

## 11. 输出方式

默认先给：

```
结论
关键依据
风险 / 阻塞点
下一步动作
```

要求：

- 引用代码时给出明确路径；
- 不把推测写成事实；
- 不用空泛总结替代验证证据；
- 涉及文件移动、删除、入口切换、DB 变更时，必须先列影响范围；
- 涉及新增目录、移动文件、删除产物、改变 artifact 位置时，必须参考 `docs/REPO_LAYOUT.md`；
- 文档引用统一使用仓库相对路径，禁止 file:// 绝对路径。

---

## 12. 禁止事项

禁止自动执行：

- 删除 shadow/；
- 删除或迁移 data/meta/real_account.db；
- 切换 production systemd 入口；
- 绕过 artifact contract 接入 Candidate / Production；
- 让 Research artifact 直接进入 Production；
- 绕过 LedgerService 直接写生产状态；
- 自动提交真实券商订单；
- 修改生产密钥、本地私密配置或外部授权信息；
- **从 archive/ import 或调用脚本**——archive/ 是只读历史参考，不保证可运行，新代码不得依赖 archive/。
