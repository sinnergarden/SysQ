# Qsys 特征系统说明

本文档定义 Qsys 信号特征部分的长期目标、工程边界与推进方式。后续所有 feature 相关开发，默认以本文件为准。

## 1. 总目标

Qsys 的目标不是只维护少量简单因子，而是建设一套**可持续演进、可增量更新、可训练、可审计**的特征系统。

核心流程应固定为：

1. 拉取 raw 原始信息
2. 基于 raw 做 feature engineering
3. 转换为训练/推理友好的 bin 或等效特征存储
4. 训练模型
5. 在研究环境中做回测、ablation 与筛选
6. 通过后再考虑晋级到 production

## 2. 工程设计原则

### 2.1 raw 与 feature engineering 分离
- raw 层只负责“原始信息获取与存储”
- feature engineering 层只负责“基于 raw 生成组合特征”
- 不把派生逻辑混在 raw 增量拉取里

### 2.2 feature list 必须有唯一权威表
需要维护一份完整 feature list 表，且**只能增量更新**。

每条 feature 至少记录：
- `feature_id`：编号
- `feature_name`：字段名
- `feature_name_zh`：中文名
- `group`：所属特征组
- `source_layer`：raw / derived / context / sequence
- `data_source`：Tushare 接口或内部派生来源
- `calculation`：计算方式
- `alignment_rule`：对齐规则
- `missing_rule`：缺失处理方式
- `normalization_rule`：标准化方式
- `neutralization_rule`：是否行业/市值中性
- `status`：draft / active / blocked / deprecated
- `notes`

补充约束：
- 这份 feature list 是字段层权威表，但不等于完整研究对象模型。
- 后续 factor governance 应在该表之上继续区分：`FactorDefinition`、`FactorVariant`、`FactorBundle`。
- 也就是说，feature list 解决“字段是什么”，但不能单独解决“哪个 variant 被验证过、哪个 bundle 在主线、哪个结论属于哪次 experiment”。

### 2.3 支持标量特征和序列特征
Qsys 的特征系统不能只支持 float 标量。

需要兼容两类输入：
- `float`：标准横截面因子、估值、财务、上下文特征
- `list / sequence`：例如多日 OHLCV、成交量、资金流序列，为后续序列模型预留接口

这意味着特征注册和特征存储层要区分：
- 标量列
- 序列列/列表列

### 2.4 增量更新要方便
全流程应支持方便的增量更新：
- raw 可增量补拉
- feature engineering 可按日期增量重算
- bin 可局部更新，而不是每次全量重建

### 2.5 模型必须说明自己用了哪些特征
每个模型目录都需要有一份小文件，说明：
- 使用了哪些 feature / feature group
- 训练时间范围
- 训练 / 验证 / 测试切分
- 适用场景

## 3. 研究策略

### 3.1 先尽量把可用特征收集全
对 Tushare 能稳定拿到的接口，不应只挑少量简单特征，而应先尽可能较全地纳入候选池。

### 3.2 再按业务类型筛选
特征收集全后，再按以下维度批量检查：
- 覆盖率
- 缺失率
- 是否有行业结构性缺失
- 是否适合日频模型
- 是否适合 context / filter，而非主信号
- 是否适合序列建模

### 3.3 研究与生产分离
新增特征、研究实验、ablation、训练 candidate model 时，不应默认修改 production manifest。

正确流程：
1. 研究态接入 feature
2. 训练 research artifact
3. 回测 / ablation / strict eval
4. 通过门槛后再晋级 production

## 4. 当前主线

当前阶段性目标是：
- 全量拉到我们能拿到的原始特征信息
- 把 raw 校验口径收干净
- 在 raw 之上做统一 feature engineering
- 转换为 bin，供模型训练
- 再分批回测看效果

在此之前，不应因为局部实验结果好看，就提前下结论。

## 5. 当前已识别的关键问题

- raw 主列可能因 merge 产生 `close_x/close_y` 等别名，必须在校验层统一修复
- 部分 warning 可能来自旧字段名、事件稀疏列、金融行业天然缺列，不应误判为主链路故障
- `phase1/phase12/phase123` 这类研究 feature set 若未真正进入训练矩阵，就不能假装已经完成正式验证

## 6. 开发要求

后续 feature 开发默认遵守：
- 先补 feature list，再写代码
- 先确认 raw 是否真有、字段是否稳定，再做派生
- 先做覆盖率与 readiness 审计，再进训练
- train / test 默认不 overlap
- 研究结果必须说明特征实际有没有进入训练矩阵
- 日常运维也按同一套口径执行：raw 更新、feature engineering、bin、readiness audit、训练/推理，不能只在研究阶段检查填充率与对齐

## 6.1 日常运维最低检查项
- 核心行情列不得出现系统性缺失
- qlib/bin 必须实际可读，不接受“日期对齐但无 feature rows”
- 训练前至少保留一份 feature readiness audit 结果
- 对 fake warning（旧字段名、事件稀疏列、行业天然缺列）与真实缺失要区分处理

## 7. 文档关系

- `docs/features/feature_system.md`：特征系统长期说明（本文件）
- `docs/research/feature_gap_analysis.md`：当前特征缺口与实现落点分析
- `docs/research/feature_groups_phase2.md`：Phase 2 说明
- `docs/research/feature_groups_phase3.md`：Phase 3 说明
- `docs/research/feature_backtest_report.md`：正式实验与回测汇总

后续若 feature 系统设计发生长期变化，应优先更新本文件，再更新具体实验文档。

当前与 factor governance 相关的补充文档：
- `docs/features/factor_governance_and_research_migration.md`
- `docs/features/factor_governance_pr_plan.md`
