# SysQ 特征框架全面盘点与重构计划

> 本文档基于对 SysQ 特征系统的深度代码分析编写，覆盖寄存器（registry）、构建器（builder）、解析器（resolver）、YAML 配置、适配器（adapter）、特征组实现代码和仓库（library）等全部环节。

---

## 一、现状梳理

### 1.1 当前 registry.py 如何定义 feature group

`qsys/feature/registry.py` 通过全局字典 `FEATURE_GROUPS` 定义特征组。每个条目包含：

- **`enabled_by`**: 对应的 feature flag 名称，与 `config.py` 中的 `RESEARCH_FEATURE_FLAGS` 键值对应
- **`features`**: 该组包含的 derived feature 名称列表

**当前 12 个特征组统计：**

| 组名 | 特征数 | enabled_by flag | 实现文件路径 |
|------|--------|-----------------|-------------|
| microstructure | 7 | enable_microstructure_features | groups/microstructure.py |
| liquidity | 7 | enable_liquidity_features | groups/liquidity.py |
| tradability | 7 | enable_tradability_features | groups/tradability.py |
| relative_strength | 46 | enable_relative_strength_features | groups/relative_strength.py |
| regime | 8 | enable_regime_features | groups/regime.py |
| industry_context | 7 | enable_industry_context_features | groups/industry_context.py |
| fundamental_context | 40 | enable_fundamental_context_features | groups/fundamental_context.py |
| v3a_margin | 9 | enable_v3a_margin_features | groups/value_growth_v3a.py |
| v3a_shareholder | 10 | enable_v3a_shareholder_features | groups/value_growth_v3a.py |
| v3b_price_volume | 14 | enable_v3b_price_volume_features | groups/value_growth_v3b_price_volume.py |
| v3b_interaction | 5 | enable_v3b_interaction_features | groups/value_growth_v3b_price_volume.py |
| industry_momentum | 11 | enable_industry_momentum_features | groups/industry_momentum_features.py |

**总量：** 171 个注册条目（含跨组重复），去重后 169 个唯一 derived feature 名称。

**注册和解析路径：**
- `FeatureListRegistry` 类从 `configs/features/*.yaml` 加载特征列表（读取 yaml 中的 `features` 字段）
- `get_feature_fields(name)` 方法通过 `FeatureLibrary` 类方法解析命名特征集（如 `semantic_all_features`）
- 解析器 `resolver.py` 的 `resolve_feature_list()` 将 `feature_groups` 名称展开为具体特征名列表

**重要发现：** `registry_v2.py` 定义了 `FeatureSpec` 数据类并提供 `register()/get_by_id()/resolve_dependencies()` 接口，但目前**仅用于单元测试**，未与 `FEATURE_GROUPS` 集成。171 个特征中没有一个通过 `registry_v2` 注册 —— `registry_v2` 处于"有框架无数据"的状态。

---

### 1.2 当前 configs/features/*.yaml 如何引用 feature

`configs/features/` 目录下包含 **14 个 YAML 文件**。

**两种引用模式：**

1. **显式 `features` 列表（全部 14 个 YAML 均使用此模式）**
   - `alpha_v1_clean_132.yaml`: 132 个条目，混合 qlib 表达式（`$close`, `Ref($close,5)/$close` 等）和 derived feature 名称
   - `momentum_price_volume_v1.yaml`: 6 个纯 qlib 表达式
   - 其余 12 个 `value_growth_*` YAML: 均使用纯 derived feature 名称（如 `ret_60d`, `margin_crowding_score`），不包含 qlib 表达式

2. **`feature_groups` 列表（当前无 YAML 使用此模式）**
   - resolver.py 支持该字段，也实现了展开和去重逻辑，但实际 YAML 文件均未使用
   - 所有 YAML 通过手工编排的长 `features` 列表来引用特征

**各 YAML 文件统计：**

| 文件名 | 特征数 | 含 qlib 表达式 | 说明 |
|--------|--------|--------------|------|
| alpha_v1_clean_132 | 132 | 是 | 大量 qlib 算子（Ref/Mean/Std/Slope/Rsquare/Rank 等） |
| momentum_price_volume_v1 | 6 | 是 | 纯 qlib 表达式 |
| value_growth_60d_full_price_volume | 81 | 否 | microstructure+liquidity+tradability+relative_strength+v3b_pv |
| value_growth_60d_structured_price_volume | 35 | 否 | 精选 60d 价格动量+趋势质量+成交量参与度 |
| value_growth_60d_v3a_full_plus_structured_pv | 98 | 否 | v3a_full + structured PV |
| value_growth_existing_price_volume | 26 | 否 | 注释写 23 但实际包含 26 个 |
| value_growth_multibagger_v1 | 26 | 否 | growth_quality + valuation_repair + market_confirmation |
| value_growth_multibagger_v2 | 64 | 否 | v1 + continuation_trend + volume_participation + fundamental_accel + path_classifier |
| value_growth_multibagger_v3a | 83 | 否 | v2 + margin(9) + shareholder(10) |
| value_growth_v2_margin | 73 | 否 | v2(64) + margin(9) |
| value_growth_v2_shareholder | 74 | 否 | v2(64) + shareholder(10) |
| value_growth_v3a_plus_industry_momentum | 95 | 否 | v3a(83) + $industry + industry_momentum(11) |
| value_growth_v3b_pv | 97 | 否 | v3a(83) + v3b_pv(14) |
| value_growth_v3b_pv_interact | 102 | 否 | v3a(83) + v3b_pv(14) + interactions(5) |

