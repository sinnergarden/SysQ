# 特征链路 SOP

## 目标

确保 SysQ 的特征链路在进入训练或盘前推理前，满足：
- raw 已拉齐
- qlib 已转换
- 填充率、对齐口径已检查

## 特征链路在每日同步中的位置

特征链路是 CSI800 日频同步（`sync_csi800_daily.py`）的内嵌步骤，不单独运行。

```
sync_csi800_daily.py
  ├── 拉 raw（Tushare → feather）
  ├── qlib 转换（feature 表达式写入 qlib_bin）  ← 特征工程在此
  └── readiness 检查（6 核心字段 null 率）      ← 特征审计在此
```

### qlib 转换（核心行情字段）

raw 数据 → qlib bin 的转换由 `QlibAdapter` 处理，每日同步 materialize 的是核心行情字段：

```python
adapter = QlibAdapter()
adapter.convert_incremental(since="2026-05-15")   # 快速增量
# 或
adapter.convert_fix(since="2026-05-15")            # 增量失败时 fallback
```

写入 `data/qlib_bin/features/` 的核心字段：
- `$open`, `$high`, `$low`, `$close`, `$volume`, `$factor` — 行情基础
- 衍生特征在 registry / qlib expression / runtime builder 层定义，不在 daily sync 中 materialize

### readiness 检查（特征审计）

sync 的 `_readiness_check()` 自动检查：

| 检查项 | 标准 |
|--------|------|
| 6 核心字段 null 率 | < 5% |
| 活跃成分股数量 | >= 750 |

不需要额外跑独立的 audit 脚本。

## 特征注册中心

特征集合定义由 `qsys/feature/registry.py` + `qsys/feature/library.py` 维护。

```python
from qsys.feature.registry import list_feature_groups
from qsys.feature.library import FeatureLibrary
```

- `research_ui` 通过 `/api/feature-registry` 浏览
- 训练和推理通过 feature registry 拉取特征列表

## 研究工具（非日常管道）

以下脚本用于特征研究和实验，不参与每日同步：

- `scripts/run_feature_build.py` — 基于 raw feather 做特征工程研究，输出 CSV
- `scripts/run_feature_readiness_audit.py` — 一次性特征覆盖率审计
- `scripts/run_feature_ablation.py` — 特征消融实验

## 通过标准

- 核心行情特征接近 0 缺失（通过 readiness check 验证）
- 活跃成分股 >= 750
- 特征注册中心有明确定义

## 运维要求

- 日常数据同步后，readiness 检查自动验证特征可用性
- 若特征集合有结构性变化，需更新 feature registry 后再进入训练
- 模型产物应记录使用了哪些特征组
