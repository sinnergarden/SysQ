# Feature Registry 设计说明

## 1. 设计目标

这套 registry 设计要解决两个核心问题：

1. 正式特征应该有稳定身份，而不是只靠历史字符串引用
2. Qlib 内置特征、自定义特征、事件类特征必须进入同一套正式引用体系

## 2. 为什么采用通用语义分组

当前项目里最容易产生误解的就是历史名字：

- `alpha158`
- `extended`
- `margin_extended`
- `phase1 / phase12 / phase123`

这些名字有历史价值，但不适合做正式 taxonomy。因为它们表达的是：

- provider 名
- 历史组合名
- 研究阶段号

而不是正式业务语义。

因此 registry 里正式采用的分组是：

- `price`
- `liquidity`
- `microstructure`
- `tradability`
- `cross_section`
- `regime`
- `fundamental`
- `event`

## 3. identity 与 implementation 的区分

### 3.1 feature identity

由以下字段表达：

- `id`
- `name`
- `group`
- `business_meaning`
- `modeling_role`
- `temporal_type`

它们描述“这个正式特征是谁”。

### 3.2 feature implementation

由以下字段表达：

- `source_type`
- `provider_ref`
- `provider_expression`
- `qlib_column_name`
- `source_module`
- `generation_method`
- `dependencies`
- `builder_group`（如适用）

它们描述“当前这个正式特征怎么被实现和装配”。

这让我们可以：

- 让历史 provider 名退居 implementation 层
- 让正式 identity 保持更通用、更稳定

## 4. 为什么 feature id 仍然必要

项目已经有多条 feature 链路并行：

- Qlib 原始字段
- Alpha158 表达式
- 自定义 Python builder
- 外部 merge 事件列

如果没有稳定 id，后续很难稳定回答：

- 某次实验到底用了哪些正式特征
- 某个正式特征未来换了实现后身份是否连续
- 多模型引用时怎样避免只记住临时列名

因此每个正式特征都分配 `F0001` 形式的稳定编号。

## 5. 为什么还要 feature set

feature id 解决的是“身份”，feature set 解决的是“正式组合引用”。

当前项目需要 feature set 的原因：

- 训练入口历史名很多
- 研究脚本按 group / flag 选择
- 未来 sequence / Transformer 需要主输入和 side input 的正式组合

因此 feature set 支持：

- `feature_ids`
- `include_groups`
- `include_sets`

## 6. 当前推荐的语义化 feature set

### 6.1 面向当前 tabular 的集合

- `semantic_all_features_v1`
- `price_volume_expression_core_v1`
- `price_volume_fundamental_core_v1`
- `price_volume_fundamental_event_core_v1`

### 6.2 面向研究 builder 的集合

- `short_horizon_state_core_v1`
- `context_regime_overlay_v1`
- `research_semantic_default_v1`

### 6.3 面向未来 sequence 的集合

- `atomic_panel_core_v1`
- `atomic_panel_plus_state_v1`

### 6.4 验证混合来源解析的集合

- `mixed_provider_demo_v1`

## 7. 历史名字如何处理

历史名字现在不再作为主推荐命名，而是兼容 alias：

- `tabular_baseline_v1`
- `tabular_extended_v1`
- `tabular_margin_extended_v1`
- `research_phase1_core_v1`
- `research_context_v1`
- `research_default_v1`
- `sequence_native_core_v1`
- `transformer_core_v1`
- `hybrid_research_demo_v1`
- `legacy_phase1_raw_v1`
- `legacy_phase12_raw_v1`
- `legacy_phase123_raw_v1`

这样可以：

- 保持旧入口不炸
- 逐步把新代码迁到语义化 set 名称

## 8. 如何统一 Qlib 内置特征与自定义特征

统一点在于：

- 两者都有正式 id
- 两者都有正式 group
- 两者都通过 feature set 进入统一解析链路

不同点只体现在 implementation 元信息：

- `qlib_raw_field`
- `qlib_alpha158`
- `custom_python`

这意味着“是不是 Qlib 内置”不再决定引用体系，而只决定生成方式。

## 9. 为什么增加 `builder_group`

正式 group 现在按通用语义命名，例如 `microstructure`、`cross_section`。

但当前 builder 代码仍按历史实现组装：

- `daily_price_state`
- `relative_strength`
- `industry_context`
- `market_regime`
- 等

因此 registry 中增加 `builder_group`：

- `group` 表示正式语义分类
- `builder_group` 表示当前实现层如何装配

这样可以在不破坏现有 builder 的情况下，把正式 taxonomy 提升到更通用的名字。

## 10. 对 future sequence / Transformer 的价值

这套设计的核心价值是：

- 底层仍是 panel-friendly 单日特征表达
- 可以正式区分主序列输入与 side input
- 可以在不重造存储的前提下迁移到 sequence / Transformer
- 历史训练入口和未来新模型入口可以共享同一套 feature identity