**关键发现：**
- 每个 YAML 中的 `features` 列表是**手写维护**的，未利用 `feature_groups` 自动展开机制
- YAML 内容与 `FEATURE_GROUPS` 定义之间存在**隐式冗余**：新增特征时需要手动同步到每个 YAML 文件
- `value_growth_existing_price_volume.yaml` 注释声称 23 特征，实际列表包含 26 个 —— 手工计数误差的例证
- 多个 YAML 的内容存在大量重叠（例如 5 个 YAML 包含 v2 的 64 个基础特征），管理成本高

---

### 1.3 当前 builder.py 如何根据 flags 构造 feature

`build_phase1_features(df, flags)` 是核心建构入口。

**默认 flags：** 来自 `config.py` 的 `RESEARCH_FEATURE_FLAGS`：

```python
RESEARCH_FEATURE_FLAGS = {
    "enable_microstructure_features": True,
    "enable_liquidity_features": True,
    "enable_tradability_features": True,
    "enable_relative_strength_features": True,
    "enable_regime_features": False,
    "enable_industry_context_features": False,
    "enable_fundamental_context_features": False,
    "enable_v3a_margin_features": False,
    "enable_v3a_shareholder_features": False,
    "enable_v3b_price_volume_features": False,
    "enable_v3b_interaction_features": False,
    "enable_industry_momentum_features": False,
}
```

前 4 个为 True，其余均为 False。

**构建流程（按 flag 检查顺序）：**

```python
# 1. 列名修复（合并 close_x/close_y 等重复列）
out = _repair_research_input_columns(df)

# 2. 按顺序条件调用各组 builder
flags["enable_microstructure_features"]     → build_microstructure_features()
flags["enable_liquidity_features"]          → build_liquidity_features()
flags["enable_tradability_features"]        → build_tradability_features()
flags["enable_regime_features"] OR          
  enable_relative_strength_features         → attach_index_context()  # 先插入指数数据
flags["enable_relative_strength_features"]  → build_relative_strength_features()
flags["enable_industry_context_features"]   → build_industry_context_features()
flags["enable_regime_features"]             → build_regime_features()
flags["enable_fundamental_context_features"]→ build_fundamental_context_features()
flags["enable_v3a_margin_features"]         → build_margin_features()
flags["enable_v3a_shareholder_features"]    → load_shareholder_data() + build_shareholder_features()
flags["enable_v3b_price_volume_features"]   → build_v3b_price_volume_features()
flags["enable_v3b_interaction_features"]    → build_v3a_v3b_interaction_features()
flags["enable_industry_momentum_features"]  → build_industry_momentum_features()

# 3. 对部分列做跨截面标准化（winsorize + zscore + rank）
standardize_cols = [15 个硬编码列名]
apply_cross_sectional_standardization(out, standardize_cols)
```

**关键发现：**
- **隐式依赖链：** `build_relative_strength_features()` 依赖 `attach_index_context()` 生成的 `index_close` 列以及 `build_industry_context_features()` 生成的 `industry_ret_*` 列。但 builder 仅在 flag 级别控制执行顺序，缺少显式依赖声明 —— 跳过 industry_context 但请求 relative_strength 时，`stock_minus_industry_ret_3d` 等列全是 NaN。
- **v3b_interaction 依赖 v3a 和 v3b：** `build_v3a_v3b_interaction_features()` 需要 `holder_concentration_score`、`margin_trend_confirm_score`（来自 v3a）和 `trend_consistency_120d`（来自 v3b），但无任何代码检查前置条件是否已计算。
- **path_classifier_scores 静默 NaN：** `fundamental_context.py` 中的 `continuation_candidate_score` 等 4 个打分依赖 relative_strength 组的 `trend_smoothness_60d`、`rps_120d` 等特征。如果 builder 单独启用 `enable_fundamental_context_features` 而不启用 `enable_relative_strength_features`，这些打分列全是 NaN 且不做任何告警。
- **standardize_cols 列表硬编码：** builder.py 第 88-107 行列出了 15 个需要标准化的列名，与 `FEATURE_GROUPS` 的定义完全解耦。新增特征时需要手动更新此列表。
- **QlibAdapter 独立路径：** `adapter.py` 中的 `_semantic_feature_flags()` 通过请求的特征名反向推导 flags，绕过 YAML 配置直接调用 builder，构成第二条完全独立的特征构造路径。

---

### 1.4 raw feature 的来源

**qlib 日线原始字段（daily 表）：**
- `close`, `open`, `high`, `low` — 基础价格数据
- `volume`, `amount` — 成交量、成交金额
- `vwap` — 成交量加权均价
- `factor` — 复权因子
- `high_limit`, `low_limit` — 涨停价、跌停价

**qlib 财务指标（fina_indicator 表）：**
- `roe` — 净资产收益率
- `grossprofit_margin` — 营业毛利率
- `debt_to_assets` — 资产负债率
- `current_ratio` — 流动比率

**qlib 市值类（market_value 表或 daily 衍生）：**
- `total_mv` — 总市值
- `circ_mv` — 自由流通市值

**qlib 估值类（valuation 表）：**
- `pe`, `pb`, `ps` — 市盈率、市净率、市销率

**qlib 资金流（money_flow 表）：**
- `net_inflow` — 净流入额
- `big_inflow` — 大单流入额

