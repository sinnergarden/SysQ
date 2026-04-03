# 特征系统总览

## 1. 目标

Qsys 当前的特征系统不再把 `alpha158 / extended / phase1` 这类历史名字当成正式语义层，而是把所有正式可训练特征收束到一套通用建模分类中。

重构后的目标是：

- 用通用语义而不是历史包袱命名特征层
- 统一 Qlib 内置特征、自定义派生特征和事件类特征
- 保持 panel-friendly 单日表达作为底层 source of truth
- 通过 feature registry 与 feature set 稳定引用正式特征
- 为 tabular 与 sequence / Transformer 保留统一底层输入体系

## 2. 正式分类

当前正式采用的语义分类是：

- `price`
- `liquidity`
- `microstructure`
- `tradability`
- `cross_section`
- `regime`
- `fundamental`
- `event`

这八组不是按历史来源分，而是按业务语义和建模用途分。

### 2.1 为什么不用 `alpha158 / extended / margin_extended / phase1`

因为这些名字描述的是历史入口，不是长期语义：

- `alpha158` 只是 Qlib 内置特征包名，不等于正式业务分类
- `extended` 只表示“比 baseline 多一点”，本身没有语义
- `margin_extended` 只是历史组合名，不说明里面到底多了什么
- `phase1 / phase12 / phase123` 更像研究阶段号，不适合作为长期 feature taxonomy

因此它们现在只保留为兼容别名，不再作为主文档的正式分类名称。

## 3. 目录结构

```text
qsys/feature/
├── builder.py
├── library.py
├── registry.py
├── selection.py
├── transforms.py
├── definitions/
└── groups/         # 历史兼容 wrapper
```

```text
features/registry/
├── feature_registry.yaml
└── feature_sets.yaml
```

```text
docs/features/
├── README.md
├── feature_system.md
├── feature_registry_design.md
├── migration_notes.md
├── feature_system_refactor.md
└── groups/
    ├── price.md
    ├── liquidity.md
    ├── microstructure.md
    ├── tradability.md
    ├── cross_section.md
    ├── regime.md
    ├── fundamental.md
    └── event.md
```

## 4. registry 与 feature set

### 4.1 registry

正式 source of truth：`features/registry/feature_registry.yaml`

registry 记录的是“正式 feature identity”，而不是脚本里的临时列名。每个 feature 至少有：

- `id`
- `name`
- `group`
- `business_meaning`
- `modeling_role`
- `temporal_type`
- `source_type`
- `provider_ref`
- `dependencies`
- `generation_method`
- `need_norm`
- `norm_method`
- `tabular_ready`
- `sequence_ready`
- `qlib_column_name`

### 4.2 feature set

正式 source of truth：`features/registry/feature_sets.yaml`

当前推荐的语义化 set 包括：

- `semantic_all_features_v1`
- `price_volume_expression_core_v1`
- `price_volume_fundamental_core_v1`
- `price_volume_fundamental_event_core_v1`
- `short_horizon_state_core_v1`
- `context_regime_overlay_v1`
- `research_semantic_default_v1`
- `atomic_panel_core_v1`
- `atomic_panel_plus_state_v1`
- `mixed_provider_demo_v1`

历史名字如 `tabular_extended_v1`、`research_phase1_core_v1`、`transformer_core_v1` 仍保留兼容，但不再作为主命名推荐。

当前如果需要一个“全正式特征都用上”的语义化基线，优先使用：

- `semantic_all_features_v1`

## 5. 如何统一适配 Qlib 内置与自定义特征

统一方式不是把所有实现做成一样，而是把它们都纳入同一套正式 identity：

- Qlib 原始字段：`source_type=qlib_raw_field`
- Qlib Alpha158：`source_type=qlib_alpha158`
- 自定义 Python builder：`source_type=custom_python`

解析层会把 feature set 拆成：

- `native_qlib_fields`
- `derived_columns`
- `required_groups`

因此不同来源的正式特征已经进入同一套引用体系。

## 6. tabular 与 sequence 的兼容策略

### 6.1 tabular

更适合重点消费：

- `microstructure`
- `liquidity`
- `cross_section`
- `fundamental`
- 一部分 `event`

### 6.2 sequence / Transformer

更适合重点消费：

- `price`
- `liquidity` 中原子输入
- `tradability`
- 一部分 `microstructure`
- `fundamental / regime / event` 作为 side input

sequence 样本应在 dataset / builder 视图阶段切窗，而不是另做一套底层存储。

## 7. 如何新增一个正式 feature

1. 先判断它在八个正式语义组里属于哪一组
2. 再判断它更像 atomic / aggregated / context / regime / event / interaction
3. 在实现层补逻辑
4. 注册进 `feature_registry.yaml`
5. 如需被研究或训练正式引用，再加入 `feature_sets.yaml`
6. 更新分组文档与测试

## 8. 相关文档

- `docs/features/feature_system.md`
- `docs/features/feature_registry_design.md`
- `docs/features/migration_notes.md`
- `docs/architecture/feature_refactor_plan.md`
- `docs/research/feature_refactor_inventory.md`
- `docs/research/feature_refactor_validation.md`
