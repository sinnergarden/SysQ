# Feature 完整清单

> 最后更新: 2026-06-27
> Baseline 特征集合: `v3a_plus_liquidity` (92 features)
> 总计系统特征: ~159

---

## 一、Raw / Native Qlib 字段

这些是 Qlib 原生提供的原始字段。在 feature list 中通过 `$` 前缀引用（如 `$close`）。
v3a+liq 中使用的用对应 `$name` 引用。

| # | feature_id | 表达式 | 含义 | 所属 Group | v3a+liq |
|:---|:---|---:|:---|:---:|:---:|
| 1 | `close` | `$close` | 复权收盘价 | raw | ✅ |
| 2 | `open` | `$open` | 开盘价 | raw | — |
| 3 | `high` | `$high` | 最高价 | raw | — |
| 4 | `low` | `$low` | 最低价 | raw | — |
| 5 | `volume` | `$volume` | 成交量（股） | raw | — |
| 6 | `amount` | `$amount` | 成交额（元） | raw | — |
| 7 | `vwap` | `$vwap` | 成交量加权均价 | raw | — |
| 8 | `turnover_rate` | `$turnover_rate` | 换手率 | raw | — |
| 9 | `pe` | `$pe` | 市盈率 | raw | ✅ |
| 10 | `pb` | `$pb` | 市净率 | raw | ✅ |
| 11 | `roe` | `$roe` | 净资产收益率 | raw | ✅ |
| 12 | `grossprofit_margin` | `$grossprofit_margin` | 毛利率 | raw | ✅ |
| 13 | `debt_to_assets` | `$debt_to_assets` | 资产负债率 | raw | ✅ |
| 14 | `op_cashflow` | `$op_cashflow` | 经营现金流 | raw | ✅ |
| 15 | `industry` | `$industry` | 行业分类 | raw | — |
| 16 | `high_limit` | `$high_limit` | 涨停价 | raw | — |
| 17 | `low_limit` | `$low_limit` | 跌停价 | raw | — |
| 18 | `total_mv` | `$total_mv` | 总市值 | raw | — |
| 19 | `circ_mv` | `$circ_mv` | 流通市值 | raw | — |

---

## 二、Microstructure（微观结构）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 20 | `close_to_open_gap_1d` | `prev_close / open` | 前收盘/开盘缺口 | microstructure | — |
| 21 | `open_to_close_ret` | `close / open - 1` | 日内收益 | microstructure | — |
| 22 | `close_pos_in_range` | `(close-low)/(high-low)` | 收盘价在日内位置 | microstructure | — |
| 23 | `open_pos_in_range` | `(open-low)/(high-low)` | 开盘价在日内位置 | microstructure | — |
| 24 | `upper_shadow_ratio` | `(high-max(c,o))/(h-l)` | 上影线比例 | microstructure | — |
| 25 | `lower_shadow_ratio` | `(min(c,o)-low)/(h-l)` | 下影线比例 | microstructure | — |
| 26 | `intraday_reversal_strength` | `abs(c-o)/(h-l)` | 日内反转强度 | microstructure | — |

---

## 三、Liquidity（流动性）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 27 | `turnover_rate` | $turnover_rate 或 vol/f_shares | 换手率 | liquidity | ✅ |
| 28 | `amount_log` | `log1p(amount)` | 成交额对数 | liquidity | ✅ |
| 29 | `amount_zscore_20` | `rolling_zscore(amount, 20)` | 20日成交额 zscore | liquidity | ✅ |
| 30 | `volume_shock_3` | `vol / rolling_mean(vol,3)` | 3日量比 | liquidity | ✅ |
| 31 | `volume_shock_5` | `vol / rolling_mean(vol,5)` | 5日量比 | liquidity | ✅ |
| 32 | `turnover_acceleration` | `turnover - shift(turnover, 3)` | 换手率加速度 | liquidity | ✅ |
| 33 | `illiquidity` | `abs(ret) / amount` | Amihud 非流动性 | liquidity | ✅ |
| 34 | `amount_log_ind_zscore` | `groupby(ind).zscore(amount_log)` | 行业内成交额对数 zscore | liquidity | ✅ |
| 35 | `turnover_rate_ind_zscore` | `groupby(ind).zscore(turnover_rate)` | 行业内换手率 zscore | liquidity | ✅ |

---

