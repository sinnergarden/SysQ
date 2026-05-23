# ADR 006: Strategy Lifecycle

**状态**：已采纳 (Accepted)
**日期**：2026-05-23

## 背景 (Context)

SysQ 目前只有 alpha_v1 一个活跃策略，但系统设计目标之一是支持多策略、多模型、多影子账户的未来场景。

当前缺乏明确的策略晋升路径：
- 一个策略从研究到生产需要经过哪些阶段？
- 每个阶段允许和禁止什么操作？
- 晋升需要满足什么条件？
- 如何退役一个策略？

没有统一的策略生命周期，容易出现研究阶段的原型直接进入生产管道、或候选策略缺乏充分验证就进入实盘的情况。

## 决策 (Decision)

我们决定定义统一的策略生命周期，包含以下阶段：

```
Research → Candidate → Shadow → Production → Retired / Archived
```

每个阶段的定义如下。

---

## 阶段详情

### 1. Research（研究阶段）

**目的**：
- 探索和验证新的因子、模型、标签、训练方式、选股逻辑、调仓频率等。
- 可以快速试错和迭代。

**允许操作**：
- 尝试：factor、label、model architecture、universe、train window、neutralization、rebalance frequency、portfolio construction
- 在 `research/`、`qsys/signal/`、`qsys/feature/`、`qsys/model/`、`configs/research/` 中自由修改
- 运行独立回测和研究脚本
- 输出研究报告

**禁止操作**：
- ❌ 写入 production ledger（`data/trade.db` 中非 research 账户）
- ❌ 进入 daily production DAG 或 systemd timer 链路
- ❌ 输出可直接用于实盘生产的订单列表
- ❌ 修改 Protected Core（见 ADR-005）

**必需输出**：
- `research_id` — 研究唯一标识
- `strategy_family` — 所属策略家族（如 alpha_v1、alpha_v2）
- `feature_set` — 使用的特征集
- `label` — 标签定义
- `universe` — 股票池
- `train_window` / `valid_window` / `test_window` — 时间窗口
- `model_type` — 模型类型
- `metrics` — 回测/评估指标
- `report.md` — 研究报告
- `artifacts` — 最小产物集合（预测、权重等）

**Promotion 到 Candidate 的条件**：
- 研究报告已完成，包含明确的假设和验证结果
- 回测结果优于当前 Shadow Baseline（或填补了现有空白）
- 不存在明显的数据泄漏
- 已知风险已被记录
- 策略家族的所有者已审核并同意晋升

---

### 2. Candidate（候选阶段）

**目的**：
- 将研究阶段的发现固化为可重复的策略候选。
- 验证候选策略能否在 daily pipeline 中稳定运行。
- 为晋升到 Shadow 做准备。

**允许操作**：
- 在 `configs/candidates/`、`qsys/strategy/candidates/` 中配置和实现候选策略
- 进入 daily shadow run（作为非主力观察账户运行）
- 输出统一的信号产物和订单意图产物

**禁止操作**：
- ❌ 修改 Protected Core（见 ADR-005）
- ❌ 写入 production ledger 中非 Shadow 的账户
- ❌ 影响主力 Shadow 账户的运行
- ❌ 输出可直接提交给真实券商的订单

**必需输出**：
- `strategy_id` — 策略唯一标识
- `candidate_id` — 候选版本标识
- `model_version` — 模型版本
- `signal_version` — 信号版本
- `config_hash` — 配置哈希
- `data_cutoff` — 数据截止日期
- `feature_schema_version` — 特征 schema 版本
- `training_recipe_version` — 训练方案版本
- `candidate_report.md` — 候选报告
- `promotion_reason` — 为什么此候选应晋升
- `owner_decision` — 策略所有者的晋升决定

**Promotion 到 Shadow 的条件**：
- 至少完成一轮完整的一周 shadow 观察运行
- 产物契约（SignalArtifact + OrderIntentArtifact）满足 ADR-007 标准
- 无异常日志输出
- 无数据 pipeline 阻塞
- 候选报告完成，包含风险摘要
- 策略所有者明确批准

---

### 3. Shadow（影子/仿真阶段）

**目的**：
- 在实际 daily pipeline 中以仿真模式运行策略。
- 验证策略在实际市场条件下的表现（以开盘价模拟成交）。
- 验证 daily pipeline 的稳定性（数据健康检查、预开盘计划生成、盘后复盘 MTM）。

**当前状态**：
- `alpha_v1` 是当前的 Shadow Baseline / Daily Ops Baseline。

**必需连接**：
- SQLite ledger（作为账户状态的事实标准）
- Run archive（运行产物归档）
- Daily report（盘前/盘后报告）
- Orders / Fills / Portfolio snapshots（订单、成交、组合快照）
- Signal artifacts / Execution artifacts（信号和执行的产物契约）

**允许操作**：
- 在 daily pipeline 中以 shadow 身份运行
- 生成交易计划并模拟成交
- 输出 MTM 报告
- 从 `data/trade.db` 中查询账户状态
- 通过 `check_shadow_status.py` 等工具监控状态

**禁止操作**：
- ❌ 自动提交真实券商订单
- ❌ 绕过 ledger，仅在 CSV/JSON 中维护状态
- ❌ 修改 ledger 历史记录用于美化回测
- ❌ 修改 Protected Core 中的交易成本假设以改善 PnL

**必需输出**（满足 ADR-007 契约）：
- SignalArtifact
- OrderIntentArtifact
- ExecutionArtifact
- PortfolioSnapshot
- RunManifest

**Promotion 到 Production 的条件**：
- 至少稳定运行 4 周以上
- 已建立 broker read-only reconciliation 流程
- 盘中风险阈值已定义
- Kill switch 已就绪
- 日度 reconciliation 已自动运行
- 回测与 shadow 结果之间的偏差已量化并解释

---

### 4. Production（生产阶段）

**目的**：
- 以真实资金进行极小规模交易。
- 必须经过严格的 broker 对账、手动确认、半自动执行阶段。

**要求**：
- Broker read-only reconciliation 至少运行 2 周
- 然后 manual order confirmation 阶段
- 然后 small semi-auto execution（手动确认交易计划，自动提交）
- Kill switch 必须可操作
- Daily reconciliation 必须自动运行

**禁止操作**：
- ❌ Agent 直接控制实盘下单
- ❌ 跳过 manual confirmation 步骤
- ❌ 在 reconciliation 流程验证完成前增加资金规模

---

### 5. Retired / Archived（退役阶段）

**目的**：
- 策略被新版本替代或明确判定无效后，有序退役。

**操作**：
- 从 daily DAG 中移除
- 在运行清单中标注 `retired` 状态
- 产物和运行记录保留，不删除
- 归档到 `run_archive/retired/` 目录

---

## 影响 (Consequences)

### 正面影响

- **可重复的晋升路径**：策略从研究到生产有明确的检查点和标准。
- **降低风险**：Shadow 阶段长时间验证后才考虑 promotion 到 Production。
- **并行开发**：多个候选策略可同时在 Shadow 层级运行观察。

### 代价

- **额外的文档负担**：每个阶段需要维护对应的产物和报告。
- **晋升周期变长**：从 Research 到 Shadow 需要多次验证。

## 后续

- [ ] 定义 Shadow 阶段的标准运行时长和观察指标。
- [ ] 实现候选策略自动注册和资源配置系统。
- [ ] 为多个候选策略并行运行准备基础框架。
- [ ] 建立候选到 Shadow 晋升的自动化检查列表。
