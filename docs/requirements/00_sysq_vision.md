# SysQ Vision — v0.1

> 本文档定义 SysQ 系统的长期愿景、角色体系和发展原则。
> 系统架构见 `docs/ARCHITECTURE.md`，use case registry 见 `01_usecase_index.md`。

---

## 1. 系统定位

SysQ 是一个**个人可维护的 A 股日频量化系统**，当前正在从个人实验系统逐步收敛为：

- **A 股量化研究系统** — 特征/标签/信号/回测的标准化实验框架
- **Shadow trading / 小资金真实交易** — 受控的晋级和日常执行管线
- **UI 分析工具** — 策略、回测、信号的只读可视化
- **Agent-assisted 股票研究** — 利用 LLM agent 处理财报/公告/新闻，输出结构化研究 memo
- **多 agent + skills + harness 驱动的研发系统** — 人主导、AI 辅助的协作开发模式

---

## 2. 设计原则

继承自 `docs/ARCHITECTURE.md` 的设计原则，额外补充：

- **需求文档驱动** — 所有命令必须对应一个 use case。如果 agent 发现用户请求不在任何 use case 中，必须先与用户确认是否为临时请求。若是，注册为 UC_TEMPORARY_REQUESTS；若同一临时请求出现超过 2 次，必须补文档并考虑是否收束为正式 use case。
- **唯一入口 + 测试兜底** — 每个正式 use case 有唯一 canonical entrypoint，且必须有测试。当 use case 的输入输出发生变更（如新增参数、扩展 schema）时，必须确保向后兼容。
- **Skill-first + harness-first** — task skills 定义执行轨道，harness checks 提供可执行护栏。不依赖 agent 人格或模型记忆。主对话拥有状态和最终决定权。Subagent 只做只读辅助。
- **Agent role notes 是 lightweight 参考，不是 runtime agent team** — 不需要用户维护多个常驻 agent。
- **Harness 优先** — 关键约束写成自动化 check，不依赖人的记忆。

---

## 3. Agent Role Taxonomy (v0.1)

当前定义 agent 角色用于 use case mapping。多个角色可由同一模型承担。**不实现多 agent runtime。**

| Role | 职责 | Use Cases |
|------|------|-----------|
| **main_agent** | 总体决策、需求拆解、任务路由、PR 创建与合并 | 全 UC（主导路由、协调） |
| **builder_agent** | 代码实现、测试修复、工程落地 | UC_DIAGNOSTICS, UC_TEMPORARY |
| **research_agent** | 量化研究、特征/模型/回测方案 | UC_RESEARCH_BACKTEST, UC_MODEL_TRAINING |
| **stock_research_agent** | 财报 PDF、公告、新闻、消息面研究，输出股票研究 memo | UC_STOCK_FUNDAMENTAL_RESEARCH |
| **ui_agent** | UI/API/可视化相关需求 | UC_UI_ANALYSIS |
| **reviewer_agent** | 代码审查、架构边界、harness/check 维护 | 全 UC（审查视角） |
| **operator_agent** | daily ops、产物检查、日志、环境、PR 状态管理 | UC_DAILY_OPS, UC_CANDIDATE_PROMOTION |

---

## 4. v0.1 Taxonomy

当前 use case 分为以下类别：

| 代码 | 类别 | 说明 |
|------|------|------|
| A | Daily Ops | 每日同步、信号、订单、盘后、shadow dry-run |
| B | Research Backtest | 特征/信号/候选回测，模型比较 |
| C | Model Training | shadow retrain、模型 artifact、训练评估 |
| D | UI Analysis | 策略看板、回测查看、信号可视化 |
| E | _(已融合到 D)_ | 单票 review 合入 UI Analysis |
| F | Candidate Promotion | candidate → shadow → prod 晋级流程 |
| G | Diagnostics | 数据/信号/ledger/artifact 质量检查 |
| H | Stock Fundamental Research | 财报/公告/新闻研究，输出 stock memo |
| I | Temporary Requests | 临时指标、图表、实验对比，不直接成为 canonical entrypoint |

---

## 5. AI 操作入口

- `AGENTS.md` 和 `docs/agents/` 存放 SysQ 的 AI 操作规则。
- workspace 级 `.claude.md` 只做 SysQ 任务分流（见 `docs/agents/workspace_claude_redirect.md`），不包含仓库内部规则。
- 所有 SysQ 的 agent 规则、use case、harness、角色说明都放在仓库内，随代码版本化和审查。
- 本 PR 新增：`.claude/skills/` 定义 task skills，`harness/checks/check_label_maturity.py` 和 `check_daily_inference_ready.py` 是可执行决策检查。

---

## 6. 收束目标

- 每个正式 use case 有唯一 canonical entrypoint
- `scripts/` 顶层只保留 canonical entrypoints（对应 `harness_map.yaml` 中 `status=stable` 的 UC）
- 临时/实验脚本在子目录或 dev 目录
- harness checks 覆盖所有 use case 的边界约束
- use case registry 随 PR 同步更新，与架构/ROADMAP/AGENTS 一致