## 四、Tradability（可交易性）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 36 | `is_limit_up` | `close >= high_limit` | 涨停 | tradability | — |
| 37 | `is_limit_down` | `close <= low_limit` | 跌停 | tradability | — |
| 38 | `distance_to_limit_up` | `hig_limit / close - 1` | 距涨停距离 | tradability | — |
| 39 | `distance_to_limit_down` | `close / low_limit - 1` | 距跌停距离 | tradability | — |
| 40 | `limit_up_count_5d` | 5日内涨停次数 | 涨停次数 | tradability | — |
| 41 | `tradability_score` | 综合可交易性评分 | 可交易评分 | tradability | — |
| 42 | `opened_from_limit_up` | `open >= high_limit_prev` | 是否从涨停开盘 | tradability | — |

---

## 五、Relative Strength（相对强弱）

### 5a — 基础

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 43 | `ret_1d` | `close / prev_close - 1` | 1日收益 | relative_strength | — |
| 44 | `ret_3d` | `close / prev_3_close - 1` | 3日收益 | relative_strength | — |
| 45 | `ret_5d` | `close / prev_5_close - 1` | 5日收益 | relative_strength | — |
| 46 | `vol_mean_3d` | `ravg(vol, 3)` | 3日均量 | relative_strength | — |
| 47 | `vol_mean_5d` | `ravg(vol, 5)` | 5日均量 | relative_strength | — |
| 48 | `amount_mean_3d` | `ravg(amount, 3)` | 3日均额 | relative_strength | — |
| 49 | `amount_mean_5d` | `ravg(amount, 5)` | 5日均额 | relative_strength | — |
| 50 | `ret_1d_rank` | `cs_rank(ret_1d)` | 1日收益截面排名 | relative_strength | — |
| 51 | `ret_3d_rank` | `cs_rank(ret_3d)` | 3日收益截面排名 | relative_strength | — |
| 52 | `ret_5d_rank` | `cs_rank(ret_5d)` | 5日收益截面排名 | relative_strength | — |
| 53 | `vol_mean_3d_rank` | `cs_rank(vol_mean_3d)` | 3日均量排名 | relative_strength | — |
| 54 | `vol_mean_5d_rank` | `cs_rank(vol_mean_5d)` | 5日均量排名 | relative_strength | — |
| 55 | `amount_mean_3d_rank` | `cs_rank(amount_mean_3d)` | 3日均额排名 | relative_strength | — |
| 56 | `amount_mean_5d_rank` | `cs_rank(amount_mean_5d)` | 5日均额排名 | relative_strength | — |
| 57 | `stock_minus_index_ret_3d` | `ret_3d - index_ret_3d` | 个股减指数3日收益 | relative_strength | — |
| 58 | `stock_minus_index_ret_5d` | `ret_5d - index_ret_5d` | 个股减指数5日收益 | relative_strength | — |
| 59 | `stock_minus_industry_ret_3d` | `ret_3d - ind_ret_3d` | 个股减行业3日收益 | relative_strength | — |
| 60 | `stock_minus_industry_ret_5d` | `ret_5d - ind_ret_5d` | 个股减行业5日收益 | relative_strength | — |

### 5b — Market Confirmation

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 61 | `ret_20d` | 20日收益 | 20日收益 | relative_strength | ✅ |
| 62 | `ret_60d` | 60日收益 | 60日收益 | relative_strength | ✅ |
| 63 | `ret_120d` | 120日收益 | 120日收益 | relative_strength | ✅ |
| 64 | `volume_ratio_20d` | `vol / ravg(vol,20)` | 20日量比 | relative_strength | ✅ |
| 65 | `volume_ratio_60d` | `vol / ravg(vol,60)` | 60日量比 | relative_strength | ✅ |
| 66 | `distance_to_120d_high` | `close / max_120_close` | 距120日高点距离 | relative_strength | ✅ |
| 67 | `distance_to_250d_high` | `close / max_250_close` | 距250日高点距离 | relative_strength | ✅ |

