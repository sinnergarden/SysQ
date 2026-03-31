# Feature Refactor Plan

## 1. 这次第二轮收束的重点

在第一轮把 registry、feature set 和代码边界搭起来之后，这一轮的重点是把正式命名从历史名字收束到通用语义。

也就是说：

- 结构保持稳
- 兼容入口保留
- 正式 taxonomy 进一步去历史化

## 2. 正式 taxonomy

正式采用八类通用语义组：

- `price`
- `liquidity`
- `microstructure`
- `tradability`
- `cross_section`
- `regime`
- `fundamental`
- `event`

## 3. 为什么这样比 `alpha158 / extended / phase1` 更好

### 3.1 可读性更高

维护者一眼能知道：

- `price` 是价格
- `event` 是事件/行为
- `regime` 是市场环境

而不需要先知道某个历史实验分期。

### 3.2 兼容未来多模型

这套名字天然适合：

- tree model
- tabular linear model
- sequence / Transformer

因为它们表达的是特征语义，而不是某个 provider 或实验阶段。

### 3.3 能把 provider 和语义拆开

- `alpha158` 仍然保留在 `source_type / provider_ref`
- 正式 group 改为 `microstructure / liquidity / cross_section`

这样 provider 名和 feature 语义不再混淆。

## 4. 实现策略

### 4.1 正式 group 与 builder_group 分离

本次不激进重写 builder 代码，而是：

- `group`：正式语义分组
- `builder_group`：当前实现装配路径

例如：

- 正式 group = `microstructure`
- builder_group = `daily_price_state`

这样可以：

- 新文档和 registry 用通用名字
- 旧实现仍保持稳定

### 4.2 语义化 feature set 为主，历史 set 名为兼容 alias

新的主推荐 set：

- `price_volume_expression_core_v1`
- `price_volume_fundamental_core_v1`
- `price_volume_fundamental_event_core_v1`
- `short_horizon_state_core_v1`
- `context_regime_overlay_v1`
- `research_semantic_default_v1`
- `atomic_panel_core_v1`
- `atomic_panel_plus_state_v1`
- `mixed_provider_demo_v1`

历史名字保留兼容：

- `tabular_baseline_v1`
- `tabular_extended_v1`
- `tabular_margin_extended_v1`
- `research_phase1_core_v1`
- `research_default_v1`
- `transformer_core_v1`
- 等

## 5. 代码层落点

### 5.1 保持不动的部分

- `definitions/` 主实现目录
- `groups/` 兼容 wrapper
- 训练 CLI 参数名

### 5.2 收束的部分

- `feature_registry.yaml` 中的正式 group
- `feature_sets.yaml` 中的主推荐 set 名称
- `FeatureLibrary` 对训练入口的 feature set 映射
- 文档里的主推荐名称
- 解析层输出与测试

## 6. 风险控制

- 不改交易逻辑
- 不改 label
- 不改回测规则
- 不激进重命名已有 feature 列名
- 不要求一次性把实现文件名也全部改成新语义

## 7. 对 future sequence / Transformer 的意义

这轮收束后，未来做 sequence / Transformer 时，正式表达会更自然：

- `price / liquidity / tradability` 作为原子主输入
- `microstructure` 作为状态增强
- `cross_section / regime / fundamental / event` 作为辅助输入或 context

而不必先把历史名字翻译一遍再理解。
