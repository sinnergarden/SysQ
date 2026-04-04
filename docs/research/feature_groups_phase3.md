# Phase 3 Feature Groups

## 总体原则
Phase 3 只接“披露后可见、当前数据源足以支撑、可解释”的基本面快变量；若字段不足，不假装完成，而是标记为 blocked。

## Group G: 估值与规模
当前最小可落地：
- `log_mktcap` <- `total_mv`
- `float_mktcap` <- `circ_mv`
- `pe_ttm` <- 当前先用 `pe` 代理
- `pb` <- 当前实现名为 `pb_raw`
- `ps_ttm` <- 若 raw 存在 `ps`，则映射为 `ps_ttm`；否则 blocked

## Group H: 财务质量与变化率
当前最小可落地：
- `roe`（直接字段或现有 PIT 派生）
- `roa` = `net_income / total_assets`
- `gross_margin` <- `grossprofit_margin`
- `net_margin` = `net_income / revenue`
- `operating_cf_to_profit` = `op_cashflow / net_income`
- `debt_to_asset` <- `debt_to_assets`
- `revenue_yoy`（当前先用日频 PIT 展开后对 252 个交易日做同比近似）
- `profit_yoy`（同上）

## 当前 blocked
- `inventory_yoy`
  - 当前未确认 raw 中有稳定 `inventory` 字段
  - 最小替代：先用 `revenue_yoy / profit_yoy` 代表经营变化速度
- `ar_yoy`
  - 当前未确认 raw 中有稳定 `accounts_receiv` 字段
  - 最小替代：先不纳入，待应收字段稳定后补
- `ps_ttm`
  - 代码已兼容 `ps -> ps_ttm`，但需继续确认 raw 历史覆盖

## 风险说明
- `revenue_yoy / profit_yoy` 目前是研究态近似实现：基于 PIT 展开的日频序列，再按约 252 个交易日同比。
- 这对最小研究闭环足够，但后续若要做更严格财报因子研究，应改成基于财报期的严格同比口径，而不是日频近似同比。