**qlib 利润表（income 表）：**
- `revenue` — 营业总收入
- `net_income` — 净利润

**qlib 资产负债表（balance 表）：**
- `total_assets` — 总资产
- `equity` — 股东权益
- `inventory` — 存货
- `ar` — 应收账款

**qlib 现金流量表（cashflow 表）：**
- `op_cashflow` — 经营活动现金流净额

**qlib 融资融券（margin_detail 表）：**
- `margin_balance` — 融资余额
- `margin_buy_amount` — 融资买入额
- `margin_repay_amount` — 融资偿还额
- `margin_total_balance` — 融资融券余额
- `lend_volume` — 融券余量
- `lend_sell_volume` — 融券卖出量
- `lend_repay_volume` — 融券偿还量

**外部 Parquet 数据（Tushare 来源）：**
- `holder_num` — 股东户数（路径: `data/canonical/holder_num.parquet`）
- `top10_holder_ratio` — 前十大股东持股比例（路径: `data/canonical/top10_holder_ratio.parquet`）

**元数据库：**
- `industry` — 行业归属（通过 `meta.db` 中的 `stock_basic` 表查询）

**指数数据（CSV 文件）：**
- `index_close`, `index_ma20` — 通过 `data/raw/index/<ts_code>.csv` 加载

---

### 1.5 derived feature 的实现位置

每个特征组及其生成函数的实现位置：

| 特征组 | 文件路径 | 构建函数 | 特征数 |
|--------|----------|----------|--------|
| microstructure | `groups/microstructure.py` | `build_microstructure_features()` | 7 |
| liquidity | `groups/liquidity.py` | `build_liquidity_features()` | 6 |
| tradability | `groups/tradability.py` | `build_tradability_features()` | 7 |
| relative_strength | `groups/relative_strength.py` | `build_relative_strength_features()` | 46（含子组） |
| regime | `groups/regime.py` | `build_regime_features()` | 8 |
| industry_context | `groups/industry_context.py` | `build_industry_context_features()` | 7 |
| fundamental_context | `groups/fundamental_context.py` | `build_fundamental_context_features()` | 40（含 v2 子组） |
| v3a_margin | `groups/value_growth_v3a.py` | `build_margin_features()` | 9 |
| v3a_shareholder | `groups/value_growth_v3a.py` | `load_shareholder_data()` + `build_shareholder_features()` | 10 |
| v3b_price_volume | `groups/value_growth_v3b_price_volume.py` | `build_v3b_price_volume_features()` | 14 |
| v3b_interaction | `groups/value_growth_v3b_price_volume.py` | `build_v3a_v3b_interaction_features()` | 5 |
| industry_momentum | `groups/industry_momentum_features.py` | `build_industry_momentum_features()` | 11 |
| index_context | `groups/index_context.py` | `attach_index_context()` | 1（仅 index_close 列） |

**补充路径：**
- `qsys/feature/library.py` — `FeatureLibrary` 类提供命名配置集方法（如 `get_semantic_all_features_config()`），但其逻辑独立于 builder.py 和 resolver.py
- `qsys/feature/calculator.py` — `FeatureCalculator` 提供纯 Python 实现的 qlib 算子（Ref/Mean/Std 等），设计用于推理环境，但功能有限
- `qsys/feature/transforms.py` — 跨截面标准化工具函数（winsorize、zscore、rank）
- `qsys/feature/groups/structured_alpha.py` — 已不存在于磁盘（实验性质，被评估为负面结果后移除）

---

### 1.6 feature flags 和 yaml feature list 是否可能不一致

**是 —— 存在多个导致不一致的路径，风险等级为 HIGH。**

**风险 1：Adapter 自动检测路径（`_semantic_feature_flags()`）**

`qsys/data/adapter.py` 中 `_semantic_feature_flags(derived_fields)` 通过请求的特征名反向推导 flags：

```python
@staticmethod
def _semantic_feature_flags(derived_fields):
    flags = {key: False for key in RESEARCH_FEATURE_FLAGS}
    groups = list_feature_groups()
    requested = set(derived_fields)
    for group in groups.values():
        if requested.intersection(group.get("features", [])):
            flags[group["enabled_by"]] = True
    # 特殊规则：硬编码的特征名
    if requested.intersection({"stock_minus_industry_ret_3d", "stock_minus_industry_ret_5d"}):
        flags["enable_industry_context_features"] = True
        flags["enable_relative_strength_features"] = True
    if requested.intersection({"inventory_yoy", "ar_yoy"}):
        flags["enable_fundamental_context_features"] = True
    if any(f.startswith("industry_") or f.startswith("stock_minus_industry_") for f in requested):
        flags["enable_industry_momentum_features"] = True
        flags["enable_industry_context_features"] = True
    return flags
```

问题分析：
- 仅检查 `FEATURE_GROUPS` 中显式列出的特征名，不检查 YAML 上下文
- `stock_minus_industry_ret_3d` 同时属于 `relative_strength` 和 `industry_context` 两组，但特殊规则代码将其分类为同时需要 industry_context 和 relative_strength，而 registry 中仅 relative_strength 组包含 `enable_relative_strength_features`
- `industry_*` 前缀匹配过于宽泛：`industry_ret_1d`（应启用 industry_context）和 `industry_ret_20d`（应启用 industry_momentum）都会被此规则同时捕获
- YAML 显式列表中的特征名触发此 auto-detect，导致 YAML 未声明的 flag 被启用

