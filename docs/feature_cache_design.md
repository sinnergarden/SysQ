# Feature Cache Design — 两级特征缓存

## 1. 问题描述

当前滚动研究（RollingResearchRunner）在每个窗口上重新计算所有派生特征。对于 67 个滚动窗口，每个窗口覆盖相同的回溯期，导致：

- 同一段数据被反复传递给 `build_phase1_features()` 及其下游的 `build_*_features()` 函数
- 窗口间的计算完全独立，没有跨窗口共享
- 在 67 窗口 × 相同回溯期的典型场景下，约有 **60-67 倍的不必要重复计算**

此外，当前 `qsys/feature/cache.py` 的实现过于粗糙：

- 缓存键仅包含 `(universe, sorted_fields, start, end)`，不区分计算逻辑版本
- 改变 `build_*_features()` 的任何一行代码不会导致缓存失效，脏数据会被重用
- 没有 per-feature 粒度的缓存，只能缓存整个特征矩阵

## 2. 两级缓存设计

设计两层缓存：

### Level 1：Per-Feature 缓存

```
data/feature_cache/features/{feature_id}/{cache_hash}.parquet
```

每列：

| 列名 | 类型 | 说明 |
|------|------|------|
| trade_date | date | 交易日 |
| ts_code | str | 股票代码 |
| feature_value | float64 | 特征值 |

**用途**：缓存单个派生特征的计算结果，供多个特征列表复用。

**适用场景**：
- 计算昂贵的特征（如 `rps_industry_60d` 涉及行业排序）
- 被多个特征列表引用的共同依赖特征
- 计算稳定、变更频率低的特征

### Level 2：Feature Matrix 缓存

```
data/feature_cache/matrices/{feature_list_id}/{feature_set_hash}/panel.parquet
```

这是一个宽表，包含 `trade_date`、`ts_code` 和若干 `feature_value` 列。

**用途**：缓存已解析好的特征矩阵，供滚动窗口直接加载。

**适用场景**：
- 滚动窗口中的第一个窗口计算后，后续窗口如果日期范围重叠则部分命中
- 同一 feature_list_id 在不同实验中重复出现

## 3. 缓存键设计

### 3.1 Per-Feature 缓存键组成

`cache_hash` = SHA256(以下组件的序列化)：

| 组件 | 说明 | 来源 |
|------|------|------|
| `feature_id` | 特征 ID | `FeatureSpec.feature_id` |
| `compute_fn_version` | 计算函数的 git blob hash | `git hash-object qsys/feature/groups/...py` |
| `dependency_hash` | 输入特征/数据的版本哈希 | 依赖链的拓扑哈希 |
| `source_data_format_version` | 源数据 schema 版本 | `data/canonical/daily/` 结构版本号 |
| `universe` | 股票池 | 如 `csi800` |
| `date_ranges` | 数据范围 `[start, end]` | 调用参数 |
| `pit_policy` | PIT 策略标识 | `FeatureSpec.pit_type` |

### 3.2 Feature Matrix 缓存键组成

`feature_set_hash` = SHA256(以下组件的序列化)：

| 组件 | 说明 |
|------|------|
| `feature_list_id` | YAML 中的 `feature_list_id` |
| `sorted_feature_ids` | 排序后的 feature_id 列表 |
| `per_feature_cache_hashes` | 每个特征对应的 per-feature cache hash |
| `universe` | 股票池 |
| `start` | 开始日期 |
| `end` | 结束日期 |
| `pit_policy` | PIT 策略 |

### 3.3 缓存键生成示例

```python
def _feature_cache_key(spec: FeatureSpec, universe: str, start: str, end: str) -> str:
    raw = json.dumps({
        "feature_id": spec.feature_id,
        "compute_fn_version": _git_hash(spec.compute_fn),
        "dependency_hash": _dependency_hash(spec),
        "source_data_version": _source_version(),
        "universe": universe,
        "start": start,
        "end": end,
        "pit_policy": spec.pit_type.value,
    }, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
```

## 4. 缓存失效策略

| 变更场景 | 影响 | 失效行为 |
|----------|------|---------|
| 源数据变更（股价、财报等） | 影响所有 RAW + DERIVED 特征 | `source_data_version` 变化 → cache miss |
| 计算函数变更（git commit） | 影响特定 DERIVED 特征 | `compute_fn_version` 变化 → cache miss |
| 依赖特征变更 | 影响上游 DERIVED 特征 | `dependency_hash` 变化 → 级联 cache miss |
| 按需手动清除 | 所有 | `rm -rf data/feature_cache/` 或选择性删除 |

**级联失效规则**：

```
如果 change_capex_chg_qoq 的 compute_fn 变更：
  → change_capex_chg_qoq 缓存 miss
  → 所有依赖 change_capex_chg_qoq 的特征（如某复合特征）也 miss
  → 包含上述特征的 matrix cache 也 miss
```

级联在缓存查询时惰性计算：查询 `feature_id=X` 时检查 `dependency_hash`，如果任一依赖缓存 miss 则整个链重建。

