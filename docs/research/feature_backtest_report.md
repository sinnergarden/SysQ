# Feature Backtest Report

## 口径

- 训练区间：`2020-01-01 ~ 2024-12-31`
- 测试区间：`2025-01-02 ~ 2026-03-20`
- 股票池：`csi300`
- 持仓：`top_k = 5`
- 目标：在不 overlap 的前提下比较 baseline 与 research feature set 的最小可用效果

## 回测结果

| 模型 | feature_count | total_return | annual_return | annual_vol | sharpe | max_drawdown | trade_count |
|---|---:|---:|---:|---:|---:|---:|---:|
| `qlib_lgbm` | 158 | -4.59% | -3.99% | 35.89% | -0.111 | -33.13% | 2041 |
| `qlib_lgbm_phase1` | 173 | 4.50% | 3.88% | 31.17% | 0.125 | -35.28% | 1999 |
| `qlib_lgbm_phase12` | 173 | -0.86% | -0.75% | 30.72% | -0.024 | -34.95% | 1978 |
| `qlib_lgbm_phase123` | 180 | 22.03% | 18.82% | 31.45% | 0.598 | -27.79% | 1833 |

## 训练摘要

| 模型 | mse | rank_ic |
|---|---:|---:|
| `qlib_lgbm` | 0.8743 | 0.3996 |
| `qlib_lgbm_phase1` | 0.8294 | 0.4607 |
| `qlib_lgbm_phase12` | 0.8294 | 0.4607 |
| `qlib_lgbm_phase123` | 0.8232 | 0.4734 |

## 当前结论

- 在非重叠切分下，`phase123` 明显优于 baseline。
- `phase1` 也优于 baseline，但幅度有限。
- `phase12` 当前不如 `phase1`，但这一结论不能直接解释为“Phase 2 无效”。

## 重要 caveat

当前 `phase1` 与 `phase12` 的训练摘要完全一致：
- `sample_count` 一样
- `feature_count` 一样
- `mse / rank_ic` 一样

这说明二者底层训练矩阵还没有真正分开。

因此：
- `phase123` 优于 baseline 这个结论成立
- `phase1 vs phase12` 的比较目前**不能过度解读**
- 若要判断 Phase 2 的真实增益，必须先把 `phase1 / phase12` 对应到不同的训练特征矩阵，再重跑实验

## 特征有效性补充

当前 `Phase123` 共检 97 个特征：
- 82 个缺失率 < 10%
- 6 个缺失率在 10% ~ 50%
- 9 个缺失率 >= 50%，当前不应直接纳入训练

较稳的一批包括：
- `open_to_close_ret`
- `amount_log`
- `is_limit_up / is_limit_down`
- `distance_to_limit_up / distance_to_limit_down`
- `tradability_score`
- `industry_breadth`
- `market_breadth`

## 下一步

1. 把 `phase1 / phase12` 真正拆成不同训练矩阵
2. 用 ready feature 子集继续训练与回测
3. 再做更长窗口、更严格的 ablation