**风险 2：双代码路径无交验**

- **路径 A（YAML 驱动）：** 读取 YAML config → `FeatureListRegistry.load()` → 直接传给 qlib `DatasetD.dataset()`（使用原生 qlib 表达式计算特征）
- **路径 B（Adapter 驱动）：** `QlibAdapter.get_features()` → `_semantic_feature_flags()` → `build_phase1_features(flags=...)`（通过 flag 控制 builder）

两条路径在特征选择逻辑上完全独立，没有任何交叉验证。

**风险 3：resolver.py 不做 flag 检查**

`resolver.py` 的 `resolve_feature_list()` 仅展开 group 名称为具体特征名，不会检查：
- 展开后的特征是否会被 builder 实际计算
- 对应的 `enabled_by` flag 在调用侧是否已开启
- 展开的特征是否存在依赖缺失

**风险 4：FeatureLibrary 独立组合逻辑**

`FeatureLibrary.get_semantic_all_features_config()` 手动调用 `get_alpha158_margin_extended_config()` 然后遍历 `FEATURE_GROUPS` 累加特征名，与 resolver 逻辑重复且无校验：

```python
@classmethod
def get_semantic_all_features_config(cls):
    merged = list(cls.get_alpha158_margin_extended_config())
    for group in list_feature_groups().values():
        for field in group.get("features", []):
            if field not in merged:
                merged.append(field)
    return merged
```

**风险 5：YAML 引用单个特征而不启用其父组**

YAML 显式列表可以引用 `ret_60d` 而不引用 `relative_strength` 组。在 YAML 驱动路径下这可以工作，但在 adapter 路径下 `_semantic_feature_flags()` 检测到 `ret_60d` 后会自动启用 `enable_relative_strength_features`，导致 builder 计算了比请求更多的特征 —— 行为不一致。

**风险 6：NON_RESOLVABLE 组的特殊情况**

`resolver.py` 将 `v3a_margin` 和 `v3a_shareholder` 标记为 `NON_RESOLVABLE`，但此标记仅在 manifest 构建时生效，不影响 `FEATURE_GROUPS` 或 builder 的 flag 判断。

---

### 1.7 feature name 是否存在重复、同义、语义不清、依赖不明的问题

**问题 1：特征名跨组重复**

通过代码验证，以下两个特征名同时在两个组的 `features` 列表中出现：

| 特征名 | 所属组 |
|--------|--------|
| `stock_minus_industry_ret_3d` | **relative_strength**, **industry_context** |
| `stock_minus_industry_ret_5d` | **relative_strength**, **industry_context** |

这两个特征的实际计算代码仅在 `relative_strength.py` 中。`industry_context.py` 计算的是 `stock_minus_industry_ret`（1 日），而非 `_3d/_5d`。虽然通过 resolver 的稳定去重后结果一致，但在语义上存在歧义 —— 一个特征究竟"属于"哪个组？

**问题 2：语义重叠的特征簇**

| 特征簇 | 涉及组 | 问题描述 |
|--------|--------|----------|
| `rps_industry_60d`（relative_strength） vs `industry_ret_20d` / `industry_top_stock_momentum`（industry_momentum） | 行业板块强度的计算路径不同，但概念高度重叠 |
| `volume_up_down_ratio_60d`（relative_strength） vs `up_volume_down_volume_ratio_60d`（v3b_price_volume） | **命名风格不一致**：前者用 `volume_` 前缀，后者用 `up_volume_` 前缀，含义相似但计算方式不同（成交量 vs 成交额，均值比 vs 总和比） |
| `stock_minus_industry_ret`（industry_context，1日） vs `stock_minus_industry_ret_20d` / `_60d`（industry_momentum） | 同一前缀 `stock_minus_industry_ret` 不带窗口时为 1d，在不同组中含义不同 |
| `opened_from_limit_up`（tradability） vs `is_limit_up`（tradability） | 前者"曾涨停后开板"，后者"当前涨停"，语义有关联但特征名未体现差异 |
| `industry_ret_1d`/`_3d`/`_5d`（industry_context） vs `industry_ret_20d`/`_60d`/`_120d`（industry_momentum） | 两组按窗口长度而非业务角色划分，命名中无法区分属于哪个组 |

**问题 3："volume_up_down_ratio_60d" vs "up_volume_down_volume_ratio_60d" 详细对比**

| 属性 | volume_up_down_ratio_60d | up_volume_down_volume_ratio_60d |
|------|--------------------------|--------------------------------|
| 定义位置 | relative_strength.py 第 115 行 | value_growth_v3b_price_volume.py 第 146 行 |
| 所属组 | relative_strength（volume_participation_quality 子组） | v3b_price_volume（volume_quality_features 子组） |
| 分子 | 上涨日**平均成交量** / 下跌日**平均成交量** | 上涨日**成交额总和** / 下跌日**成交额总和** |
| 使用列 | `volume`（成交量） | `amount`（成交额） |
| 计算方式 | `(up_vol_sum/up_count) / (down_vol_sum/down_count)` | `up_amount_sum / down_amount_sum` |

两者名称相似但计算差异很大（成交量 vs 成交额，均值比 vs 总和比）。更严重的是，名称高度相似的这两个特征很容易在分析和回测中被混淆使用。

**问题 4：无显式依赖追踪**

