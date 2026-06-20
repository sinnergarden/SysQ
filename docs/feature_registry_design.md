# Feature Registry Design — FeatureSpec 化

## 1. 背景：当前注册表的问题

当前特征注册表由 `qsys/feature/registry.py` 中的 `FEATURE_GROUPS` 字典管理：

```python
FEATURE_GROUPS = {
    "microstructure": {
        "enabled_by": "enable_microstructure_features",
        "features": ["close_to_open_gap_1d", "open_to_close_ret", ...],
    },
    ...
}
```

这种设计存在以下核心问题：

| 问题 | 后果 |
|------|------|
| 每组的 features 只是一个字符串列表，没有 per-feature 元数据 | 无法表达每个特征的 kind、dtype、dependencies、owner 等属性 |
| 依赖关系隐含在 builder 代码中（`resolver.py` 的 `_REQUIRED_FIELDS` 是独立维护的硬编码字典）| 容易产生 registry 与 builder 的不一致 |
| 没有 raw / derived 的显式区分 | 无法自动推断计算顺序，也无法检测循环依赖 |
| 没有 status 字段（active / experimental / deprecated / broken）| 无法安全管理特征生命周期，broken 特征可能被误用 |
| feature_id 和 name 混为一谈 | 重命名会导致下游查询断裂（如 UI 持久化的 feature_id）|
| 无法表达 compute_fn 或 cache_scope | 缓存系统无法知道哪些特征可缓存、如何缓存 |

## 2. FeatureSpec 设计

引入 `FeatureSpec` 数据类作为每个特征的唯一描述。

### 2.1 数据结构

```python
@dataclass(frozen=True)
class FeatureSpec:
    # ── 标识 ──
    feature_id: str          # 永久稳定标识符，一旦分配永不改变
    name: str                # DataFrame 中的实际列名，可随设计调整

    # ── 分类 ──
    group: str               # 所属组名，对应 FEATURE_GROUPS 的 key
    kind: FeatureKind        # FeatureKind.RAW 或 FeatureKind.DERIVED

    # ── 来源和依赖 ──
    source: str              # 数据来源描述，如 "tushare/stock_daily", "qlib/fundamental"
    dependencies: list[str]  # 依赖的 feature_id 列表（仅 DERIVED 类型需要）
    compute_fn: str | None   # 计算函数引用，格式 "module:function"（仅 DERIVED）

    # ── 类型和存储 ──
    dtype: str               # pandas dtype，如 "float64", "int64", "bool"
    pit_type: PITRule        # PIT 规则枚举，见下文
    cache_scope: CacheScope  # CacheScope.NONE / PER_FEATURE / MATRIX

    # ── 生命周期 ──
    status: FeatureStatus    # FeatureStatus.ACTIVE / EXPERIMENTAL / DEPRECATED / BROKEN

    # ── 文档 ──
    description: str         # 人类可读的描述
    owner: str               # 维护者标识
```

### 2.2 辅助枚举

```python
from enum import Enum, auto

class FeatureKind(Enum):
    RAW = "raw"
    DERIVED = "derived"

class FeatureStatus(Enum):
    ACTIVE = "active"         # 生产就绪，纳入所有默认特征列表
    EXPERIMENTAL = "experimental"  # 实验中，需显式启用
    DEPRECATED = "deprecated" # 已废弃，保留兼容性但不再推荐使用
    BROKEN = "broken"         # 已损坏，不能被任何 active feature list 引用

class PITRule(Enum):
    # 见 docs/feature_development.md — 6 条 PIT 规则
    ROLLING_HISTORY_ONLY = "rolling_history_only"
    CROSS_SECTIONAL_BY_DATE = "cross_sectional_by_date"
    INDUSTRY_AGGREGATION = "industry_aggregation"
    FINANCIAL_ANN_DATE = "financial_ann_date"
    QOQ_REPORT_PANEL = "qoq_report_panel"
    PROXY_NAMED = "proxy_named"

class CacheScope(Enum):
    NONE = "none"             # 不缓存（简单原始特征）
    PER_FEATURE = "per_feature"  # 单特征级别缓存
    MATRIX = "matrix"         # 仅矩阵级别缓存
```

### 2.3 核心设计决策

#### feature_id 必须永久不变

- `feature_id` 是特征的**稳定标识符**。下游（UI、缓存、实验索引、数据库）应使用 `feature_id` 引用特征。
- `name` 是 DataFrame 中的实际列名，可以随重构调整。
- 重命名时必须保持 `feature_id` 不变；引入新特征时必须使用新的 `feature_id`。
- 此决策确保查询、缓存和实验结果不会因重命名而断裂。

#### name 是 DataFrame 列名

- `name` 字段对应 builder 在 DataFrame 中插入的列名，也对应 YAML 配置文件和 qlib expression 中的引用名称。
- `name` 可包含字母、数字、下划线。
- 如果某个特征在 DataFrame 中的列名在将来变更，它的 `feature_id` 保持不变，仅 `name` 更新。

#### status 的流动规则

