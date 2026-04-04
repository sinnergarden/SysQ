# Phase 2 Feature Groups

## 概览
Phase 2 关注“市场状态 + 行业上下文”，默认先做 **行业版**，不等待概念板块历史数据完全稳定。

## Group E: 市场 regime / breadth
最小实现目标：
- `market_breadth`
- `limit_up_breadth`
- `index_volatility_5`
- `index_volatility_10`
- `index_volatility_20`
- `small_vs_large_strength`
- `growth_vs_value_proxy`
- `market_trend_strength`

当前说明：
- `market_breadth` / `limit_up_breadth` 可直接从当日横截面得到
- `small_vs_large_strength` 先用 `circ_mv` 分层代理
- `growth_vs_value_proxy` 先用 `pb` 分层代理
- 指数相关特征需要稳定的指数收盘价列；若研究样本暂未接指数列，可先记为 context slot

## Group F: 行业 / 板块相对特征
最小实现目标：
- `industry_ret_1d`
- `industry_ret_3d`
- `industry_ret_5d`
- `stock_minus_industry_ret`
- `industry_breadth`
- `concept_or_sector_heat_proxy`（当前先用行业 proxy，不阻塞）

当前说明：
- 行业字段可直接从 `meta.db.stock_basic.industry` 复用
- 概念板块数据当前未作为稳定历史源纳入主线，因此先不强行实现概念特征

## 主信号 vs 状态 / 过滤 feature
建议区分：

### 更适合主信号的 feature
- `stock_minus_industry_ret`
- `stock_minus_industry_ret_3d`
- `stock_minus_industry_ret_5d`
- `industry_ret_1d / 3d / 5d`（视实验结果）

### 更适合作为状态 / 过滤 / context 的 feature
- `market_breadth`
- `limit_up_breadth`
- `index_volatility_5 / 10 / 20`
- `small_vs_large_strength`
- `growth_vs_value_proxy`
- `market_trend_strength`
- `industry_breadth`

原因：
- 这些变量更像“今天市场在什么环境下”，而不是直接决定个股横截面 alpha 的主排序项
- 更适合作为 filter、regime tag、解释层、或后续 graceful degradation 的 context 输入

## 当前 blocked / 降级说明
- `concept_or_sector_heat_proxy`：当前缺稳定概念板块历史源，先用行业版代理，不阻塞 Phase 2 最小实现。
- 指数波动 / 市场趋势：若样本数据里未显式补指数列，可先保留接口并在研究样本中后续补齐。