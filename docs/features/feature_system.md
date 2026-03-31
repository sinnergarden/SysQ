# Qsys 特征系统长期说明

## 1. 长期判断

Qsys 现在不再把 `alpha158 / extended / phase1` 这类历史名字视为长期 feature 分类，而把正式可训练特征统一收束到一套通用建模语义中：

- `price`
- `liquidity`
- `microstructure`
- `tradability`
- `cross_section`
- `regime`
- `fundamental`
- `event`

这套分类的意义是：

- 面向业务语义
- 面向建模角色
- 不被历史入口名绑架
- 能同时服务 tabular 与 sequence / Transformer

## 2. 为什么要抛开历史名字

### 2.1 `alpha158`

`alpha158` 是 provider 名，不是长期语义。里面既有：

- 更接近微观结构的表达
- 更接近流动性的表达
- 更接近横截面比较和压缩统计的表达

把整包都叫 `alpha158`，不利于未来多模型演进。

### 2.2 `extended`

`extended` 只表达“比 baseline 多一点”，但不告诉维护者多出来的是：

- 基本面
- 估值
- 资金流
- 还是事件类特征

因此它不能充当长期 taxonomy。

### 2.3 `margin_extended`

`margin_extended` 仍然是历史组合名，不是业务语义；现在更准确的长期语义是把它理解为“价格量能表达 + 基本面 + 事件/两融扩展”。

### 2.4 `phase1 / phase12 / phase123`

这些名字更像研究分期或实验阶段号，不适合作为正式 feature identity 和长期 feature set 名称。

## 3. 当前长期边界

### 3.1 source of truth 仍是 panel-friendly 单日特征表达

正式特征底层仍尽量保持为：

- `(datetime, instrument) -> feature columns`

sequence / Transformer 的窗口样本应在 dataset / builder 视图阶段切出，而不是另建一套底层 feature 存储。

### 3.2 正式 feature 统一进入 registry

无论特征来自：

- Qlib 原始字段
- Qlib Alpha158 内置表达式
- 自定义 Python builder
- 外部 merge 后的事件/行为列

只要它是模型训练时可直接引用的正式特征，就应该进入同一套 registry。

## 4. 八类正式语义组的长期含义

### 4.1 `price`

原生价格输入主层，未来 sequence 主通道优先来源。

### 4.2 `liquidity`

成交量、成交额、换手与量能冲击；原子输入与压缩特征并存。

### 4.3 `microstructure`

单日 K 线与日内结构表达，描述“今天怎么走出来”。

### 4.4 `tradability`

停牌、涨跌停、可交易性与执行约束。

### 4.5 `cross_section`

横截面相对强弱、分位、相对指数/行业的比较表达。

### 4.6 `regime`

市场广度、风格代理、波动环境等系统性上下文。

### 4.7 `fundamental`

估值、规模、财务质量与慢变量背景。

### 4.8 `event`

资金行为、两融和其他稀疏事件/行为输入。

## 5. 对未来 sequence / Transformer 的约束

### 5.1 哪些更适合作为主输入

优先：

- `price`
- `liquidity` 中原子输入
- `tradability`
- 一部分 `microstructure`

### 5.2 哪些更适合作为 side input / context

优先：

- `regime`
- `fundamental`
- `event`
- `cross_section` 中压缩和比较类表达

### 5.3 为什么不把所有东西都变成 sequence feature

因为很多 formal feature 已经是：

- rolling 压缩
- rank
- spread
- regime proxy

它们对 tabular 很有用，但不应误当成 sequence 主通道。

## 6. 与训练链路的关系

当前训练入口仍保留历史参数名，例如：

- `alpha158`
- `extended`
- `margin_extended`
- `phase1 / phase12 / phase123`

但这些名字在内部已映射到语义化 feature set。长期方向是：

- 对外逐步以正式 feature set 名称为主
- 历史名字仅保留兼容

## 7. 文档关系

- `docs/features/README.md`：日常使用总览
- `docs/features/feature_registry_design.md`：registry / feature set 设计
- `docs/features/migration_notes.md`：迁移说明
- `docs/architecture/feature_refactor_plan.md`：重构方案
- `docs/research/feature_refactor_inventory.md`：现状盘点
- `docs/research/feature_refactor_validation.md`：验证结果