关键合成打分特征的依赖链完全隐式：

```
margin_crowding_score
  → margin_balance_to_float_mv (需 margin_balance, circ_mv)
  → margin_balance_chg_60d (需 margin_balance)

holder_concentration_trend_confirm
  → holder_concentration_score (来自 v3a)
  → trend_consistency_120d (来自 v3b)

continuation_candidate_score
  → trend_smoothness_60d, rps_120d, rps_20d, price_percentile_252d
    → close (price_percentile_252d 依赖 close 的 252 日滚动)

overheat_risk_score
  → price_percentile_252d, rps_120d, volume_spike_20d
```

这些依赖链在代码中**没有任何地方被显式声明**。`registry_v2.py` 虽然设计了 `dependencies` 字段，但从未被填充。当构建顺序错误或缺少前置特征时，合成打分列静默输出 NaN 而不会报错。

**问题 5：命名不一致**

| 问题 | 示例 |
|------|------|
| 缩写不统一 | `operating_cf_to_profit` vs `ocf_margin`（同一数据源的两种命名） |
| 同一指标两种写法 | `gross_margin` vs `grossprofit_margin`；`debt_to_asset` vs `debt_to_assets` |
| 时间窗口位置不统一 | `volume_shock_3`（窗口在末尾） vs `ret_3d`（窗口在末尾) vs `pe_percentile_756d`（窗口末尾）—— 已基本一致，但有少量例外 |
| Raw 字段名与 derived 字段名冲突 | `top10_holder_ratio` 既是外部 parquet 的 raw 字段名，又是 v3a_shareholder group 的输出 feature（直通） |

**问题 6：数量不一致**

- registry 声明 171 个条目（含重复 2 个 = 169 唯一）
- YAML 文件中的显式列表合计远超 169（有大量 qlib 表达式不在 registry 中）
- `_semantic_feature_flags()` 自动检测基于 registry，与 YAML 内容不完全对齐

---

## 二、目标架构

### 2.1 分层设计

```
┌─────────────────────────────────────────────────────────┐
│  User-facing layer                                      │
│  FeatureSet YAML (configs/features/*.yaml)              │
│    • features list / extends + add_features (只加不减)   │
│    • 声明 = 承诺：缺失或 broken → fail fast              │
└───────────────────────┬─────────────────────────────────┘
                        │  (内部，自动)
                        v
┌─────────────────────────────────────────────────────────┐
│  Resolver / BuildPlan                                   │
│    • 读 YAML → 唯一特征列表                              │
│    • 查 FeatureSpec → 拓扑排序 → build_plan              │
│    • 校验：broken/missing → fail fast                    │
└──────┬──────────────────────────────┬───────────────────┘
       │                              │
       v                              v
┌──────────────┐          ┌──────────────────────┐
│ Transform    │          │ Cache                │
│ (compute)    │◄─────────│ • transform-level     │
│              │          │ • matrix-level        │
└──────┬───────┘          │ • per-feature (扩展)  │
       │                  └──────────────────────┘
       v
┌─────────────────────────────────────────────────────────┐
│  Manifest (audit only)                                  │
│    • final_features, required_transforms                │
│    • cache hits/misses, source_hash, builder_hash       │
│    • 若 final_columns ≠ resolved → 构建失败             │
└─────────────────────────────────────────────────────────┘
```

### 2.2 核心原则

1. **用户感知层只能有一层：FeatureSet YAML。** FeatureSpec、TransformSpec、Resolver、Cache、Manifest 都是内部实现细节。
2. **YAML 声明 = 承诺。** 声明的 feature 必须全部产出，缺者 fail fast。不允许 silent skip。
3. **只做加法。** 不支持 `exclude_features` / `exclude_groups`，不支持运行时减法。需要 ablation 就新建显式 YAML。
4. **Manifest 只用于审计和复现，不用于容错。**
5. **旧 YAML 输出 feature column 不变。** 除非显式标注为 bugfix/migration。
6. **FeatureSet YAML 只支持两种模式：** old-style features list（兼容）；new-style `extends + add_features`（只追加）。

### 2.3 各层设计要点

**FeatureSet YAML（用户层）：**
```yaml
# 旧式（兼容）
feature_list_id: value_growth_multibagger_v3a
features:
  - ret_60d
  - margin_crowding_score

# 新式（目标态）
feature_list_id: vg_v3a_plus_momentum
extends: value_growth_multibagger_v3a
add_features:
  - industry_breadth_20d
```

**FeatureSpec（内部）：** 每个 feature 的完整元数据，含 feature_id（永久稳定）、name（列名）、kind（raw/derived）、dependencies、compute_fn、pit_type、cache_scope、status 等。

**TransformSpec（内部）：** 描述一个计算单元（如 "build_relative_strength_features"），含 inputs、outputs、compute_fn、pit_contract、cache_scope、dependencies（transform 级依赖）。

**Resolver（内部）：** YAML → FeatureSpec → BuildPlan。自动检测 broken/deprecated/missing feature。自动决定需要跑哪些 transform 及其顺序。

**Cache（内部）：**
- 主路径：transform-level cache（scope=panel 的 transform 结果可缓存）
- 可选：matrix cache（全量矩阵缓存）
- Per-feature cache 作为未来扩展，当前不做主路径

**Manifest（内部）：** 每次构建产出一份 manifest，记录 final_features、required_transforms、cache hits/misses、source hash、builder hash。若 final_columns ≠ resolved_features，构建失败。

