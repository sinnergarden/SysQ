# CandidateReport Schema

## 目的

CandidateReport 记录候选策略从 Research 晋升到 Candidate 阶段的完整评估报告。它是策略晋升的审计轨迹，确保每次晋升都有可追溯的记录。

## 格式

Markdown（推荐）或 JSON。

## 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `candidate_id` | STRING | 候选版本标识，如 `candidate_20260515_v1` |
| `strategy_id` | STRING | 所属策略标识 |
| `research_id` | STRING | 来源研究 ID |
| `hypothesis` | TEXT | 核心假设——试图验证什么 |
| `feature_set` | STRING | 使用的特征集，如 `clean_features_v1` |
| `label` | STRING | 标签定义，如 `zscore(fwd_5d_return)` |
| `model` | STRING | 模型描述，如 `LightGBM, 200 trees, 132 features` |
| `train_window` | STRING | 训练窗口，如 `2024-01 to 2026-05` |
| `validation_result` | JSON | 验证集上的评估指标 |
| `backtest_result` | JSON | 回测结果摘要 |
| `risk_summary` | TEXT | 风险评估摘要 |
| `known_issues` | TEXT | 已知问题和限制 |
| `promotion_decision` | STRING | 是否晋升：`approved` / `rejected` / `pending` |
| `next_action` | STRING | 下一步操作，如 `enter_shadow_observation` |

## validation_result 字段结构

```json
{
  "ic_mean": 0.039,
  "rank_ic_mean": 0.054,
  "icir": 0.305,
  "rank_icir": 0.404,
  "group_returns": {
    "group_1": 2.439,
    "group_5": 1.496
  },
  "long_short_return": 0.943,
  "top_k_return": { "top_5": 1.52, "top_10": 1.31, "top_20": 1.12 }
}
```

## backtest_result 字段结构

```json
{
  "total_return_pct": 152.04,
  "annual_return_pct": 53.33,
  "sharpe": 1.771,
  "max_drawdown_pct": -16.12,
  "calmar": 3.309,
  "annual_turnover_x": 35.8,
  "total_fees": 1138128,
  "win_rate_pct": 44.1,
  "trading_days": 545
}
```

## 示例

参见 `docs/templates/candidate-promotion-checklist.md` 和 `docs/alpha_v1_baseline.md`。

## 验证规则

- `candidate_id` 和 `research_id` 应在系统中唯一。
- `promotion_decision` 必须为 `approved` 才能进入 Shadow 阶段。
- `known_issues` 不应为空——每个候选都有已知限制。
