# ROADMAP

## 当前阶段目标

当前阶段的目标，不再只是"把链路跑通一次"，而是把 Qsys 收敛成一个：

- 能稳定做周一到周五运营的系统
- 能持续做投研与回测的系统
- 能把 research / candidate / production 分层管理的系统

阶段完成判定：

- daily ops 有固定主入口与盘前/盘后流程
- 训练 / 回测 / 严格评估有统一口径
- 模型替换有明确晋级门槛与回滚边界
- 重要流程有结构化报告，而不是只看 stdout
- 文档、脚本、测试三者保持同步

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

待办：
- [x] 盘前 checklist 结构化
- [x] 盘后 checklist 结构化
- [x] 生成 daily plan 时附带模型版本 / 数据状态
- [x] real/shadow reconciliation 结果结构化输出
- [ ] 明确空 plan、账户异常、数据不齐时的处理策略

### P0.4 统一 run report

目标：
- 所有关键流程都产出结构化结果

待办：
- [ ] 数据更新 report
- [x] 训练 report
- [ ] 回测 report
- [ ] strict evaluation report
- [x] daily ops report

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
