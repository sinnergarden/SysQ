# SignalArtifact Schema

## 目的

SignalArtifact 记录策略在特定交易日的信号输出——每个股票的模型得分、排名、归一化分数和原始目标权重。这是从模型预测到交易计划的核心中间产物，用于后续的 OrderIntent 生成。

## 格式

CSV（推荐）或 JSON，按策略统一使用一种格式。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `trade_date` | DATE | 交易日，格式 `YYYY-MM-DD` |
| `strategy_id` | STRING | 策略唯一标识，如 `qsys_alpha_v1_blend20_weekly_top20_buffer` |
| `candidate_id` | STRING | 候选版本标识，如 `candidate_20260515_v1` |
| `model_version` | STRING | 模型版本标识，如 `20260515` |
| `signal_version` | STRING | 信号版本标识，如 `v1.0` |
| `data_cutoff` | DATE | 数据截止日期，格式 `YYYY-MM-DD` |
| `instrument` | STRING | 股票代码，如 `600000.SH` |
| `score` | FLOAT | 综合评分（混合后），如 `2.7734` |
| `rank` | INTEGER | 当日在全选股池中的排名，1-based |
| `raw_prediction` | FLOAT | 模型原始预测值（blend 前），如 `1.2345` |
| `normalized_score` | FLOAT | Z-score 或其他方法归一化后的分数 |
| `target_weight_raw` | FLOAT | cap/renorm 前的原始目标权重，如 `0.0952` |
| `created_at` | TIMESTAMP | 产物生成时间，格式 `YYYY-MM-DDTHH:MM:SS` |
| `config_hash` | STRING | 配置哈希，用于追踪参数版本 |
| `feature_schema_version` | STRING | 特征 schema 版本，如 `v1.0` |

## 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `universe` | STRING | 股票池，如 `csi300`，如全局统一可省略 |
| `top_n` | INTEGER | 选股数量，如 `20` |
| `buffer_hold` | INTEGER | 持仓缓冲阈值，如 `60` |
| `buffer_buy` | INTEGER | 买入缓冲阈值，如 `40` |
| `single_stock_cap` | FLOAT | 单股权重上限，如 `0.07` |

## 字段说明

- **score**: 最终用于排序的评分。对于 alpha_v1 为 `0.8 * zscore(pred_5d) + 0.2 * zscore(pred_20d)`。
- **raw_prediction**: 模型直接输出的预测值（各模型 blend 前）。如为多模型集成可存储主要模型的输出。
- **normalized_score**: Z-score 归一化后的分数，用于跨时间截面比较。
- **target_weight_raw**: 经过 rank-linear-decay 计算但尚未施加 cap 和 renormalize 的权重。用于分析 cap 的影响。

## 示例

```csv
trade_date,strategy_id,candidate_id,model_version,signal_version,data_cutoff,instrument,score,rank,raw_prediction,normalized_score,target_weight_raw,created_at,config_hash,feature_schema_version
2026-05-18,qsys_alpha_v1_blend20_weekly_top20_buffer,candidate_20260515_v1,20260515,v1.0,2026-05-18,600584.SH,2.7734,1,1.8562,2.7734,0.0952,2026-05-18T08:00:00,a1b2c3d4,v1.0
2026-05-18,qsys_alpha_v1_blend20_weekly_top20_buffer,candidate_20260515_v1,20260515,v1.0,2026-05-18,300251.SZ,2.3012,2,1.5438,2.3012,0.0905,2026-05-18T08:00:00,a1b2c3d4,v1.0
```

## 验证规则

- `trade_date` 必须为交易日（非周末、非 A 股节假日）。
- `score` 为 `NaN` 或 `Inf` 的行应被过滤。
- `rank` 应在 `[1, N]` 范围内，N 为当日选股池大小。
- `target_weight_raw` 应在 `[0.0, 1.0]` 范围内。
- 必填字段不应为 null。