## 5. 与现有 cache.py 的关系

现有 `qsys/feature/cache.py`：

```python
# 当前实现
_CACHE_ROOT = "data/canonical/features/{universe}/{hash}.parquet"
# 键 = hash(universe, sorted_fields, start, end)
```

对比：

| 维度 | 当前 cache.py | 新 cache_v2.py |
|------|---------------|----------------|
| 粒度 | 整个特征矩阵 | per-feature + matrix 两级 |
| 键要素 | `(universe, fields, start, end)` | `(feature_id, compute_fn_version, dependency_hash, source_data_version, universe, date_range, pit_policy)` |
| 版本感知 | 无 | 通过 git hash 感知代码变更 |
| 级联失效 | 无 | 惰性依赖级联 |
| 并发安全 | N/A（单进程使用）| N/A（单进程，写前检查 + 原子写）|

**新 cache 将替代旧 cache**。迁移期间可双写，但只读新缓存。旧 cache 的 `has()` 和 `load()` 在数据不可用时应 fallback 到新缓存。

## 6. 读写流程

### 6.1 读路径

```
Request: (feature_id, universe, start, end)

1. 计算 cache_hash
2. 检查 data/feature_cache/features/{feature_id}/{cache_hash}.parquet
3. 如果存在：
   a. 检查级联依赖的 cache_hash 是否一致
   b. 一致 → 返回缓存的 parquet
   c. 不一致 → 触发重建（走写路径）
4. 如果不存在：触发重建（走写路径）
```

### 6.2 写路径

```
Compute: (feature_id, compute_fn, dependencies, universe, start, end)

1. 递归解析依赖链（确保所有依赖已缓存或可计算）
2. 调用 compute_fn 生成特征值
3. 写入 data/feature_cache/features/{feature_id}/{cache_hash}.parquet
4. 写入依赖关系索引 data/feature_cache/index.json（可选，用于快速级联查询）
```

## 7. 与日常同步和 qlib 转换的集成

### 7.1 数据同步后的缓存标记

当每日同步脚本（`sync_csi800_daily.py`）完成时，它应该：

1. 递增 `source_data_format_version`（或记录新的版本号）
2. （按需）清理在 `feature_cache/manifest.json` 中标记为 "stale" 的特征缓存
3. **不自动重建**所有缓存——惰性重建，在下次查询时检测 cache miss 即可

```python
# data/feature_cache/manifest.json
{
    "source_data_version": "20260620",  # 最后一个完整 sync 的日期
    "features": {
        "close_to_open_gap_1d": {
            "last_compute_fn_hash": "abc123",
            "last_source_version": "20260619",
            "status": "valid",
            "cache_paths": ["abc.parquet", "def.parquet"]
        },
        ...
    }
}
```

### 7.2 每日推理集成

每日 preopen 流程中，当 DailyRunner 构建特征矩阵时：

1. 查缓存 → 如果命中且所有依赖版本一致 → 直接加载
2. 如果 miss → 计算 → 写缓存 → 返回
3. 结果不应影响推理正确性，仅影响性能

## 8. 与未来 UI 查询的集成

UI 允许按 `feature_id` 查询单特征历史值：

```
GET /api/features/{feature_id}?date_range=2026-01-01,2026-06-01&universe=csi800
```

- 后端直接映射到 `data/feature_cache/features/{feature_id}/` 下的缓存文件
- 不需要重新计算或加载整个特征矩阵
- 这是 per-feature 缓存设计的主要动机之一

## 9. 缓存目录布局

```
data/feature_cache/
├── manifest.json                # 缓存元数据（版本、状态）
├── index.json                   # 特征依赖索引（可选加速）
└── features/
│   ├── close_to_open_gap_1d/
│   │   ├── a1b2c3d4.parquet    # cache_hash → 实际数据
│   │   └── e5f6g7h8.parquet
│   ├── ret_60d/
│   │   └── b2c3d4e5.parquet
│   ...
└── matrices/
    ├── vg_60d_full_pv/
    │   └── f1a2b3c4.parquet    # feature_set_hash → panel parquet
    └── momentum_pv_v1/
        └── d4e5f6g7.parquet
```

## 10. 关键设计决策

1. **Per-feature 优先于 matrix 缓存**。优先缓存单特征，matrix 缓存只是对常用组合的优化。单一特征缓存更灵活，更容易使 UI 按 feature_id 查询。

2. **惰性验证而非主动失效**。每次查询时检查 cache_hash 是否匹配当前版本，而不是在代码变更时主动标记失效。这简化了缓存一致性问题。

3. **计算函数版本 via git hash**。`git hash-object <compute_fn_path>` 提供确定性的函数版本标识，无需手动版本号管理。

4. **parquet 是唯一序列化格式**。一个格式，支持压缩、列式扫描、分区裁剪。

5. **CACHE 不是 SOT**。`data/feature_cache/` 仅用于加速计算，无条件可删除（`rm -rf data/feature_cache/`），不影响任何下游结果。
