# Feature Development Guide

## Raw vs Derived

### Raw Feature
直接来自数据源或 qlib 表，不经过业务计算。

```python
# 示例
"close"        # qlib daily OHLCV
"industry"     # qlib daily SW industry classification
"roe"          # qlib fina_indicator
"pe"           # qlib fina_indicator
"margin_balance"  # qlib margin_detail
"holder_num"   # Tushare parquet (external)
```

Raw feature 的 `kind="raw"`，`compute_fn=None`，`dependencies=()`。

### Derived Feature
由 raw feature 或其他 derived feature 计算得到。

```python
# 示例
"ret_60d"                      # derived from close
"rps_industry_60d"             # derived from ret_60d + industry
"margin_crowding_score"        # derived from margin_balance_to_float_mv + margin_balance_chg_60d
```

Derived feature 的 `kind="derived"`，`compute_fn="build_xxx_features"`，`dependencies=("feature_a", "feature_b")`。

## PIT Rules（强制）

### 规则 1：Rolling window 只能使用过去窗口

```python
# ✅ Correct: groupby(ts_code).rolling(window)
ret_60d = close.groupby("ts_code").pct_change(60)

# ❌ Wrong: incorporating future data
ret_60d_wrong = close.pct_change(-60).shift(-60)
```

### 规则 2：Cross-sectional 必须按 trade_date 分组

```python
# ✅ Correct: groupby(trade_date) → zscore per date
zscore = out.groupby("trade_date")["ret_60d"].transform(cs_zscore)

# ❌ Wrong: no date grouping
zscore_wrong = cs_zscore(out["ret_60d"])
```

### 规则 3：Industry aggregation 两步法

**第一步**：聚合为 `(trade_date, industry)` 面板

```python
ind_panel = out.groupby(["trade_date", "industry"]).agg(
    ind_ret=("_daily_ret", "mean"),
    ind_breadth=("_daily_ret", lambda s: (s > 0).mean()),
).reset_index()
```

**第二步**：在 industry 面板上做时间 rolling

```python
# ✅ Correct: groupby(industry).rolling on daily panel
ind_panel["industry_ret_20d"] = ind_panel.groupby("industry")["ind_ret"].transform(
    lambda s: s.rolling(20, min_periods=5).mean()
)
```

**历史 Bug**: `groupby(industry).rolling` 不先 collapse 会导致跨日期 contamination（不同 trade_date 的相同 industry 在同一 window 内混合）。

### 规则 4：财报 feature 必须用 ann_date

```python
# ✅ Correct: merge_asof on ann_date, direction='backward'
right["_dt"] = right["ann_date"]
merged = pd.merge_asof(left, right, on="_dt", by="inst", direction="backward")

# ❌ Wrong: using end_date instead of ann_date
right["_dt"] = right["end_date"]  # Lookahead: data visible too early!
```

### 规则 5：季度同比必须在 report-level 计算

```python
# ✅ Correct: on report-level panel before daily merge
report["revenue_yoy"] = report.groupby("inst")["revenue"].pct_change(4)

# ❌ Wrong: on daily PIT-expanded data
daily["revenue_yoy"] = daily.groupby("ts_code")["revenue"].pct_change(4)  # ~252 days = 1 year
```

### 规则 6：Proxy 命名

如果某个 feature 使用了经济含义的 proxy（如用 free_cashflow 代替 capex），feature name 必须包含 `_proxy`：

```python
# ✅ Correct
"earnings_yield_proxy"      # proxy for earnings yield
"peg_proxy"                  # proxy for PEG ratio
"capex_proxy"                # (hypothetical) proxy for capex

# ❌ Wrong: misleading name
"capex_to_assets"           # actually uses free_cashflow, not capex
```

## 注册流程

1. 在 `registry_v2.py` 中添加 `FeatureSpec`
2. 声明 `kind`（raw/derived）
3. 声明 `dependencies`（raw→derived 链）
4. 声明 `pit_type`
5. 添加计算函数到适当的 `qsys/feature/groups/` 文件
6. 在 `configs/features/` 添加 YAML feature_groups 引用
7. 在 `FEATURE_GROUPS`（registry.py）注册
8. 在 `builder.py` 中添加 flag dispatch
9. 在 `config.py` 添加默认 flag 值
10. 在 `adapter.py` 添加 `_semantic_feature_flags` 映射
11. 添加 registry consistency test

## 本地测试

```bash
# Registry consistency
python -m unittest tests/features/test_feature_registry_consistency.py

# Builder isolation
python -m unittest tests/features/test_feature_builder_isolation.py

# Industry aggregation contract
python -m unittest tests/features/test_industry_aggregation_contract.py

# Financial statement semantics
python -m unittest tests/features/test_financial_statement_feature_semantics.py

# Full feature test suite
python -m unittest discover tests/features/
```
