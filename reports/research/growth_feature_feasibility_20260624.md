# Growth Confirmation Feature Feasibility Research

> Date: 2026-06-22
> Source: `scripts/research/feature_feasibility_growth_confirmation.py`

## 1. Feature Feasibility Summary

| 等级 | 数量 | 特征 |
|------|------|------|
| **✅ 可直接做** | 2 | `breakout_252d_high`, `days_since_252d_high` |
| **🟡 需要 Tushare 后可做** | 6 | `forecast_type_score`, `ttm_revenue_yoy`, `single_q_revenue_yoy`, `is_profitable_ttm`, `is_profitable_latest_q`, `gross_margin_delta` |
| **🔴 暂时不建议** | 4 | `contract_liability_yoy`, `advance_receipts_yoy`, `express_revenue_yoy`, `express_profit_yoy` |
| **🟡 依赖上游** | 3 | `revenue_yoy_above_40`, `revenue_growth_consistency_4q`, `forecast_profit_yoy_mid` |

## 2. PIT Risk Summary

| 风险 | 特征 |
|------|------|
| **无风险** | `breakout_252d_high`, `days_since_252d_high`（日频数据，无 PIT 问题） |
| **低风险** | `is_profitable_latest_q`, `single_q_revenue_yoy`, `contract_liability_yoy`, `advance_receipts_yoy`, `forecast_*`, `express_*`（均有 ann_date，严格 merge_asof 可用） |
| **中风险** | `is_profitable_ttm`, `ttm_revenue_yoy`, `gross_margin_delta`（qlib 有近似字段但无 ann_date） |

**结论：** Tushare 的 income/balancesheet/forecast/express 表都包含 ann_date，可严格 PIT。当前 qlib 的 fina_indicator 没有 ann_date，使用 qlib 数据时需要依赖其 forward-fill，存在轻微未来函数风险。

## 3. Coverage Summary

现有 qlib 字段在 CSI800 中的覆盖率：

| 字段 | 覆盖率 | 有数据的股票数 |
|------|--------|--------------|
| $roe | **99.9%** | 797/797 |
| $revenue | **99.9%** | 797/797 |
| $net_income | **99.9%** | 797/797 |
| $op_cashflow | **99.9%** | 797/797 |
| $grossprofit_margin | 90.7% | 724/797 |
| $debt_to_assets | **99.9%** | 797/797 |
| $total_assets | **99.9%** | 797/797 |

Tushare 独有字段（forecast/express/balancesheet）的覆盖率暂无法估计，需获取数据后评估。

## 4. Signal Sanity Check（关键发现）

### 4.1 `breakout_252d_high`（near 252d high = price_percentile > 0.95）

| 指标 | Near 252d high | Not near high |
|------|---------------|---------------|
| 全样本 mean_ret | **+0.092** | **+0.122** |
| Score top5% mean_ret | **+0.089** | **+0.230** |
| Score top5% bad_fp (ret<0.1) | **68.4%** | **51.9%** |
| Score top5% big_win (ret>0.6) | 11.8% | 15.2% |

**反直觉结论：near 252d high 是一个负向信号，不是正向信号。** 处于 252d 高位的股票后续 180d 表现反而更差。在 score top5% 中，near_high 的 bad_fp 高达 68.4%（vs 51.9%），big_win仅 11.8%（vs 15.2%）。

这与之前 bad case mining 的结论一致：高分+高位=过度乐观，后续表现不佳。

### 4.2 Value zone（price_percentile 30-70%）

- 全样本 mean_ret: 30-70% zone = +0.1195
- **30% 的 super winners（ret>1.0）在爆发前处于这个区间**
- 模型 miss 的 super winners 中有 30% 在这个区间

这表明 **"启动前的价值区间"是一个 real pattern**，但单独用 price_percentile 不够精确。

## 5. 推荐进入下一轮 Implementation 的特征（最多 5 个）

