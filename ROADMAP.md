# ROADMAP

## 当前阶段目标

当前阶段的目标，不再只是"把链路跑通一次"，而是把 Qsys 收敛成一个：

- 能稳定做周一到周五运营的系统
- 能持续做投研与回测的系统
- 能把 research / candidate / production 分层管理的系统
- 能把高频研究与运营流程沉淀成稳定入口，而不是每次靠临时 prompt 和散装文档驱动
- 能逐步收敛出低 agent 依赖、可严格 review 的生产脚本与执行桥接链路

阶段完成判定：

- daily ops 有固定主入口与盘前/盘后流程
- 训练 / 回测 / 严格评估有统一口径
- 模型替换有明确晋级门槛与回滚边界
- 重要流程有结构化报告，而不是只看 stdout
- 高频流程已有可复用的 workflow asset（skills / commands / output contract）
- 文档、脚本、测试三者保持同步

---

## 现状总结（2026-04）

当前 Qsys 已经不是“空架子”，而是已经具备最小闭环的量化投研/运营底座：

- `raw -> qlib/bin -> train -> backtest -> latest cross-sectional predict` 最小链路已跑通
- 日常交易主流程已存在，real / shadow 账户和 plan 产物已初步成形
- 非重叠 strict eval 口径、`top_k=5`、周级重训等关键研究共识已基本固定
- 文档、架构、runbook、feature docs 已开始成体系，项目已从“探索期”进入“收敛期”

但当前的主要短板，也已经比较明确：

- 高频流程仍偏脚本驱动，入口多、口径容易漂移
- 一部分重要规则仍散落在聊天结论、文档、脚本默认值和人工记忆里
- 结构化 report 还不完整，很多流程仍更像“能跑通”而不是“可审计、可复用”
- Qsys 已有不少文档，但其中很多是给人读的，不是给 agent/自动流程直接消费的“可执行规则层”
- 生产运行形态与执行桥接尚未产品化：目标是 WSL 固定生产流程 + Windows MiniQMT 执行桥接，但当前还停留在 plan / sync / reconcile 阶段

因此，当前 roadmap 的新增方向不是大改引擎，而是补一层轻量的 workflow / plugin abstraction：

- 保留现有 Python engine 与脚本入口
- 把高频流程提炼成 `skills + commands + connectors + output contracts`
- 让 Qsys 从“研究仓库 + 若干脚本”进一步收敛成“研究操作系统”

---

## 已形成共识

- 数据 readiness 是训练、回测、daily ops 的前置条件
- 不能只依赖 Alpha158 量价特征；在数据 ready 且缺失可控时，应优先接入保守的资金流 / 基本面 / 估值因子
- 默认不做 feature selection
- 评估必须尽量 out-of-sample，避免 train/test overlap
- 近期回测统一优先用 `top_k=5`
- 新功能优先收束已有脚本，不轻易新增入口
- 重要变更必须同步更新文档和测试

---

## P0：先解决顶层运行模型

### P0.1 分层管理：research / candidate / production

目标：
- 不再让研究产物和生产产物混用
- production 只认明确批准的模型版本

待办：
- [x] 定义 production model manifest 结构
- [ ] 明确 candidate artifact 的目录与元信息要求
- [x] 明确 production 切换与回滚规则
- [x] 在 daily ops 中去掉"默认拿最新模型目录"的隐式假设

### P0.2 统一 evaluator / strict evaluation contract

目标：
- 把当前口头共识固化成系统默认

待办：
- [x] 统一 baseline / extended 的评估入口
- [x] 明确 train / valid / test 切法
- [x] 固化主评估窗口：`2025 -> 最近`
- [x] 固化辅评估窗口：`2026 YTD`
- [x] 固化回测默认参数：`top_k=5`
- [x] 统一输出 evaluation report

### P0.3 Daily Ops checklist 产品化

目标：
- 让盘前 / 盘后流程从"能跑"进化到"可运营"
- 为后续 WSL 生产脚本与 Windows MiniQMT 执行桥接预留稳定输入输出

待办：
- [x] 盘前 checklist 结构化
- [x] 盘后 checklist 结构化
- [x] 生成 daily plan 时附带模型版本 / 数据状态
- [x] real/shadow reconciliation 结果结构化输出
- [ ] 明确空 plan、账户异常、数据不齐时的处理策略
- [ ] 定义 `order_intents` 产物契约，作为 WSL -> Windows bridge 的固定输入

### P0.4 统一 run report

目标：
- 所有关键流程都产出结构化结果

待办：
- [ ] 数据更新 report
- [x] 训练 report
- [ ] 回测 report
- [ ] strict evaluation report
- [x] daily ops report

### P0.5 固化 workflow contract（新增）