### 2.4 当前 vs 目标态

| 维度 | 当前 | 目标态（Phase 4） |
|------|------|------------------|
| 用户入口 | flag + YAML + FeatureLibrary 三路径 | FeatureSet YAML only |
| Feature 元数据 | FEATURE_GROUPS (name list) | FeatureSpec (full metadata) |
| 构建调度 | 硬编码 flag → group → fn 链 | BuildPlan (拓扑排序) |
| 校验 | 无 | YAML↔registry 双向校验，fail fast |
| 缓存 | QlibAdapter 粗粒度缓存 | Transform-level + matrix |
| YAML 模式 | 纯 features list | extends + add_features（只加不减） |
| 减法 | 隐式（flag=false） | 不允许；需要 ablation 就建新 YAML |

---

## 三、Phase 1 交付物（本 PR）

### 3.1 文件交付物

| 交付物 | 文件路径 | 说明 |
|--------|----------|------|
| 全量特征清单 CSV | `research_notes/feature_inventory.csv` | 全量特征清单，含名称、组、依赖、数据源、PIT 类型等 |
| 本分析文档 | `research_notes/feature_framework_inventory_and_refactor_plan.md` | 本文档 |
| registry_v2 全部特征注册（增量） | `qsys/feature/registry_v2.py` | 填充全部 169+ 特征的 `FeatureSpec` |
| 一致性测试增强 | `tests/features/test_feature_registry_consistency.py` | 新增 registry/YAML/builder 三方对齐测试 |
| Builder 隔离测试 | `tests/features/test_feature_builder_isolation.py` | 验证各 builder 在无前置特征时不报错 |
| 行业聚合合约测试 | `tests/features/test_industry_aggregation_contract.py` | 防止跨股票污染回归 |

### 3.2 代码交付物详细说明

**A. 特征清单 CSV（feature_inventory.csv）**
- 范围：全部 169 个唯一 derived feature + 使用的 raw feature
- 字段：`feature_id`, `name`, `group`, `kind`, `source`, `dependencies`, `compute_fn`, `pit_type`, `cache_scope`, `status`, `description`, `enabled_by_flag`
- 来源：从 registry.py、各组 builder、resolver.py 中自动提取并交叉验证

**B. registry_v2 填充**
- 为每个 `FEATURE_GROUPS` 中的 derived feature 创建 `FeatureSpec`
- 填充 `dependencies`（从 builder 代码分析得出）
- 填充 `compute_fn`（指向具体组构建函数）
- 填充 `pit_type`（`rolling_past` / `cross_sectional` / `point_in_time`）
- 填充 `cache_scope`（`panel` / `per_date` / `none`）
- 填充 `status`（`active` / `experimental` / `deprecated` / `broken`）

**C. 一致性测试增强**
- 测试 1：`FEATURE_GROUPS` 中所有 `enabled_by` flag 在 `config.py` 中存在
- 测试 2：所有 YAML 中引用的 derived feature 名在 registry 中存在
- 测试 3：builder.py 中每个 flag 分支调用正确的组构建函数
- 测试 4：每组的构建函数输出列包含 `FEATURE_GROUPS` 声明的全部特征
- 测试 5：全量特征名无重复（已有测试增强）
- 测试 6：无 status=broken 特征进入 FEATURE_GROUPS
- 测试 7：registry_v2 中的 FeatureSpec 与 FEATURE_GROUPS 条目一一对应

**D. 设计文档**
- `docs/feature_registry_design.md` — registry_v2 设计和迁移指南
- `docs/feature_cache_design.md` — 缓存策略和集成方案
- `docs/feature_development.md` — 开发新特征的操作规范
- `docs/agent_feature_development_checklist.md` — AI Agent 开发特征时的工作清单

---

## 四、Phase 2-4 规划

### Phase 2：Resolver 一致性增强和 YAML 重构

**目标：** 建立 YAML ↔ registry ↔ builder 的三方校验体系，将 YAML 配置改为由 `feature_groups` 自动展开。

**步骤：**
1. 实现 manifest 自动生成：每个 YAML 配置经过 resolver 输出 manifest JSON（`artifacts/feature_manifests/{feature_list_id}.json`）
2. 新增 registry consistency 测试套件（YAML ↔ registry ↔ builder 三向校验）
3. 分析每个 YAML 的 `features` 列表，按特征组归类
4. 将冗余 YAML 的显式列表替换为 `feature_groups` 引用 + 极少数特例
5. 废弃完全被覆盖的冗余 YAML 文件（如 `value_growth_v2_margin.yaml` 和 `value_growth_multibagger_v3a_features.yaml` 内容高度重叠）
6. 为跨组重复的特征建立 `alias` 概念：`feature_id` 唯一，但可以有多个 `name`（各组的输出列名）
7. Legacy flags 继续可用，但标记为 deprecated

### Phase 3：Cache 集成

**目标：** 基于 registry_v2 的 cache_scope 信息，实现特征计算的增量缓存。

**步骤：**
1. 定义清晰缓存策略：cache key 包含 `feature_id`, `compute_fn` 版本, `dependencies` hash, 数据源 manifest hash, 日期范围, 股票池
2. 先支持特征矩阵缓存（`data/feature_cache/matrices/`）
3. 再支持逐特征缓存（`data/feature_cache/features/`）
4. 与 builder 集成：每次构建前检查 cache hit，跳过 cache hit 的特征
5. 缓存失效策略：依赖链中任一上游变更 → 下游所有特征缓存失效

