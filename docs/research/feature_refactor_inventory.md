# Feature Refactor Inventory

## 1. 现状核心判断

当前 Qsys 的 feature 体系最大的问题不是“缺 feature”，而是“正式命名长期被历史名字绑架”。

最典型的历史名字包括：

- `alpha158`
- `extended`
- `margin_extended`
- `phase1 / phase12 / phase123`

这些名字在历史上各有来历，但并不适合作为长期的正式 taxonomy。因为：

- `alpha158` 是 provider 名，不是业务语义
- `extended` 只表达“比 baseline 多一点”，没有实际含义
- `margin_extended` 是组合名，不说明多出来的到底是什么
- `phase1 / phase12 / phase123` 更像研究阶段号，而不是正式特征层

## 2. 当前 feature 资产实际上在表达什么

### 2.1 Qlib 原生输入层

由 `qsys/feature/library.py` 承接，主要包含：

- Alpha158 内置表达式
- 估值 / 市值 / 财务 / 资金流原始扩展
- 两融原始扩展

这些都属于正式可训练 feature，但以前常被历史组合名掩盖了真实语义。

### 2.2 研究派生层

由 `qsys/feature/builder.py` 和 `qsys/feature/definitions/*.py` 承接，主要表达：

- 单日价格状态
- 流动性压缩
- 可交易性约束
- 横截面强弱与相对位置
- 市场环境
- 财务慢变量背景

这些特征在业务上其实已经可以归入更通用的语义组。

## 3. 对当前命名的具体判断

### 3.1 `alpha158`

它的真实含义不是一个独立的正式业务组，而是：

- Qlib 提供的一套价格量能表达式集合
- 其中既有更接近 `microstructure` 的，也有更接近 `liquidity` 的，还有更接近 `cross_section` 的

因此把整包都叫 `alpha158`，会让读者误以为它是统一语义层。

### 3.2 `extended`

它的真实含义更接近：

- 在价格量能表达式之外，再加入基本面、估值和资金流原始扩展

因此长期更适合用语义名来描述，而不是 `extended`。

### 3.3 `margin_extended`

它的真实含义更接近：

- 价格量能表达式
- 基本面/估值/资金流扩展
- 再加事件/两融扩展

因此长期语义上更接近 “event-enriched” 而不是 `margin_extended`。

### 3.4 `phase1 / phase12 / phase123`

它们当前更多是历史实验入口名，不应再承担正式 feature 层的命名职责。

## 4. 当前更通用的语义分类应该是什么

基于已有代码和业务语义，我判断更自然的正式分组是：

- `price`
- `liquidity`
- `microstructure`
- `tradability`
- `cross_section`
- `regime`
- `fundamental`
- `event`

理由是：

- 这些名字不依赖某个具体 provider 或某次历史实验
- 对未来 sequence / Transformer 也更自然
- 更容易让维护者看懂“这个特征到底在描述什么”

## 5. 当前组织方式为什么会阻碍未来模型演进

### 5.1 provider 名和 feature 语义混在一起

如果继续沿用 `alpha158 / extended / phase1` 这类名字，未来迁移到 sequence / Transformer 时会很难回答：

- 哪些是原子 panel 输入
- 哪些是压缩比较层
- 哪些是 context / regime
- 哪些是事件/行为输入

### 5.2 正式组名和实现文件名没有分开

当前实现文件名里有历史痕迹，但正式分类需要更通用语义。

因此需要把：

- 正式 `group`
- 当前 `builder_group`

分开，才能稳妥迁移。

## 6. 结论

这次第二轮命名收束的核心结论是：

- 正式 taxonomy 应该从历史组合名切换到通用语义名
- 历史名字只保留兼容
- provider 名放到 implementation metadata，不再当正式 feature group
- 这样更利于后续 tabular / sequence 共用同一套正式 feature identity
