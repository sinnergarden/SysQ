# ROADMAP

## 当前阶段目标

当前阶段的目标，不再只是“把链路跑通一次”，而是把 SysQ 收敛成一个：

- 能稳定做周一到周五运营的系统
- 能持续做投研、评估、滚动回测的系统
- 能把 research / candidate / production 分层管理的系统
- 能把高频研究与运营流程沉淀成稳定入口，而不是每次靠临时 prompt 和散装文档驱动
- 能在长任务里稳定给出低噪音、结构化、可复盘进度的系统
- 能逐步收敛出低 agent 依赖、可严格 review 的生产脚本与执行桥接链路

阶段完成判定：

- daily ops 有固定主入口与盘前/盘后流程
- 训练 / strict eval / backtest / rolling backtest 口径统一
- 模型替换有明确晋级门槛与回滚边界
- feature set 可追踪 coverage / readiness / 缺失退化情况
- 重要流程优先产出结构化 report，而不是堆 stdout
- 高频流程已有可复用的 workflow asset（skills / commands / output contract）
- 文档、脚本、测试三者保持同步

---

## 现状总结（2026-04）

当前 SysQ 已经不是“空架子”，而是已经具备最小闭环的量化投研/运营底座：

- `raw -> qlib/bin -> train -> backtest -> latest cross-sectional predict` 最小链路已跑通
- 日常交易主流程已存在，real / shadow 账户和 plan 产物已初步成形
- 非重叠 strict eval 口径、`top_k=5`、周级重训等关键研究共识已基本固定
- 文档、架构、runbook、feature docs 已开始成体系，项目已从“探索期”进入“收敛期”

但当前的主要短板也比较明确：

- 高频流程仍偏脚本驱动，入口多、口径容易漂移
- 一部分重要规则仍散落在聊天结论、文档、脚本默认值和人工记忆里
- 结构化 report 还不完整，很多流程仍更像“能跑通”而不是“可审计、可复用”
- SysQ 已有不少文档，但其中很多是给人读的，不是给 agent/自动流程直接消费的“可执行规则层”
- 生产运行形态与执行桥接尚未产品化：目标是 WSL 固定生产流程 + Windows MiniQMT 执行桥接，但当前还停留在 plan / sync / reconcile 阶段

因此，当前 roadmap 的新增方向不是大改引擎，而是两条并行主线：

- 保留现有 Python engine 与脚本入口，继续收束研究、评估、运营主链路
- 补一层轻量的 workflow / plugin abstraction，把高频流程提炼成 `skills + commands + connectors + output contracts`
- 同时明确生产运行形态：WSL 固定生产脚本 + Windows MiniQMT bridge

---

## 已完成 / 已形成共识

- 数据 readiness 是训练、回测、 daily ops 的前置条件
- 不能只依赖 phase123 量价特征；在数据 ready 且缺失可控时，应优先接入保守的资金流 / 基本面 / 估值因子
- 默认不做 feature selection，先做可解释的 feature set 管理与分层观察
- 评估必须尽量 out-of-sample，避免 train/test overlap
- strict eval 近期统一优先用 `top_k=5`
- 已建立 production manifest 与 daily ops 的生产模型解析路径
- 已建立训练 / strict eval / daily ops 的结构化 report 基座
- 新功能优先收束已有脚本，不轻易新增入口
- 重要变更必须同步更新文档和测试

---

## 进行中（当前主线）

### 1. 生产主链路收束

目标：
- 不再让研究产物和生产产物混用
- 让盘前链路优先稳定、可解释、可回滚

当前项：
- [x] 定义 production model manifest 结构
- [x] 明确 production 切换与回滚规则
- [x] 在 daily ops 中去掉“默认拿最新模型目录”的隐式假设
- [ ] 明确 candidate artifact 的目录与元信息要求
- [ ] 定义 candidate gate / production gate 的最小可执行口径

### 2. 评估与回测口径统一

目标：
- 把 strict eval、普通 backtest、rolling backtest 收到统一对比面板
- 让模型是否值得晋级，不再只看一次性结果

当前项：
- [x] 统一 baseline / extended 的 strict eval 入口
- [x] 明确 train / valid / test 切法
- [x] 固化主评估窗口：`2025 -> 最近`
- [x] 固化辅评估窗口：`2026 YTD`
- [x] 固化 strict eval 默认参数：`top_k=5`
- [ ] 普通 backtest 输出统一 report 与 artifact 契约
- [ ] rolling backtest 的窗口、步长、汇总指标落地
- [ ] strict eval / backtest / rolling backtest 的统一汇总视图

### 3. feature set 收束与 readiness 观测