### 🏆 1. `breakout_252d_high`
- **数据源**: close（已有，无需新数据）
- **计算**: `close >= close.rolling(252).max() * 0.98`
- **PIT**: 无（日频数据）
- **预期作用**: 不是"买入突破"信号，而是**辅助识别 overheat 状态**。与 score 结合：high score + near high → caution。
- **风险**: 信号方向是负的，不能单独作为买入信号。
- **实现优先级**: ⭐⭐⭐⭐⭐（无外部依赖，一行代码）

### 🏆 2. `forecast_type_score`
- **数据源**: Tushare forecast（需要新接口）
- **计算**: 预告类型映射为数值（预增=+2, 略增/扭亏=+1, 续盈=0, 预减/略减=-1, 首亏/续亏=-2）
- **PIT**: ✅ 有 ann_date，低风险
- **预期作用**: 直接反映管理层对未来业绩的判断，是基本面改善的领先信号
- **风险**: 覆盖率中等（SZ 强制披露，SH 可选）
- **实现优先级**: ⭐⭐⭐⭐（需要 Tushare，但信息价值高）

### 🏆 3. `ttm_revenue_yoy`
- **数据源**: Tushare income（ann_date）
- **计算**: 最近四季 revenue / 去年同四季 revenue - 1
- **PIT**: ✅ 有 ann_date
- **预期作用**: 核心成长性指标，是多个衍生特征的基础
- **风险**: qlib 已有 $revenue 但无 ann_date；Tushare 版本可实现严格 PIT
- **实现优先级**: ⭐⭐⭐⭐

### 🏆 4. `single_q_revenue_yoy`
- **数据源**: Tushare income（需要从累计值差分）
- **计算**: 单季 revenue / 去年同季 revenue - 1
- **PIT**: ✅ 有 ann_date
- **预期作用**: 比 TTM 更敏感的近期变化检测，可提前发现拐点
- **风险**: 需要 Tushare income 表，构造单季值增加复杂度
- **实现优先级**: ⭐⭐⭐（建议在 ttm_revenue_yoy 后做）

### 🏆 5. `gross_margin_delta_qoq_or_yoy`
- **数据源**: Tushare income（x_sprofit, revenue）或 qlib fina_indicator($grossprofit_margin)
- **计算**: 最新毛利率同比（同季 vs 去年同季）
- **PIT**: 🟡 中风险（qlib 无 ann_date；Tushare 版本严格）
- **预期作用**: 毛利率变化是盈利能力改善的直接信号，且与当前 model 已使用的 grossprofit_margin 形成差分补充
- **风险**: qlib 版本无 ann_date
- **实现优先级**: ⭐⭐⭐（建议优先用 Tushare 版本）

## 6. 不推荐的特征

| 特征 | 不推荐原因 |
|------|-----------|
| `contract_liability_yoy` | 新准则后部分企业已不披露，覆盖不稳定；与 advance_receipts 含义部分重叠；需要 balancesheet 表，当前无数据 |
| `advance_receipts_yoy` | 同上，新准则后大量转入 contract_liability；两者需要合并处理才有稳定信号 |
| `express_revenue_yoy` | 覆盖率中等（40-50%），且 express 通常在正式财报前 1-2 周发布，窗口短；获取数据的边际收益有限 |
| `express_profit_yoy` | 同上 |

## 7. 执行建议

**当前最优先：实现 breakout_252d_high（无需新数据，验证结果明确）。**

步骤：
1. 在 `build_liquidity_features` 中添加 `close.rolling(252).max()` 计算
2. 注册 `breakout_252d_high` 和 `days_since_252d_high` 到 FEATURE_GROUPS 和 registry_v2
3. 回填 FeatureStore → 验证 IC 变化

**下一步：接入 Tushare forecast 接口，实现 forecast_type_score。**

步骤：
1. 在 `data_sync.py` 中新增 forecast 数据同步任务
2. 按 ann_date PIT merge 到日频面板
3. 构造 forecast_type_score 二值/多值特征
4. 添加到 FeatureStore + 验证