### 5c — Continuation / Trend Quality（趋势质量）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 68 | `up_day_ratio_60d` | 60日上涨天数比例 | 上涨天数占比 | relative_strength | ✅ |
| 69 | `up_day_ratio_120d` | 120日上涨天数比例 | 上涨天数占比 | relative_strength | ✅ |
| 70 | `trend_smoothness_60d` | `R²(close ~ days, 60)` | 60日趋势平滑度 | relative_strength | ✅ |
| 71 | `trend_smoothness_120d` | `R²(close ~ days, 120)` | 120日趋势平滑度 | relative_strength | ✅ |
| 72 | `max_pullback_120d` | 最大回撤 | 120日最大回撤 | relative_strength | ✅ |
| 73 | `volatility_adjusted_return_60d` | `ret_60d / vol_60d` | 波动率调整60日收益 | relative_strength | ✅ |
| 74 | `volatility_adjusted_return_120d` | `ret_120d / vol_120d` | 波动率调整120日收益 | relative_strength | ✅ |
| 75 | `rps_60d` | `cs_pct(ret_60d)` | 60日相对强弱 | relative_strength | ✅ |
| 76 | `rps_120d` | `cs_pct(ret_120d)` | 120日相对强弱 | relative_strength | ✅ |
| 77 | `rps_20d` | `cs_pct(ret_20d)` | 20日相对强弱 | relative_strength | ✅ |
| 78 | `rps_20d_minus_rps_60d` | `rps_20d - rps_60d` | 短期减中期 RPS | relative_strength | ✅ |
| 79 | `rps_industry_60d` | `cs_pct(stock-ind_ret_60d)` | 行业相对60日 RPS | relative_strength | ✅ |
| 80 | `rps_industry_120d` | `cs_pct(stock-ind_ret_120d)` | 行业相对120日 RPS | relative_strength | ✅ |
| 81 | `price_percentile_252d` | `cs_pct(close,252)` | 252日价格分位数 | relative_strength | ✅ |
| 82 | `distance_to_252d_low` | `close / min_252_close` | 距252日低点距离 | relative_strength | ✅ |

### 5d — Volume Participation（成交量质量）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 83 | `volume_up_down_ratio_60d` | 涨日量/跌日量（60日） | 涨跌量比 | relative_strength | ✅ |
| 84 | `above_avg_volume_ratio_60d` | 超均量天数/60 | 超均量天数比 | relative_strength | ✅ |
| 85 | `amount_ratio_20d` | `amount / ravg(amount,20)` | 20日成交额比 | relative_strength | ✅ |
| 86 | `amount_ratio_60d` | `amount / ravg(amount,60)` | 60日成交额比 | relative_strength | ✅ |
| 87 | `volume_spike_20d` | 20日量尖峰强度 | 量尖峰强度 | relative_strength | ✅ |
| 88 | `volume_stability_60d` | 60日成交量稳定性（1/CV） | 量稳定性 | relative_strength | ✅ |

---

## 六、Regime（市场状态）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 89 | `market_breadth` | `% above MA20` | 站上MA20比例 | regime | — |
| 90 | `limit_up_breadth` | 涨停股票比例 | 涨停广度 | regime | — |
| 91 | `index_volatility_5` | 指数5日波动率 | 指数波动率 | regime | — |
| 92 | `index_volatility_10` | 指数10日波动率 | 指数波动率 | regime | — |
| 93 | `index_volatility_20` | 指数20日波动率 | 指数波动率 | regime | — |
| 94 | `small_vs_large_strength` | 小盘/大盘相对 | 大小盘强弱 | regime | — |
| 95 | `growth_vs_value_proxy` | 成长/价值价差 | 风格价差 | regime | — |
| 96 | `market_trend_strength` | 指数趋势强度 | 趋势强度 | regime | — |

---

## 七、Industry Context（行业背景）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 97 | `industry_ret_1d` | 行业等权1日收益 | 行业1日收益 | industry_context | — |
| 98 | `industry_ret_3d` | 行业等权3日收益 | 行业3日收益 | industry_context | — |
| 99 | `industry_ret_5d` | 行业等权5日收益 | 行业5日收益 | industry_context | — |
| 100 | `industry_breadth` | 行业内上涨占比 | 行业宽度 | industry_context | — |
| 101 | `stock_minus_industry_ret` | 个股-行业收益 | 个股减行业 | industry_context | — |

---

## 八、Fundamental Context（基本面语境）

### 8a — General（通用）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 102 | `log_mktcap` | `log(total_mv)` | 总市值对数 | fundamental_context | — |
| 103 | `float_mktcap` | `circ_mv` | 流通市值 | fundamental_context | — |
| 104 | `pe_ttm` | `$pe` | TTM PE | fundamental_context | — |
| 105 | `pb_raw` | `$pb` | PB | fundamental_context | — |
| 106 | `ps_ttm` | `$ps_ttm` | TTM PS | fundamental_context | — |
| 107 | `roe` | `$roe` | ROE | fundamental_context | — |
| 108 | `roa` | `net_income / total_assets` | ROA | fundamental_context | ✅ |
| 109 | `gross_margin` | `$grossprofit_margin` | 毛利率 | fundamental_context | — |
| 110 | `net_margin` | `net_income / revenue` | 净利率 | fundamental_context | ✅ |
| 111 | `operating_cf_to_profit` | `op_cashflow / net_income` | 经营现金流/净利润 | fundamental_context | ✅ |
| 112 | `debt_to_asset` | `$debt_to_assets` | 资产负债率 | fundamental_context | — |
| 113 | `revenue_yoy` | `revenue / prev_revenue - 1` | 营收同比 | fundamental_context | ✅ |
| 114 | `profit_yoy` | `net_income / prev_ni - 1` | 净利润同比 | fundamental_context | ✅ |

