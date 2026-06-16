# v3-a Feature Ablation Plan

> This PR implements research features and ablation configs.
> It does not prove alpha.
> The next PR/report must run the ablation and compare against v2 baseline.

## 1. v3-a Feature Definitions

在 value-growth v2 的 64 个特征基础上新增两组特征：

### Margin Financing（9 特征）

| 特征 | 定义 | 信号逻辑 |
|------|------|---------|
| `margin_eligible` | margin_balance 非空标识 | 区分两融/非两融标的 |
| `margin_balance_to_float_mv` | margin_balance / float_mv | 杠杆资金参与度 |
| `margin_balance_chg_20d` | 20d 两融余额变化率 | 短期杠杆变化 |
| `margin_balance_chg_60d` | 60d 两融余额变化率 | 中期杠杆趋势 |
| `margin_buy_intensity_20d` | 20d 融资买入额 / 成交额 | 杠杆买入强度 |
| `margin_repay_to_buy_20d` | 20d 融资偿还额 / 融资买入额 | 杠杆还款压力 |
| `margin_crowding_score` | zscore(margin_balance_to_float_mv) + zscore(margin_balance_chg_60d) | 杠杆拥挤度 |
| `margin_trend_confirm_score` | zscore(margin_balance_chg_60d) * max(zscore(ret_60d), 0) | 杠杆确认上升趋势 |
| `margin_overheat_risk_score` | zscore(margin_crowding_score) * max(zscore(ret_120d), 0) | 杠杆过热风险 |

### Shareholder Concentration（10 特征）

| 特征 | 定义 | 信号逻辑 |
|------|------|---------|
| `holder_num_chg_qoq` | 股东户数环比变化率 | 筹码分散/集中 |
| `holder_num_chg_2q` | 股东户数两季变化率 | 筹码趋势 |
| `avg_shares_per_holder_chg_qoq` | 户均持股环比变化率 | 人均持股趋势 |
| `top10_holder_ratio` | 前十大股东持股比例 | 机构集中度 |
| `top10_holder_ratio_chg_qoq` | 前十大股东比例环比变化 | 机构增减仓 |
| `holder_concentration_score` | 三个维度的综合分数 | 筹码集中综合指标 |
| `holder_squeeze_score` | 筹码集中 * max(zscore(ret_60d), 0) | 集中+上涨→逼空潜力 |
| `holder_price_confirm_score` | 筹码集中 * max(zscore(ret_120d), 0) | 集中+上涨→趋势确认 |
| `holder_num_stale_days` | 股东户数数据陈旧天数 | 数据新鲜度 |
| `top10_holder_stale_days` | 前十大数据陈旧天数 | 数据新鲜度 |

## 2. Why Raw Single-Feature IC Is Insufficient

PR #176 审计结论：margin 和 shareholder 裸单因子 IC 弱。原因是：
- **Margin**：杠杆数据高度依赖价格语境。单一比例因子不控制趋势/拥挤方向时，信号正负抵消。
- **Shareholder**：季频数据，同一公告日的值持续使用直到下个公告日，daily IC 计算中大量重复值压低区分度。

组合/交互特征（如拥挤度、确认信号）可能释放边际价值。

## 3. Margin Feature Group Design

- 所有比例特征使用 `_safe_div()` 防除零
- 极端值用 `_clip_inf()` 清除 inf
- NaN 保留不填 0（非两融标的不混淆）
- composite 特征用 `_zscore(s.fillna(0))` 以确保非两融标的不干扰截面排序
- 20d/60d change 需要 20/60 天的 lookback 期

## 4. Shareholder Feature Group Design

- **严格 PIT**：用 `merge_asof(ann_date, direction="backward")`，禁止使用 end_date/report_period
- `stale_days` 显式暴露数据延迟，不隐式 forward fill
- 低频数据允许 forward fill，但 `stale_days` 让模型可以学会忽略过期数据
- 季度变化特征（chg_qoq）需要至少 2 个公告日才有值

## 5. PIT and Missing Value Policy

| 数据 | 频率 | PIT 键 | NaN 策略 |
|------|------|--------|---------|
| Margin balance | 日频 | trade_date | NaN=非两融标的，保留 |
| Margin buy/repay | 日频 | trade_date | NaN=非两融标的，保留 |
| Holder number | 季频 | ann_date | NaN=无公告，不参与训练 |
| Top10 holder ratio | 季频 | ann_date | NaN=无公告，不参与训练 |

margin 特征在前面 `groupby("trade_date").transform(lambda s: _zscore(s.fillna(0)))` 中用 fillna(0) 处理，确保非两融标的不干扰截面标准化。

## 6. Controlled Ablation Matrix

| Config | experiment_id | Features | Count |
|--------|-------------|----------|------|
| `abl_baseline.yaml` | v3a_bl | v2 only | 64 |
| `abl_margin.yaml` | v3a_mg | v2 + margin | 73 |
| `abl_shareholder.yaml` | v3a_sh | v2 + shareholder | 74 |
| `abl_full.yaml` | v3a_fl | v2 + margin + shareholder | 83 |

**验证口径**：2020-01-01 ~ 2025-12-31，504d train，20d step，LightGBM 300 estimators，fwd_ret_180d_raw label。

**评估**：Strict 20d non-overlapping RankIC（同 v1 vs v2 方法论）。

## 7. Pass/Fail Criteria

### Strong Pass
- v3a_full RankIC > v2 RankIC + 0.02
- ICIR improves
- top50 excess return improves
- majority of years improve
- no major degradation in 2021/2022/2024 style regimes

### Weak Pass
- RankIC roughly flat
- but top50 quality improves
- or overheat/value-trap diagnostics improve

### Fail
- v3a_full RankIC < v2 RankIC - 0.02
- margin-only and shareholder-only both flat/negative
- top50 excess return worsens
- feature importance of new groups near zero
- new features introduce instability or missing-value artifacts

## 8. Risks and Caveats

- Margin 特征依赖于 qlib bin 中是否存在 margin 字段。如果没有，所有特征静默跳过。
- Shareholder 数据需要本地 `data/canonical/holder_num.parquet` 和 `top10_holder_ratio.parquet`。文件不存在时 fallback 到 NaN。
- shareholder 特征是季频，rolling training 时最近一个公告日的数据在下次公告前保持不变。
- 不要将 margin 特征非两融标的的 NaN 填 0，这会引入伪信号。
- 本 PR 仅做 feature engineering + config。Ablation 需要在下一轮跑。
