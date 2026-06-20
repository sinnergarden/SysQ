# Feature Cache Design — Transform-Level Cache

## 1. 设计定位

Feature cache 是 **内部实现细节**，用户不需要关心。用户的唯一入口是 FeatureSet YAML，cache 是 Resolver + Builder 自动使用的加速机制。

## 2. 问题

滚动研究中 67 个窗口覆盖相同历史期，每个窗口重复计算同样的 derived feature：

```
Window 1: raw → 60d return, 20d avg, margin_chg_60d, ...
Window 2: raw → 60d return, 20d avg, margin_chg_60d, ...  (重复)
...
Window 67: raw → 60d return, 20d avg, margin_chg_60d, ... (重复)
```

约 60-67 倍不必要的重复计算。

## 3. 两级缓存设计（按优先级）

### Level 1：Transform-level cache（主路径）

每个 `TransformSpec` 如果 `cache_scope="panel"`，其计算结果可缓存。

**路径**：
```
data/feature_cache/transforms/{transform_id}/{cache_key}.parquet
```

**列**：`trade_date, ts_code, feature_1, feature_2, ...`（该 transform 产出的所有 feature）

**用途**：对于 `build_relative_strength_features`、`build_margin_features` 等昂贵且稳定的 transform，一次计算后 67 个窗口复用。

**Cache key** = SHA256(transform_id + compute_fn_hash + inputs_hash + source_hash + window_start + window_end)[:16]

### Level 2：Matrix cache（可选优化）

缓存已展开的完整特征矩阵。

**路径**：
```
data/feature_cache/matrices/{feature_list_id}/{matrix_hash}.parquet
```

**列**：`trade_date, ts_code, feature_1, feature_2, ...`（全量）

**用途**：同一 feature_list_id 在多个实验中重复出现时的优化。

### Per-feature cache（未来扩展）

当前不作为主路径，留作扩展。

## 4. Cache key 组成

| 组件 | 来源 | 说明 |
|---|---|---|
| `transform_id` | TransformSpec | 唯一的 transform 标识 |
| `compute_fn_hash` | git blob hash | 代码变更时自动失效 |
| `dependencies_hash` | inputs 的版本哈希 | 依赖变更时级联失效 |
| `source_data_version` | sync 流水线 | 源数据更新时失效 |
| `date_range` | 调用参数 | 窗口范围 |
| `universe` | 调用参数 | 股票池 |

### 示例

```python
def _transform_cache_key(tspec: TransformSpec, universe: str, start: str, end: str) -> str:
    raw = {
        "transform_id": tspec.transform_id,
        "compute_fn": _git_hash(tspec.compute_fn),
        "inputs_hash": _dependency_hash(tspec),
        "source_version": _source_version(),
        "universe": universe,
        "start": start,
        "end": end,
    }
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()[:16]
```

## 5. 失效策略

**无显式过期**，设计为惰性验证：

| 变更 | cache hash 变化 | 效果 |
|---|---|---|
| 修改 builder 代码 | compute_fn_hash | cache miss，自动重建 |
| 依赖 feature 修改 | inputs_hash | cache miss，级联重建 |
| 源数据更新 | source_version | cache miss |
| 日期范围扩大 | date_range | cache miss |

## 6. 缓存的 Promise

```
CACHE IS NOT SOT — rm -rf data/feature_cache/ 安全，不影响任何下游结果。
```

## 7. 与当前 cache.py 的关系

现有 `qsys/feature/cache.py` 作为粗粒度全矩阵缓存保留。新 cache 实现时替代它。

## 8. 实现计划

- Phase 1（PR #189）：设计文档（done）
- Phase 2（PR #190）：Resolver + Manifest（done）
- Phase 3（PR #191，当前）：Cache key / path / metadata + CLI plan + manifest 集成（key only，不写 parquet）
- Phase 4：Builder 集成 + 真实 parquet 写入 + rolling research 接入

## 9. 当前实现（Phase 3）

| 模块 | 状态 |
|---|---|
| `qsys/feature/cache.py` | ✅ CacheKey, FeatureCacheContext, key/path/metadata |
| `manifest.py` cache section | ✅ 兼容旧 schema，可选 cache key 记录 |
| `scripts/dev/plan_feature_cache.py` | ✅ CLI：生成 cache plan JSON |
| Per-feature cache | ❌ 未实现（future extension） |
| 真实 parquet 写入 | ❌ 本 PR 不做 |
| Builder 集成 | ❌ Phase 4 |

### Cache key 组成

**Transform cache key** = SHA256(
    transform_id,
    input_features (order-sensitive),
    output_features (order-sensitive),
    compute_fn_hash,
    source_manifest_hash,
    date_start, date_end,
    universe,
    pit_policy_hash,
    builder_hash,
)[:20]

**Matrix cache key** = SHA256(
    feature_set_id,
    resolved_features (order-sensitive),
    required_transforms (order-sensitive),
    source_manifest_hash,
    builder_hash,
    date_start, date_end,
    universe,
    pit_policy_hash,
)[:20]

### Cache plan CLI

```bash
python scripts/dev/plan_feature_cache.py \
  --feature-set value_growth_multibagger_v3a_features \
  --source-manifest-hash src_v1 \
  --date-start 2018-01-01 --date-end 2025-12-31 \
  --universe csi800 \
  --output-dir artifacts/feature_cache_plans
```

输出 `artifacts/feature_cache_plans/{feature_set_id}.json`，包含 matrix_cache_key、transform_cache_keys、各 cache path。不写 parquet。