### Phase 4：Builder 重构和双路径统一

**目标：** 全新的 builder 以 `feature_list_id` 为入口，自动解析依赖并应用增量缓存。

**步骤：**
1. 实现新入口 `build_features_from_feature_list(df, feature_list_id, ...)`：
   - 读取 YAML → resolver 展开 → registry_v2 查找每个特征的 FeatureSpec
   - 构建依赖 DAG → 拓扑排序 → 生成构建计划
   - 自动判断 raw vs derived → 计算缺失的 derived feature
   - 可从 cache 读取已有结果
2. Legacy `build_phase1_features(flags=...)` 保留为兼容接口，标记为 legacy
3. 统一 QlibAdapter 的 flag 推导：从 registry_v2 的组归属解析替代硬编码字符串匹配
4. 废弃 `config.py` 中的 `RESEARCH_FEATURE_FLAGS`，改为从 registry_v2 动态生成
5. 废弃 `FeatureLibrary` 中的重复组合方法（`get_semantic_all_features_config` 等），统一走 YAML + resolver

---

## 五、当前系统风险总结

| 风险 | 严重程度 | 说明 | 缓解措施 |
|------|----------|------|----------|
| Registry/YAML/flag 三方不一致 | **HIGH** | FEATURE_GROUPS 定义的特征集与 YAML 手写列表、builder 实际输出可能不同步；新增特征时需在 3 个位置手动更新 | Phase 1: registry_v2 填充 + 一致性测试；Phase 2: YAML 改用 feature_groups 自动展开，消除手写列表 |
| Feature name 跨组重复 | **MEDIUM** | `stock_minus_industry_ret_3d` 和 `_5d` 同时存在于 relative_strength 和 industry_context 两组，registry 声明重复，代码实现仅在 relative_strength 中 | Phase 1: 特征清单 CSV 中标注；Phase 2: feature_id + alias 系统，统一命名规范 |
| 名称相似但计算不同 | **MEDIUM** | `volume_up_down_ratio_60d`（relative_strength, 成交量）vs `up_volume_down_volume_ratio_60d`（v3b_pv, 成交额）—— 命名高度相似但含义和列不同，极易混淆 | Phase 1: 特征清单中标注差异；Phase 2: 重命名不清晰的特征，建立命名规范评审 |
| 无显式依赖声明 | **HIGH** | 171 个特征中无一个声明依赖关系；合成打分（margin_crowding_score、continuation_candidate_score 等）的依赖链完全隐式，构建顺序错误时静默 NaN | Phase 1: registry_v2 填充 dependencies 字段；Phase 3: 基于 DAG 的构建引擎强制执行依赖检查 |
| 重复计算 | **MEDIUM** | 67 个 rolling window × 同一组特征 = 数十次重复计算；cache.py 已实现但未被 builder 集成 | Phase 3: cache 层集成到构建引擎；利用 registry_v2 的 cache_scope 实现智能缓存 |
| 行业聚合跨日期污染 | **已修复** | v3b 特征计算中 rolling() 未包裹 groupby("ts_code") 导致跨股票泄漏 | 已有 bugfix；Phase 1 新增回归测试确保不反复 |
| Adapter 自动检测路径不可靠 | **HIGH** | `_semantic_feature_flags()` 使用硬编码规则和前缀匹配（`industry_*`），可能误判或遗漏特征组 | Phase 4: 改用 registry_v2 的组归属解析替代硬编码规则 |
| FeatureLibrary 独立路径 | **MEDIUM** | `get_semantic_all_features_config()` 手动组合特征，与 resolver.py、builder.py 无协调 | Phase 4: 废弃重复方法，统一走 YAML + resolver |
| YAML 注释与实际特征数不符 | **LOW** | `value_growth_existing_price_volume.yaml` 注释声称 23 个特征，实际列表 26 个 | 修复 YAML 注释；添加自动化数量校验 |
| registry_v2 有框架无数据 | **MEDIUM** | `FeatureSpec` 和注册函数已实现但零特征注册，单元测试仅测试框架自身 | Phase 1: 填充全部 169 个特征的 FeatureSpec |
| path_classifier_scores 依赖隐式 | **HIGH** | `continuation_candidate_score` 等 4 个打分紧密依赖 relative_strength 特征，但独立启用 fundamental_context 时静默输出 NaN 无告警 | Phase 1: registry_v2 声明依赖；Phase 3: DAG 驱动后确保前置计算 |
| 交叉截面标准化列名硬编码 | **LOW** | builder.py 硬编码 15 个标准化列名，与 FEATURE_GROUPS 的定义脱节 | Phase 2: 改为从 registry_v2 的 pit_type="cross_sectional" 标记自动推导 |
| 外部 Parquet 数据 PIT 风险 | **MEDIUM** | shareholder 数据使用 merge_asof 按 ann_date 回填，若原始 parquet 未更新会导致 stale data | 已有 stale_days 字段监控；需增加数据新鲜度告警和自动化测试 |
| 指数数据从 CSV 加载 | **MEDIUM** | index_context.py 从 `data/raw/index/` 下的 CSV 文件加载，与 qlib bin 数据不同步；多个维护脚本可能导致格式不一致 | 评估迁移到 qlib 统一数据管理或新增数据完整性校验 |

---