目标：
- 把高频研究/运营流程从“散装文档 + 临时口述”收敛成可复用的 workflow asset
- 先统一规则层，再决定是否接 Claude/OpenClaw 等宿主壳

待办：
- [ ] 定义 Qsys workflow asset 的最小物理结构（`skill` / `command` / `connector` / `output contract`）
- [ ] 明确哪些现有文档内容应上收为 `SKILL.md`，哪些继续保留为普通文档
- [ ] 明确 command 层只做入口与编排，不重写底层研究/交易逻辑
- [ ] 定义 `md + json` 的统一输出契约，便于人读与 agent 续接

---

## P1：收束代码与脚本

### P1.1 脚本 inventory 与收束

目标：
- 减少临时入口，降低维护成本

待办：
- [ ] 列出当前所有 `scripts/` 的职责与归属
- [ ] 标记保留 / 合并 / 废弃候选
- [ ] 把临时实验脚本下沉为标准入口或移出主线

### P1.2 特征体系收束

目标：
- 让 feature set 从"脚本里拼"变成"系统内可管理对象"

待办：
- [x] 增加 `extended` feature set
- [ ] 拆分资金流 / 基本面 / 估值子集做归因实验
- [ ] 建立 feature set 命名与版本约定
- [ ] 输出字段级缺失与死因子报告

### P1.3 训练与生产元信息完善

目标：
- 让 scheduler / promotion 能可靠消费训练产物

待办：
- [ ] 统一 `meta.yaml` 字段要求
- [ ] 补足 model age / train window / feature_set 等元信息
- [ ] 统一训练 summary 与模型目录结构

### P1.4 文档分层与 skill 提炼（新增）

目标：
- 不再让规则长期停留在散装文档和聊天结论里
- 把“给人看的说明”和“给 agent/流程消费的规则”明确分层

待办：
- [ ] 盘点现有 `README / ARCHITECTURE / RUNBOOK / features/* / 研究记录` 中可提炼的高频规则
- [ ] 首批沉淀 `trading-calendar-guard`、`feature-readiness-audit`、`train-split-discipline`、`shadow-execution-planner`
- [ ] 为首批 skill 明确触发条件、workflow、输出契约、阻断条件
- [ ] 建立 skill 与底层 Python 入口的映射表，避免规则层与执行层脱节

---

## P2：投研与模型晋级流程

### P2.1 周级重训流程

待办：
- [ ] 明确重训频率与默认训练窗
- [ ] 重训后自动跑 strict eval
- [ ] 重训结果自动与 baseline / production 对比

### P2.2 候选模型晋级

待办：
- [ ] 定义 candidate gate
- [ ] 定义 production gate
- [ ] 定义替换上线与回滚流程

### P2.3 研究报告模板

待办：
- [ ] 因子实验模板
- [ ] 模型实验模板
- [ ] 策略实验模板

### P2.4 Qsys plugin / workflow layer 首发版本（新增）

目标：
- 在不改动核心 engine 的前提下，给 Qsys 增加一层稳定的研究操作接口

待办：
- [ ] 设计 `qsys_plugins/core` 的目录骨架与 manifest
- [ ] 首发 `preopen-plan`、`feature-audit`、`rolling-eval` 三个 command
- [ ] 为 command 补一层薄适配代码，统一调用现有 `scripts/` 或 `qsys/*` 模块
- [ ] 验证 command 输出可同时服务：人工阅读、日报沉淀、agent 二次消费
- [ ] 再决定是否兼容 Claude plugin / OpenClaw skill 等外部宿主格式

### P2.5 MiniQMT bridge / production script 主线（新增）

目标：
- 收敛出低 agent 依赖的生产 daily 脚本，并逐步打通 WSL 与 Windows MiniQMT 的执行桥接

待办：
- [ ] 明确 production daily 的固定运行时：Windows 主机上的 WSL
- [ ] 固化 `preopen-plan` adapter，并输出 target / executable / blocker / assumptions
- [ ] 设计 `order_intents` artifact 作为 WSL -> Windows bridge 输入
- [ ] 设计 `qsys/broker/miniqmt.py` 抽象接口，先支持账户/持仓/委托/成交读取
- [ ] 定义订单生命周期对象：pending / partial_fill / filled / canceled / rejected
- [ ] 设计 Windows 回流到 WSL 的执行结果契约，替代长期依赖手工 CSV

---

## Backlog

- 更细粒度的风控与交易约束测试
- 回测和 daily plan 输出的统一可视化报告
- 测试数据样本标准化与可重复生成机制
- 更正式的 model registry / artifact registry
- 调度自动化与周级运营计划固化

---

## 明确不做

- 当前阶段不接入自动实盘下单
- 当前阶段不做跨市场多资产统一引擎
- 当前阶段不做大规模框架重写
- 当前阶段不为了"看起来先进"而继续无约束增加脚本入口
