# 特征系统迁移说明

## 1. 这次命名迁移的重点

这次不是把现有逻辑重写一遍，而是把正式命名从历史标签收束到通用语义。

重点变化是：

- 不再把 `alpha158 / extended / margin_extended / phase1` 当成正式语义层
- 把正式分组统一为：
  - `price`
  - `liquidity`
  - `microstructure`
  - `tradability`
  - `cross_section`
  - `regime`
  - `fundamental`
  - `event`

## 2. 历史名字现在是什么角色

### 2.1 provider / compatibility alias

历史名字仍然保留，但角色已经改变：

- `alpha158`：provider / 兼容训练入口名，不再是正式 taxonomy
- `extended`：兼容训练组合名，不再是正式 taxonomy
- `margin_extended`：兼容训练组合名，不再是正式 taxonomy
- `phase1 / phase12 / phase123`：历史实验入口名，不再是正式 taxonomy

### 2.2 正式推荐替代名

推荐使用的正式 set 名称是：

- `price_volume_expression_core_v1`
- `price_volume_fundamental_core_v1`
- `price_volume_fundamental_event_core_v1`
- `short_horizon_state_core_v1`
- `context_regime_overlay_v1`
- `research_semantic_default_v1`
- `atomic_panel_core_v1`
- `atomic_panel_plus_state_v1`
- `mixed_provider_demo_v1`

## 3. 代码兼容层怎么处理

### 3.1 `FeatureLibrary`

`FeatureLibrary` 仍保留旧方法名：

- `get_alpha158_config`
- `get_alpha158_extended_config`
- `get_alpha158_margin_extended_config`
- `get_research_phase1_config`
- `get_research_phase12_config`
- `get_research_phase123_config`

但内部已经映射到新的语义化 feature set。

### 3.2 builder

builder 仍按历史实现模块组装，但通过 registry 的 `builder_group` 连接到新的正式语义组。

也就是说：

- 正式分类叫 `microstructure`
- 当前实现仍可能来自 `daily_price_state`

## 4. registry 层最关键的变化

### 4.1 group 语义更通用

以前容易把来源名误当组名；现在 `group` 只表达正式业务语义。

### 4.2 `builder_group` 分离实现装配

这是这次迁移的关键过渡层，目的是：

- 不打断现有 builder 逻辑
- 先把正式命名收束正确
- 后续再慢慢把实现文件名和组装逻辑也继续对齐

## 5. 仍保留的兼容 alias

以下名字目前仍可解析，但只作为兼容：

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

## 6. 当前仍未彻底调整的部分

- builder 的实现模块名仍带部分历史痕迹
- `groups/*.py` 仍保留兼容 wrapper
- 训练 CLI 参数名仍保留历史字符串

这些部分本次刻意不激进收束，目的是降低迁移风险。