## 六、附录

### 附录 A：代码路径和数据流图

```
                         特征定义层
┌──────────────────────────────────────────────────────────────────────┐
│  FEATURE_GROUPS (registry.py)                                        │
│    ├── enabled_by → RESEARCH_FEATURE_FLAGS (config.py)               │
│    ├── features → 特征名称列表（171 条目，169 唯一）                 │
│    └── → FeatureLibrary 命名配置集（library.py，独立路径）            │
│                                                                      │
│  registry_v2 (registry_v2.py，待填充)                                 │
│    └── FeatureSpec(feature_id, name, group, kind,                    │
│                    dependencies, compute_fn, pit_type, ...)           │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                          配置/解析层
┌──────────────────────────────────────────────────────────────────────┐
│  configs/features/*.yaml（14 个文件）                                 │
│    ├── features: [显式特征列表]（手工维护）                          │
│    └── feature_groups: [组名列表]（当前未使用）                      │
│                                                                      │
│  FeatureListRegistry.load(id) → YAML["features"]                     │
│  resolve_feature_list(config) → 展开 feature_groups + 稳定去重       │
│  build_feature_manifest(features, expansions) → 特征清单             │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                           构建层
┌──────────────────────────────────────────────────────────────────────┐
│  build_phase1_features(df, flags)                                    │
│    ├── _repair_research_input_columns()                              │
│    ├── 按 flag 依次检查并调用各组 builder（隐式依赖顺序）             │
│    │    每组 builder: groups/*.py 中的 build_*_features() 函数        │
│    └── 跨截面标准化（transforms.py，15 个列名硬编码）                 │
└──────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                          适配器层
┌──────────────────────────────────────────────────────────────────────┐
│  QlibAdapter (adapter.py)                                            │
│    ├── _semantic_feature_flags(derived_fields) → auto-detect flags   │
│    │   从请求特征名反向推导 flags（基于硬编码的字符串匹配）          │
│    ├── _build_semantic_features(base_df, derived_fields)             │
│    │   → build_phase1_features(flags=auto_detected)                 │
│    └── get_features(instruments, fields)                             │
│        → native_fields(qlib bin) + derived_fields(builder)          │
└──────────────────────────────────────────────────────────────────────┘
```

### 附录 B：关键文件索引

| 文件 | 用途 | 行数（约） |
|------|------|-----------|
| `qsys/feature/registry.py` | FEATURE_GROUPS 定义、FeatureListRegistry 加载 YAML | 322 |
| `qsys/feature/registry_v2.py` | FeatureSpec 数据类、注册/查询/依赖解析框架 | 246 |
| `qsys/feature/builder.py` | build_phase1_features() 核心构建入口 | 111 |
| `qsys/feature/resolver.py` | resolve_feature_list() 组名展开、build_feature_manifest() | 561 |
| `qsys/feature/config.py` | RESEARCH_FEATURE_FLAGS 默认值 | 17 |
| `qsys/feature/library.py` | FeatureLibrary 命名配置集方法（独立路径） | 465 |
| `qsys/feature/transforms.py` | 跨截面标准化工具函数（winsorize/zscore/rank） | 46 |
| `qsys/feature/cache.py` | Parquet 持久化缓存（未被 builder 集成） | 69 |
| `qsys/feature/calculator.py` | Pure Python qlib 算子实现（用于推理环境） | 154 |
| `qsys/data/adapter.py` | QlibAdapter 特征自动检测和构建链 | ~500 |
| `tests/features/test_feature_registry_consistency.py` | 现有 registry 一致性测试 | 376 |

### 附录 C：特征组功能模块总览（按构建顺序）

每个特征组的输入依赖、计算逻辑和输出特征概览：

| 组名 | 输入依赖 | 核心逻辑 | 输出特征数 |
|------|----------|----------|-----------|
| microstructure | OHLCV | 日内价格形态、震荡幅度、位置计算 | 7 |
| liquidity | volume/amount/float_shares | 换手率、成交额对数、成交量冲击、Amihud 非流动性 | 6 |
| tradability | close/high_limit/low_limit | 涨跌停检测、距涨跌停距离、可交易性评分 | 7 |
| relative_strength | close/volume/amount/index_close | 多周期收益、排名、趋势平滑度、RPS、成交量质量 | 46 |
| regime | 全市场数据 | 市场宽度、波动率、大小盘风格、价值成长风格 | 8 |
| industry_context | close/industry（SQLite） | 行业平均收益、行业宽度、个股-行业超额 | 7 |
| fundamental_context | total_mv/circ_mv/pe/pb/roe 等 | 估值、盈利能力、YoY 变化、加速、路径分类 | 40 |
| v3a_margin | margin_balance/buy/repay/circ_mv | 融资余额比率、变化、买入强度、拥挤度评分 | 9 |
| v3a_shareholder | holder_num/top10_holder_ratio（Parquet） | 股东户数变化、持股集中度、挤压评分 | 10 |
| v3b_price_volume | close/amount | 趋势一致性、低波动上升趋势、成交量质量 | 14 |
| v3b_interaction | v3a + v3b 特征 | 筹码集中度 × 趋势确认、融资 × 回撤修复 | 5 |
| industry_momentum | close/amount/industry | 行业收益、行业宽度、新高比例、个股-行业相关性 | 11 |

---

*本文档基于 SysQ 代码库的深度分析编写，覆盖了特征系统的全部环节。*
