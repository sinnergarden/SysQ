# Feature Development Guide

本文档面向 **feature framework 维护者**（不是研究人员）。
研究人员只用 FeatureSet YAML。

## Raw vs Derived

### Raw Feature
直接来自数据源或 qlib 表，不经过业务计算。

```python
"close"        # qlib daily OHLCV
"industry"     # classification
"roe"          # fina_indicator
"margin_balance"  # margin_detail
"holder_num"   # Tushare parquet
```

### Derived Feature
由 raw 或其他 derived feature 计算得到。

```python
"ret_60d"               # derived from close
"margin_crowding_score" # derived from margin_balance_to_float_mv + margin_balance_chg_60d
```

## PitType 说明

| PitType | 适用场景 | 说明 |
|---------|---------|------|
| `daily_observed` | OHLCV, amount, volume, turnover_rate | 日频直接观测值，每个 trading day 有一条记录，无需 PIT 处理 |
| `point_in_time` | PE, PB, ROE, margin_balance, holder_num | 财报/两融数据，必须用 ann_date 做 merge_asof |
| `rolling_past` | ret_60d, trend_consistency_120d | 时间窗口 rolling，只用历史数据 |
| `cross_sectional` | rps_60d, market_breadth | 横截面排序/标准化，按 trade_date group |
| `static` | industry | 不随时间改变 |

## PIT Rules（强制）

### 规则 1：Rolling window 只能用过去窗口
```python
# ✅
ret_60d = close.groupby("ts_code").pct_change(60)
# ❌ 使用未来数据
```

### 规则 2：Cross-sectional 按 trade_date 分组
```python
# ✅
zscore = out.groupby("trade_date")["ret_60d"].transform(cs_zscore)
```

### 规则 3：Industry aggregation 两步法
1. `groupby(["trade_date", "industry"])` collapse 为日面板
2. 在日面板上 `groupby("industry").rolling(window)`

### 规则 4：财报 feature 必须用 ann_date
```python
# ✅ merge_asof on ann_date, direction='backward'
# ❌ 用 end_date（数据过早可见）
```

### 规则 5：季度同比在 report-level 算
```python
# ✅ report.groupby("inst")["revenue"].pct_change(4)
# ❌ 在 daily PIT 展开后算 pct_change
```

### 规则 6：Proxy 命名
如果需要 proxy 替代某个经济含义，名中必须带 `_proxy`。

## 注册流程（框架维护者用）

1. 在 `qsys/feature/registry_v2.py` 添加 FeatureSpec
2. 在 `populate_feature_specs.py` 添加规格数据
3. 声明 kind / dependencies / pit_type / cache_scope / status
4. 在 FEATURE_GROUPS（registry.py）添加 group entry（旧路径兼容）
5. 在 `config.py` 添加默认 flag 值
6. 在 `builder.py` 添加 flag dispatch（旧路径兼容）
7. 跑 registry consistency test

## 测试命令

```bash
python -m unittest tests/features/test_feature_registry_consistency.py
python -m unittest tests/features/test_feature_builder_isolation.py
python -m unittest tests/features/test_industry_aggregation_contract.py
python -m unittest tests/features/test_financial_statement_feature_semantics.py
python -m unittest discover tests/features/
```
