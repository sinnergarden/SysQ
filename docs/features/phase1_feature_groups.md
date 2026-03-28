# Phase 1 Feature Groups

本文件记录第一批短周期日频特征（Phase 1）的最小可用实现。

## Group A: 微观结构 / K线结构
- `close_to_open_gap_1d`
- `open_to_close_ret`
- `close_pos_in_range`
- `open_pos_in_range`
- `upper_shadow_ratio`
- `lower_shadow_ratio`
- `intraday_reversal_strength`

数据来源：日线 OHLC

对齐方式：按 `ts_code + trade_date`，涉及前收时使用 `groupby(ts_code).shift(1)`。

缺失处理：
- 前收缺失时结果为空
- 当日高低价区间为 0 时，区间型特征置空

标准化：
- 连续值后续统一进入 winsorize + 当日横截面 zscore/rank

## Group B: 流动性 / 容量 / 冲击
- `turnover_rate`
- `amount_log`
- `amount_zscore_20`
- `volume_shock_3`
- `volume_shock_5`
- `turnover_acceleration`
- `illiquidity`

数据来源：日线成交额、成交量、换手率

## Group C: 涨跌停 / 可交易性
- `is_limit_up`
- `is_limit_down`
- `distance_to_limit_up`
- `distance_to_limit_down`
- `limit_up_count_5d`
- `tradability_score`
- `opened_from_limit_up`

数据来源：收盘价、开盘价、涨跌停价、停牌状态

## Group D: 横截面相对强弱
- `ret_rank_1d / 3d / 5d`（当前以 `ret_1d_rank / ret_3d_rank / ret_5d_rank` 命名）
- `vol_rank_3d / 5d`（当前以 `vol_mean_3d_rank / vol_mean_5d_rank` 命名）
- `amount_rank_3d / 5d`（当前以 `amount_mean_3d_rank / amount_mean_5d_rank` 命名）
- `stock_minus_index_ret_3d / 5d`
- `stock_minus_industry_ret_3d / 5d`

当前状态：
- 指数相对收益需要补稳定的指数收益输入列
- 行业相对收益需要补行业收益上下文列
- 因此 `stock_minus_index_ret_*` / `stock_minus_industry_ret_*` 当前属于可扩展位，后续在 Phase 2 接通