### 8b — Growth Quality（增长质量）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 115 | `roe_delta_252d` | `roe - shift(roe,252)` | ROE 252日变化 | fundamental_context | ✅ |
| 116 | `grossprofit_margin_delta_252d` | `margin - shift(margin,252)` | 毛利率 252日变化 | fundamental_context | ✅ |
| 117 | `debt_to_assets_delta_252d` | `debt - shift(debt,252)` | 杠杆 252日变化 | fundamental_context | ✅ |
| 118 | `op_cashflow_delta_252d` | `cf - shift(cf,252)` | 经营现金流 252日变化 | fundamental_context | ✅ |

### 8c — Valuation Repair（估值修复空间）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 119 | `pe_rank_252d` | `cs_pct(pe,252)` | PE 252日分位数 | fundamental_context | ✅ |
| 120 | `pb_rank_252d` | `cs_pct(pb,252)` | PB 252日分位数 | fundamental_context | ✅ |
| 121 | `pe_delta_120d` | `pe - shift(pe,120)` | PE 120日变化 | fundamental_context | ✅ |
| 122 | `pb_delta_120d` | `pb - shift(pb,120)` | PB 120日变化 | fundamental_context | ✅ |
| 123 | `pe_percentile_756d` | `cs_pct(pe,756)` | PE 756日分位数 | fundamental_context | ✅ |
| 124 | `pb_percentile_756d` | `cs_pct(pb,756)` | PB 756日分位数 | fundamental_context | ✅ |
| 125 | `pe_distance_from_756d_low` | `pe / min_756_pe` | PE距756日低点 | fundamental_context | ✅ |
| 126 | `pb_distance_from_756d_low` | `pb / min_756_pb` | PB距756日低点 | fundamental_context | ✅ |
| 127 | `pe_repair_room_to_median` | `median_756_pe / pe` | PE回升空间到中位数 | fundamental_context | ✅ |
| 128 | `pb_repair_room_to_median` | `median_756_pb / pb` | PB回升空间到中位数 | fundamental_context | ✅ |
| 129 | `earnings_yield_proxy` | `1 / pe` | 盈利收益率代理 | fundamental_context | ✅ |
| 130 | `peg_proxy` | `pe / yoy_earnings_growth` | PEG 代理 | fundamental_context | ✅ |

### 8d — Fundamental Acceleration（基本面加速度）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 131 | `revenue_yoy_accel` | `yoy_growth - shift(yoy_growth)` | 营收同比变化加速度 | fundamental_context | ✅ |
| 132 | `profit_yoy_accel` | `yoy_growth - shift(yoy_growth)` | 净利同比变化加速度 | fundamental_context | ✅ |
| 133 | `roe_delta_756d` | `roe - shift(roe,756)` | ROE 756日变化 | fundamental_context | ✅ |
| 134 | `net_margin_delta_756d` | `margin - shift(margin,756)` | 净利率 756日变化 | fundamental_context | ✅ |
| 135 | `ocf_margin` | `op_cashflow / revenue` | 经营现金流利润率 | fundamental_context | ✅ |

### 8e — Path Classifier Scores（路径分类评分）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 136 | `continuation_candidate_score` | 趋势延续候选评分 | 趋势延续评分 | fundamental_context | ✅ |
| 137 | `repair_candidate_score` | 修复候选评分 | 修复/反转评分 | fundamental_context | ✅ |
| 138 | `overheat_risk_score` | 过热风险评分 | 过热风险 | fundamental_context | ✅ |
| 139 | `value_trap_risk_score` | 价值陷阱风险评分 | 价值陷阱 | fundamental_context | ✅ |

---

