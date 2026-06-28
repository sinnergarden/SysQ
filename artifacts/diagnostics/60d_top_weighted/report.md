# 60d Top-Weighted LightGBM — Sample Weight Experiment

## 1. 实验目的

验证在 60d 相同 feature cache 和 baseline feature 组合下，仅通过对头部样本（高 label_value）
加 loss weight，是否能提升 Top20/Top50/Top100 的排序质量。

**不新增 feature。不改 production pipeline。**

## 2. 实验配置

- **Feature list:** `v3a_plus_liquidity_financial_rc` (96 feats)
- **Label:** `fwd_ret_60d_raw`
- **Rolling windows:** 504d train / 20d step / 67 windows
- **Universe:** CSI800
- **Model:** LightGBM regression, 300 trees, early stopping 20
- **Signal transform:** daily_zscore

## 3. Weight Scheme 定义

每个 trade_date 横截面内按 `label_value` 降序 rank(pct=True)：

| Scheme | top10% | top10-20% | rest |
|:---|:---:|:---:|:---:|
| baseline_no_weight | 1.0 | 1.0 | 1.0 |
| top10pct_weight_3x | **3.0** | 1.0 | 1.0 |
| top20pct_weight_2x | 2.0 | 2.0 | 1.0 |
| top10pct_3x_top20pct_2x | **3.0** | **2.0** | 1.0 |

## 4. Storage Semantic

- 本实验为 **research artifact-level signal**，不写入生产 SignalStore
- 每个 scheme 是一个 **独立 model idea**，存储独立 prediction parquet
- 同 scheme 重跑**覆盖**同一文件，不生成 timestamp run_id
- 不覆盖已有 60d baseline signal
- 后续如果某个 scheme 进入正式 candidate，再另开 PR 写入 SignalStore

## 5. IC 对比

### Overall IC

| Scheme                         |      IC |    ICIR |  RankIC | RankICIR |
| ------------------------------ | ------- | ------- | ------- | ------- |
| baseline_no_weight             |  0.0713 |   0.818 |  0.0651 |   0.648 |
| top20pct_weight_2x             |  0.0644 |   0.686 |  0.0422 |   0.385 |
| top10pct_weight_3x             |  0.0641 |   0.637 |  0.0349 |   0.300 |
| top10pct_3x_top20pct_2x        |  0.0624 |   0.642 |  0.0332 |   0.291 |

### Yearly IC

| Scheme                         |    2020 |    2021 |    2022 |    2023 |    2024 |    2025 |
| ------------------------------ | ------- | ------- | ------- | ------- | ------- | ------- |
| baseline_no_weight             |  0.0005 |  0.0642 |  0.0768 |  0.1246 |  0.0741 |  0.0576 |
| top10pct_weight_3x             |  0.0094 |  0.0762 |  0.0606 |  0.0639 |  0.0865 |  0.0660 |
| top20pct_weight_2x             |  0.0119 |  0.0667 |  0.0584 |  0.0753 |  0.0811 |  0.0718 |
| top10pct_3x_top20pct_2x        |  0.0123 |  0.0704 |  0.0624 |  0.0525 |  0.0871 |  0.0696 |

## 6. TopK 质量对比

### Overall

| Scheme                         |  T20mean |  T20hit |  T50mean |  T100mean |
| ------------------------------ | -------- | ------- | -------- | --------- |
| baseline_no_weight             |   0.1018 |  54.18% |   0.0798 |    0.0679 |
| top10pct_weight_3x             |   0.1005 |  52.68% |   0.0800 |    0.0704 |
| top20pct_weight_2x             |   0.1024 |  53.07% |   0.0785 |    0.0687 |
| top10pct_3x_top20pct_2x        |   0.0990 |  52.40% |   0.0814 |    0.0709 |

## 7. TopK Inner RankIC

| Scheme                         | T20inner | T50inner | T100inner |
| ------------------------------ | -------- | -------- | --------- |
| baseline_no_weight             |  -0.0022 |   0.0275 |    0.0301 |
| top10pct_weight_3x             |   0.0618 |   0.0373 |    0.0260 |
| top20pct_weight_2x             |   0.0451 |   0.0349 |    0.0235 |
| top10pct_3x_top20pct_2x        |   0.0470 |   0.0218 |    0.0197 |

## 8. Bucket Lift

### baseline_no_weight

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.0232 |  45.75% |  54.05% |   8.70% |   204870 |
| middle60%    |   0.0393 |  50.25% |  49.50% |   8.79% |   613116 |
| top1%        |   0.1414 |  52.85% |  47.07% |  18.38% |     9289 |
| top10%       |   0.0593 |  53.18% |  46.64% |  11.74% |    51168 |
| top20%       |   0.0520 |  52.41% |  47.36% |  10.60% |   102253 |
| top5%        |   0.0744 |  53.98% |  45.88% |  14.13% |    41051 |

  spread top10-bot20 = 0.0360
  spread top20-bot20 = 0.0287

### top10pct_weight_3x

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.0235 |  47.47% |  52.23% |   7.18% |   204872 |
| middle60%    |   0.0385 |  50.12% |  49.65% |   8.88% |   613101 |
| top1%        |   0.1494 |  55.10% |  44.84% |  19.99% |     9292 |
| top10%       |   0.0641 |  51.75% |  48.08% |  13.67% |    51165 |
| top20%       |   0.0548 |  51.29% |  48.55% |  11.84% |   102268 |
| top5%        |   0.0710 |  51.44% |  48.42% |  14.47% |    41049 |

  spread top10-bot20 = 0.0405
  spread top20-bot20 = 0.0313

### top20pct_weight_2x

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.0242 |  46.93% |  52.79% |   7.70% |   204879 |
| middle60%    |   0.0384 |  50.23% |  49.53% |   8.83% |   613107 |
| top1%        |   0.1448 |  54.85% |  44.98% |  18.37% |     9291 |
| top10%       |   0.0607 |  51.75% |  48.11% |  12.74% |    51168 |
| top20%       |   0.0557 |  51.51% |  48.30% |  11.68% |   102253 |
| top5%        |   0.0716 |  51.95% |  47.94% |  14.69% |    41049 |

  spread top10-bot20 = 0.0365
  spread top20-bot20 = 0.0315

### top10pct_3x_top20pct_2x

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.0247 |  47.70% |  52.00% |   7.33% |   204876 |
| middle60%    |   0.0381 |  50.07% |  49.70% |   8.88% |   613110 |
| top1%        |   0.1490 |  53.68% |  46.21% |  18.88% |     9289 |
| top10%       |   0.0644 |  51.70% |  48.13% |  13.19% |    51165 |
| top20%       |   0.0539 |  51.00% |  48.84% |  11.61% |   102256 |
| top5%        |   0.0723 |  52.20% |  47.67% |  15.17% |    41051 |

  spread top10-bot20 = 0.0397
  spread top20-bot20 = 0.0292


## 9. Weight Scheme Summary

| scheme                         |  n_rows |      w1 |      w2 |      w3 |  mean_w |
| ------------------------------ | ------- | ------- | ------- | ------- | ------- |
| baseline_no_weight             | 24589378 | 24589378 |       0 |       0 |   1.000 |
| top10pct_weight_3x             | 24589378 | 22112150 |       0 | 2477228 |   1.201 |
| top20pct_weight_2x             | 24589378 | 19651799 | 4937579 |       0 |   1.201 |
| top10pct_3x_top20pct_2x        | 24589378 | 19651799 | 2460351 | 2477228 |   1.302 |

## 10. 结论（等待全量结果）

*Pending full run result.*