目标：
- 让 feature set 从“脚本里拼”变成“系统内可管理对象”
- 让扩展因子缺失时能优雅退化，而不是直接把主链路搞坏

当前项：
- [x] 增加 `extended` feature set
- [ ] 完成 feature 拉齐后的消融研究：baseline / 资金流 / PIT / 估值 / 组合增量
- [ ] 输出字段级 coverage、缺失率、死因子/常数因子观察
- [ ] 建立 feature set 命名、版本、meta 约定
- [ ] 在 readiness 中补 feature coverage / readiness 分层摘要
- [ ] graceful degradation：扩展层告警不掩盖 core 主链路状态

### 4. 长任务进度与日志结构化

目标：
- 让长任务的进度表达更像“阶段状态 + 关键数字 + 产物位置”，而不是刷屏
- 默认低噪音，但保留排障所需关键上下文

当前项：
- [x] daily ops 主入口收束为阶段化、键值化进度日志
- [x] readiness 摘要进入结构化 `data_status`
- [ ] 数据更新 / 训练 / backtest / strict eval 入口对齐同一进度口径
- [ ] 统一 run report 中的 progress / stage / artifact schema
- [ ] 为长任务失败场景补“卡在哪一阶段”的最小护栏测试

### 5. workflow contract 与 skill 层

目标：
- 不再让规则长期停留在散装文档和聊天结论里
- 把“给人看的说明”和“给 agent/流程消费的规则”明确分层

当前项：
- [ ] 盘点现有 `README / ARCHITECTURE / RUNBOOK / features/* / 研究记录` 中可提炼的高频规则
- [ ] 首批沉淀 `trading-calendar-guard`、`feature-readiness-audit`、`train-split-discipline`、`shadow-execution-planner`
- [ ] 为首批 skill 明确触发条件、workflow、输出契约、阻断条件
- [ ] 建立 skill 与底层 Python 入口的映射表，避免规则层与执行层脱节
- [ ] 定义 `md + json` 的统一输出契约，便于人读与 agent 续接

### 6. daily ops 产品化与执行桥接准备

目标：
- 让盘前 / 盘后流程从“能跑”进化到“可运营”
- 为后续 WSL 生产脚本与 Windows MiniQMT 执行桥接预留稳定输入输出

当前项：
- [x] 盘前 checklist 结构化
- [x] 盘后 checklist 结构化
- [x] 生成 daily plan 时附带模型版本 / 数据状态
- [x] real/shadow reconciliation 结果结构化输出
- [ ] 明确空 plan、账户异常、数据不齐时的处理策略
- [ ] 定义 `order_intents` 产物契约，作为 WSL -> Windows bridge 的固定输入

---

## 下一阶段

### A. feature 拉齐后的研究闭环

- [ ] 跑完 extended 相对 baseline 的消融研究
- [ ] 补新特征候选池：行业/风格暴露、波动与换手稳定性、价格效率、财务质量衍生项
- [ ] 区分“可直接纳入生产候选”和“仅研究观察”的新特征层级

### B. rolling backtest 产品化

- [ ] 固化训练窗 / 测试窗 / 滚动步长默认值
- [ ] 输出分窗口收益、回撤、换手、胜率、风格漂移摘要
- [ ] 支持和 strict eval / production 模型做横向比较

### C. 运行可观测性完善

- [ ] 将 feature coverage / readiness / graceful degradation 汇总到统一 run report
- [ ] 把 report 保存路径、关键 artifact、下一步动作明确写入 CLI 末尾摘要
- [ ] 为“空 plan、账户异常、数据不齐”补清晰处理策略与人机分工

### D. 候选模型晋级流程

- [ ] 重训后自动跑 strict eval + rolling backtest
- [ ] 候选模型自动与 baseline / production 对比
- [ ] 晋级、上线、回滚留下最小审计轨迹

### E. Qsys plugin / workflow layer 首发版本

目标：
- 在不改动核心 engine 的前提下，给 Qsys 增加一层稳定的研究操作接口

待办：
- [ ] 设计 `qsys_plugins/core` 的目录骨架与 manifest
- [ ] 首发 `preopen-plan`、`feature-audit`、`rolling-eval` 三个 command
- [ ] 为 command 补一层薄适配代码，统一调用现有 `scripts/` 或 `qsys/*` 模块
- [ ] 验证 command 输出可同时服务：人工阅读、日报沉淀、agent 二次消费
- [ ] 再决定是否兼容 Claude plugin / OpenClaw skill 等外部宿主格式

### F. MiniQMT bridge / production script 主线

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
- 当前阶段不为了“看起来先进”而继续无约束增加脚本入口