```
BROKEN 只能被人工干预修复，不能自动恢复为 ACTIVE
EXPERIMENTAL → ACTIVE 需要评审
ACTIVE → DEPRECATED 需要通知下游使用者
DEPRECATED → ARCHIVED（从注册表中移除）需设过渡期
```

关键约束：
- **`status == BROKEN` 的特征不能进入任何 active feature list**。`resolve_feature_list()` 和 builder 在看到 `BROKEN` 特征时应跳过或报错。
- 特征标记为 `BROKEN` 时，其缓存必须被标记为无效（见 `feature_cache_design.md`）。

## 3. 实现策略

### 3.1 向后兼容

当前代码路径：

```
FEATURE_GROUPS dict
  → builder.py 根据 feature flag 决定调用哪些 build_* 函数
  → resolver.py 的 FEATURE_FORMULAS / REQUIRED_FIELDS 提供元数据
  → configs/features/*.yaml 提供特征列表
```

新设计在 `registry_v2.py` 中引入 `FeatureSpec` 注册表，**与现有 `FEATURE_GROUPS` 并存**。迁移过程不会破坏任何现有路径。

```python
# qsys/feature/registry_v2.py（新文件）

@dataclass(frozen=True)
class FeatureSpec:
    ...

# 全量注册表实例
FEATURE_REGISTRY: dict[str, FeatureSpec] = {
    "close_to_open_gap_1d": FeatureSpec(
        feature_id="close_to_open_gap_1d",
        name="close_to_open_gap_1d",
        group="microstructure",
        kind=FeatureKind.DERIVED,
        source="tushare/stock_daily",
        dependencies=["close", "open"],
        compute_fn="qsys.feature.groups.microstructure:_gap",
        dtype="float64",
        pit_type=PITRule.ROLLING_HISTORY_ONLY,
        cache_scope=CacheScope.PER_FEATURE,
        status=FeatureStatus.ACTIVE,
        description="Close-to-open gap ratio (prev close / today open)",
        owner="researcher/liuming",
    ),
    ...
}
```

### 3.2 兼容层

提供一个 `FeatureSpecAdapter`，将旧的 `FEATURE_GROUPS` 条目自动包装成 `FeatureSpec`（使用启发式默认值）：

```python
def adapt_group(group_name: str) -> dict[str, FeatureSpec]:
    """将 FEATURE_GROUPS[group_name] 转换为对应的 FeatureSpec 字典。"""
    ...
```

### 3.3 Resolver 改造

新的 resolver (`resolver_v2.py`) 应：

1. 从 `FEATURE_REGISTRY` 接收 `feature_id` 或 `name` 列表
2. 根据 `kind` 和 `dependencies` 拓扑排序
3. 过滤掉 `status == BROKEN` 的特征
4. 返回可用于 builder 的 `(feature_id, name, compute_fn, dependencies)` 元组列表

## 4. 迁移计划

### Phase 1：Inventory（当前 → +1 周）

- 盘点所有现有特征，为每个特征确定 `kind`、`dependencies`、`status`、`dtype`、`pit_type`
- 建立 `feature_id` ↔ `name` 映射表
- 确认无重复 feature_id

### Phase 2：FeatureSpec + Resolver + Tests（+1 周 → +3 周）

- 创建 `qsys/feature/registry_v2.py`，包含 `FeatureSpec`、枚举、全量注册表
- 创建 `qsys/feature/resolver_v2.py`，实现基于 FeatureSpec 的特征解析
- 编写迁移测试：
  - 验证 `FEATURE_REGISTRY` 覆盖 `FEATURE_GROUPS` 中所有特征
  - 验证 `BROKEN` 特征不会出现在特征列表中
  - 验证 `dependencies` 无循环引用
  - 验证 `feature_id` 唯一且无空值

### Phase 3：Cache Integration（+3 周 → +5 周）

- 在 `qsys/feature/cache_v2.py` 中使用 `feature_id` + `compute_fn` 哈希作为缓存键
- 缓存系统消费 `cache_scope` 字段决定缓存策略
- 见 `docs/feature_cache_design.md`

### Phase 4：Builder Refactor（+5 周 → +8 周）

- 将 `builder.py` 改为消费 `FeatureSpec`（通过 resolver_v2）
- 用拓扑排序替代当前的硬编码 flag → group → function 调用链
- 移除旧的 `FEATURE_GROUPS` 路径（保留兼容层，但不作为默认路径）

## 5. 迁移比对表

| 维度 | 当前（FEATURE_GROUPS）| 目标（FeatureSpec）|
|------|-----------------------|-------------------|
| 特征标识 | 仅 name（字符串）| feature_id（永久）+ name（可更换）|
| 类型区分 | 无 | 显式 kind: RAW / DERIVED |
| 依赖声明 | 分散在 resolver.py 的硬编码字典中 | 在 FeatureSpec 中声明 |
| 计算函数 | 隐式通过 builder.py 的 if/else 链 | compute_fn 显式引用 |
| 生命周期管理 | 无 | status 状态机 |
| 缓存策略 | 无 | cache_scope 字段 |
| PIT 规则 | 无 | pit_type 枚举 |
| 可测试性 | 弱 | 强（每个 FeatureSpec 可独立验证）|