## 九、v3a Margin（两融）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 140 | `margin_eligible` | 是否标的 | 两融资格 | v3a_margin | ✅ |
| 141 | `margin_balance_to_float_mv` | `bal / float_mv` | 融资余额/流通市值 | v3a_margin | ✅ |
| 142 | `margin_balance_chg_20d` | `bal - shift(bal,20)` | 融资余额 20日变化 | v3a_margin | ✅ |
| 143 | `margin_balance_chg_60d` | `bal - shift(bal,60)` | 融资余额 60日变化 | v3a_margin | ✅ |
| 144 | `margin_buy_intensity_20d` | 20日融资买入强度 | 买入强度 | v3a_margin | ✅ |
| 145 | `margin_repay_to_buy_20d` | `repay / buy` | 偿还/买入比 | v3a_margin | ✅ |
| 146 | `margin_crowding_score` | 融资拥挤度评分 | 拥挤度 | v3a_margin | ✅ |
| 147 | `margin_trend_confirm_score` | 融资趋势确认 | 趋势确认 | v3a_margin | ✅ |
| 148 | `margin_overheat_risk_score` | 融资过热风险 | 过热风险 | v3a_margin | ✅ |

---

## 十、v3a Shareholder（股东/筹码）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 149 | `holder_num_chg_qoq` | `hn / prev_hn - 1` | 股东户数环比变化 | v3a_shareholder | ✅ |
| 150 | `holder_num_chg_2q` | `hn / prev_2q_hn - 1` | 股东户数2季变化 | v3a_shareholder | ✅ |
| 151 | `avg_shares_per_holder_chg_qoq` | 户均持股环比变化 | 户均持股变化 | v3a_shareholder | ✅ |
| 152 | `top10_holder_ratio_chg_qoq` | `t10/t10_prev - 1` | 前十大占比变化 | v3a_shareholder | ✅ |
| 153 | `holder_concentration_score` | 筹码集中度评分 | 集中度 | v3a_shareholder | ✅ |
| 154 | `holder_squeeze_score` | 筹码收紧评分 | 收紧 | v3a_shareholder | ✅ |
| 155 | `holder_price_confirm_score` | 筹码 x 价格趋势确认 | 价格确认 | v3a_shareholder | ✅ |
| 156 | `holder_num_stale_days` | 股东数据滞后天数 | 数据新鲜度 | v3a_shareholder | ✅ |
| 157 | `top10_holder_stale_days` | 前十数据滞后天数 | 数据新鲜度 | v3a_shareholder | ✅ |
| 158 | `top10_holder_ratio` | `top10_hold/total_share` | 前十大股东占比 | v3a_shareholder | ✅ |

---

## 十一、v3b Price Volume（价量质量）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 159 | `trend_consistency_60d` | 趋势一致性评分 | 60日趋势一致性 | v3b_price_volume | — |
| 160 | `trend_consistency_120d` | 趋势一致性评分 | 120日趋势一致性 | v3b_price_volume | — |
| 161 | `low_vol_uptrend_60d` | 低波上涨评分 | 60日低波上涨 | v3b_price_volume | — |
| 162 | `low_vol_uptrend_120d` | 低波上涨评分 | 120日低波上涨 | v3b_price_volume | — |
| 163 | `return_drawdown_ratio_60d` | `ret_60d / max_dd_60d` | 收益/回撤比60日 | v3b_price_volume | — |
| 164 | `return_drawdown_ratio_120d` | `ret_120d / max_dd_120d` | 收益/回撤比120日 | v3b_price_volume | — |
| 165 | `pullback_recovery_speed_60d` | 回撤恢复速度 | 60日恢复速度 | v3b_price_volume | — |
| 166 | `new_high_persistence_120d` | 新高持续性 | 120日创新高稳定性 | v3b_price_volume | — |
| 167 | `up_volume_down_volume_ratio_60d` | 涨日量/跌日量 | 60日涨跌量比 | v3b_price_volume | — |
| 168 | `up_volume_down_volume_ratio_120d` | 涨日量/跌日量 | 120日涨跌量比 | v3b_price_volume | — |
| 169 | `volume_contraction_after_rise_60d` | 上涨后缩量 | 60日涨后缩量 | v3b_price_volume | — |
| 170 | `quiet_accumulation_60d` | 静默吸筹评分 | 60日吸筹评分 | v3b_price_volume | — |
| 171 | `amount_stability_60d` | `mean(amt)/std(amt)` | 60日成交额稳定性 | v3b_price_volume | — |
| 172 | `breakout_volume_quality_120d` | 突破量能质量 | 120日突破量能 | v3b_price_volume | — |

---

