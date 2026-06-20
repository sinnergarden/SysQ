# Feature Registry Design — 分层架构

## 1. 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  User-facing layer                                      │
│  FeatureSet YAML (configs/features/*.yaml)              │
│    • 只支持 features list / extends + add_features      │
│    • 不支持 exclude_features / exclude_groups           │
│    • 声明 = 承诺：所有 feature 必须产出，缺者 fail      │
└───────────────────────┬─────────────────────────────────┘
                        │
                        v  (internal, auto)
┌─────────────────────────────────────────────────────────┐
│  Resolver / BuildPlan                                   │
│    • 读 YAML → 唯一特征列表                              │
│    • 查 registry → feature_id → FeatureSpec              │
│    • 拓扑排序 → build_plan (需跑哪些 transform)          │
│    • 校验：broken/missing/deprecated → fail fast         │
└──────┬──────────────────────────────┬───────────────────┘
       │                              │
       v                              v
┌──────────────┐          ┌──────────────────────┐
│ Transform    │          │ Cache                │
│ (compute)    │◄─────────│ • transform-level     │
│              │          │   (scope=panel)       │
│              │          │ • matrix-level        │
│              │          │ • per-feature (未来)   │
└──────┬───────┘          └──────────────────────┘
       │
       v
┌─────────────────────────────────────────────────────────┐
│  Manifest (audit only)                                  │
│    • final_features, required_transforms                │
│    • cache hits/misses, source_hash, builder_hash       │
│    • 若 final columns ≠ YAML resolved → 构建失败        │
└─────────────────────────────────────────────────────────┘
```

### 核心原则

1. **用户只能看到 FeatureSet YAML**。FeatureSpec、TransformSpec、Resolver、Cache、Manifest 全是内部实现细节。
2. **YAML 声明 = 承诺**。所有 feature 必须产出，缺字段、缺依赖、broken/deprecated 都 fail fast。
3. **只能做加法**。`extends + add_features` 模式，不支持减法。需要 ablation 就新建显式 YAML。
4. **Manifest 只用于审计和复现**，不用于容错。
5. **旧 YAML 输出列不变**（除非明确标记为 bugfix/migration）。

## 2. 当前状态（Phase 1）

| 组件 | 状态 |
|---|---|
| FEATURE_GROUPS (v1 dict) | 运行中，builder 仍通过 flag dispatch 使用 |
| FeatureSpec (v2 skel) | ✅ 定义完成，partial sample specs |
| TransformSpec | ✅ 定义完成，未填充 |
| Resolver / BuildPlan | ❌ 未实现（Phase 2） |
| Cache | ❌ 未实现（Phase 3） |
| Manifest | ❌ 未实现（Phase 4） |
| FeatureSet YAML → builder 直连 | 运行中，旧路径 |

当前的迁移过渡路径：

```
YAML features list  ───→  (旧路径)  resolver.py expand → builder flag dispatch
                               (新路径，Phase 2+)  resolver_v2 → FeatureSpec → build_plan → cache → manifest
```

两条路径并存直到 Phase 4。

## 3. FeatureSpec 设计（内部）

```python
@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str          # 永久稳定标识符
    name: str                # DataFrame 列名
    group: str               # 所属组
    kind: "raw" | "derived" # 内部分类
    source: str | None       # 数据源或实现模块
    dependencies: tuple[str, ...]  # 直接依赖
    compute_fn: str | None   # 计算函数
    dtype: str | None        # 期望 dtype
    pit_type: "point_in_time" | "rolling_past" | "cross_sectional" | "static"
    cache_scope: "none" | "panel"
    status: "active" | "experimental" | "deprecated" | "broken"
    description: str
    owner: str | None
```

FeatureSpec 是 **registry 中某个 feature 的完整元数据描述**，但**用户不直接接触它**。FeatureSet YAML 里的 feature name 被 Resolver 翻译为 FeatureSpec 查询，所有校验在 Resolver 层完成。

## 4. TransformSpec 设计（内部）

```python
@dataclass(frozen=True)
class TransformSpec:
    transform_id: str        # 如 "build_microstructure"
    inputs: tuple[str, ...]  # 读取哪些 feature
    outputs: tuple[str, ...] # 产出哪些 feature
    compute_fn: str | None   # 实现函数
    pit_contract: str        # PIT 义务描述
    cache_scope: "none" | "panel"
    dependencies: tuple[str, ...]  # 其他 transform 依赖
```

TransformSpec 描述一个计算单元。Resolver 根据 FeatureSpec 的 `compute_fn` 自动决定需要跑哪些 transform，自动计算拓扑顺序。用户不需要配置 TransformSpec——它由 framework 维护者填充。

## 5. FeatureSet YAML 规范（用户层）

### 旧式（兼容，Phase 1-2）：

```yaml
feature_list_id: value_growth_multibagger_v3a
features:
  - ret_60d
  - ret_120d
  - margin_crowding_score
  - ...
```

### 新式（目标态，Phase 3+）：

```yaml
feature_list_id: vg_v3a_plus_momentum
extends: value_growth_multibagger_v3a   # 继承已有特征集
add_features:
  - industry_ret_20d
  - industry_breadth_20d
```

**规则：**
- `extends` 引用另一个 YAML 的 `feature_list_id`
- `add_features` 只追加，不支持删除
- 不支持 `exclude_features` 或 `exclude_groups`
- 需要 ablation 时，复制 YAML + 手动编辑，不允许运行时减法

### 声明 = 承诺

- YAML 中列出 feature → **必须全部产出**
- 依赖于不存在的 feature → fail fast
- 引用了 status=broken 的 feature → fail fast
- 引用了 status=deprecated 的 feature → 硬警告（不阻断，但必须报告）
- 构建完成后 final columns ≠ resolved features → 构建失败

## 6. Manifest 设计（审计用）

Manifest **不用于容错**。它的唯一用途是**审计和复现**：

```json
{
  "feature_list_id": "vg_v3a_plus_momentum",
  "resolved_at": "2026-06-20T12:00:00Z",
  "resolved_features": ["ret_60d", "ret_120d", "margin_crowding_score", "industry_ret_20d"],
  "final_columns": ["ret_60d", "ret_120d", "margin_crowding_score", "industry_ret_20d"],
  "required_transforms": [
    {"transform_id": "build_relative_strength", "cache_hit": true, "cache_key": "a1b2c3"},
    {"transform_id": "build_margin", "cache_hit": false, "duration_ms": 1200},
    {"transform_id": "build_industry_momentum", "cache_hit": true, "cache_key": "d4e5f6"}
  ],
  "source_hash": "sha256:...",
  "builder_hash": "sha256:...",
  "status": "ok"
}
```

如果 `final_columns ≠ resolved_features`，状态为 `"failed"`，构建失败。

## 7. Resolver 设计（内部）

Resolver 是用户层 → 内部的桥梁：

1. 输入：`feature_list_id`（指向 YAML）
2. 读 YAML → 展开 features 列表（`extends` 递归 + `add_features` 合并）
3. 每个 name → `get_by_name()` 查 FeatureSpec
4. 校验：missing → fail；broken → fail；deprecated → warn
5. 查 TransformSpec 决定 build plan（需要哪些 compute、顺序如何）
6. 输出 BuildPlan（transforms + cache keys）

Resolver 不直接修改 builder 代码路径。在 Phase 4 之前，Resolver 的输出只用于**校验和审计**，builder 仍走 flag dispatch 路径。

## 8. 迁移计划

### Phase 1（本 PR = #189）

- ✅ 盘点 inventory
- ✅ FeatureSpec skeleton（partial specs）
- ✅ TransformSpec skeleton
- ✅ consistency tests
- ✅ 设计文档 + agent checklist

### Phase 2

- Resolver 实现（YAML → FeatureSpec → BuildPlan）
- 全量 FeatureSpec 填充
- `extends + add_features` YAML 格式支持
- 校验集成（YAML 中的 feature 必须可在 registry 中找到）
- Manifest 生成

### Phase 3

- Transform-level cache（scope=panel）
- Matrix cache（可选优化）
- Cache 与 Resolver/BuildPlan 集成

### Phase 4

- Builder 改为由 BuildPlan 驱动
- 旧 flag dispatch 路径标记 deprecated
- 旧 FEATURE_GROUPS 降级为兼容层