## 十二、v3b Interaction（交叉特征）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 173 | `holder_concentration_trend_confirm` | `conc_score × trend_score` | 筹码集中 x 趋势确认 | v3b_interaction | — |
| 174 | `holder_concentration_low_vol_uptrend` | `conc_score × low_vol_score` | 筹码集中 x 低波上涨 | v3b_interaction | — |
| 175 | `holder_concentration_volume_contract` | `conc_score × vol_contract` | 筹码集中 x 缩量 | v3b_interaction | — |
| 176 | `margin_holder_trend_confirm` | `margin × holder × trend` | 融资 x 筹码 x 趋势 | v3b_interaction | — |
| 177 | `margin_pullback_recovery_confirm` | `margin × pullback` | 融资 x 回撤恢复 | v3b_interaction | — |

---

## 十三、Industry Momentum（行业动量）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 178 | `industry_ret_20d` | 行业等权 20日收益 | 行业20日收益 | industry_momentum | — |
| 179 | `industry_ret_60d` | 行业等权 60日收益 | 行业60日收益 | industry_momentum | — |
| 180 | `industry_ret_120d` | 行业等权 120日收益 | 行业120日收益 | industry_momentum | — |
| 181 | `industry_breadth_20d` | 行业内上涨占比 | 20日行业宽度 | industry_momentum | — |
| 182 | `industry_breadth_60d` | 行业内上涨占比 | 60日行业宽度 | industry_momentum | — |
| 183 | `industry_new_high_ratio` | 行业创新高比例 | 行业新高 | industry_momentum | — |
| 184 | `industry_top_stock_momentum` | 行业龙头动量 | 龙头动量 | industry_momentum | — |
| 185 | `industry_volume_expansion` | 行业量扩张 | 行业量扩张 | industry_momentum | — |
| 186 | `stock_industry_ret_corr_60d` | 个股-行业60d 相关性 | 个行业相关性 | industry_context | — |
| 187 | `stock_minus_industry_ret_20d` | `ret_20d - ind_ret_20d` | 个股减行业20日 | industry_context | — |
| 188 | `stock_minus_industry_ret_60d` | `ret_60d - ind_ret_60d` | 个股减行业60日 | industry_context | — |

---

## 十四、Growth Confirmation v0（业绩确认）

| # | feature_id | 表达式 | 含义 | Group | v3a+liq |
|:---|:---|---|:---|:---:|:---:|
| 189 | `forecast_type_score` | `type→score映射` | 业绩预告类型评分 | growth_confirmation_v0 | — |
| 190 | `forecast_stale_days` | `trade_date - ann_date` | 距业绩预告天数 | growth_confirmation_v0 | — |
| 191 | `has_forecast` | `1/0` | 是否有业绩预告 | growth_confirmation_v0 | — |
| 192 | `ttm_revenue_yoy` | `ttm_rev / prev_ttm - 1` | TTM 营收同比 | growth_confirmation_v0 | — |
| 193 | `single_q_revenue_yoy` | `q_rev / prev_q_rev - 1` | 单季营收同比 | growth_confirmation_v0 | — |
| 194 | `is_profitable_ttm` | `ttm_n_income > 0` | TTM 是否盈利 | growth_confirmation_v0 | — |
| 195 | `gross_margin_delta_yoy` | `gm_q - gm_q_ly` | 单季毛利率同比变化 | growth_confirmation_v0 | — |
| 196 | `breakout_252d_high` | `close >= max_252 * shift(1)` | 252日新高 | growth_confirmation_v0 | — |
| 197 | `days_since_252d_high` | 距最近新高天数 | 距新高天数 | growth_confirmation_v0 | — |

---

## 统计

| 组 | 总数 | 在 v3a+liq 中 |
|:---|---:|:---:|
| Raw/Native | 19 | 7 |
| Microstructure | 7 | 0 |
| Liquidity | 9 | 9 |
| Tradability | 7 | 0 |
| Relative Strength | 45 | 30 |
| Regime | 8 | 0 |
| Industry Context | 5 | 0 |
| Fundamental Context | 38 | 27 |
| v3a Margin | 9 | 9 |
| v3a Shareholder | 10 | 10 |
| v3b Price Volume | 14 | 0 |
| v3b Interaction | 5 | 0 |
| Industry Momentum | 11 | 0 |
| Growth Confirmation v0 | 9 | 0 |
| **Total** | **197** | **92** |

> **v3a+liq 的 92 个 features 构成** = 7 raw ($) + 9 liquidity + 30 relative strength + 27 fundamental + 9 margin + 10 shareholder
