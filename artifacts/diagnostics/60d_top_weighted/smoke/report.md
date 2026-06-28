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

### Overall IC（smoke test，仅 2 个窗口）

| Scheme                         |      IC |    ICIR |  RankIC | RankICIR |
| ------------------------------ | ------- | ------- | ------- | ------- |
| top20pct_weight_2x             |  0.0966 |   1.940 |  0.0803 |   1.268 |
| top10pct_weight_3x             |  0.0936 |   1.839 |  0.0761 |   1.259 |
| top10pct_3x_top20pct_2x        |  0.0875 |   1.797 |  0.0655 |   1.110 |
| baseline_no_weight             |  0.0618 |   0.999 |  0.0472 |   0.660 |

## 6. TopK 质量对比

### Overall

| Scheme                         |  T20mean |  T20hit |  T50mean |  T100mean |
| ------------------------------ | -------- | ------- | -------- | --------- |
| baseline_no_weight             |   0.1848 |  64.88% |   0.1736 |    0.1647 |
| top10pct_weight_3x             |   0.1947 |  65.50% |   0.1647 |    0.1580 |
| top20pct_weight_2x             |   0.2195 |  66.62% |   0.1874 |    0.1812 |
| top10pct_3x_top20pct_2x        |   0.2366 |  68.50% |   0.1709 |    0.1563 |

## 7. TopK Inner RankIC

| Scheme                         | T20inner | T50inner | T100inner |
| ------------------------------ | -------- | -------- | --------- |
| baseline_no_weight             |  -0.0726 |   0.0065 |   -0.0142 |
| top10pct_weight_3x             |   0.1002 |   0.0478 |   -0.0051 |
| top20pct_weight_2x             |   0.0455 |  -0.0701 |   -0.0145 |
| top10pct_3x_top20pct_2x        |   0.0797 |   0.0760 |    0.0371 |

## 8. Bucket Lift

### baseline_no_weight

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.1145 |  60.97% |  38.86% |  17.32% |     6390 |
| middle60%    |   0.0928 |  55.72% |  43.98% |  13.38% |    19134 |
| top1%        |   0.1422 |  63.30% |  36.70% |  19.64% |      286 |
| top10%       |   0.1483 |  67.00% |  33.00% |  21.44% |     1600 |
| top20%       |   0.1333 |  63.50% |  36.35% |  17.65% |     3195 |
| top5%        |   0.1974 |  63.91% |  36.02% |  24.45% |     1280 |

  spread top10-bot20 = 0.0338
  spread top20-bot20 = 0.0188

### top10pct_weight_3x

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.0988 |  56.54% |  43.17% |  15.35% |     6390 |
| middle60%    |   0.0947 |  57.12% |  42.61% |  13.36% |    19134 |
| top1%        |   0.2393 |  74.06% |  25.94% |  28.48% |      286 |
| top10%       |   0.1428 |  64.62% |  35.25% |  21.81% |     1600 |
| top20%       |   0.1624 |  66.00% |  33.97% |  22.26% |     3195 |
| top5%        |   0.1601 |  59.38% |  40.55% |  20.70% |     1280 |

  spread top10-bot20 = 0.0440
  spread top20-bot20 = 0.0637

### top20pct_weight_2x

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.0995 |  57.66% |  42.07% |  16.00% |     6390 |
| middle60%    |   0.0932 |  56.45% |  43.29% |  13.11% |    19134 |
| top1%        |   0.1700 |  70.18% |  29.82% |  22.68% |      286 |
| top10%       |   0.2051 |  71.81% |  28.00% |  27.94% |     1600 |
| top20%       |   0.1372 |  64.12% |  35.69% |  19.38% |     3195 |
| top5%        |   0.1790 |  60.23% |  39.69% |  21.95% |     1280 |

  spread top10-bot20 = 0.1056
  spread top20-bot20 = 0.0376

### top10pct_3x_top20pct_2x

| Bucket       | mean_ret |     hit |     bad |    gt30 |        n |
| ------------ | -------- | ------- | ------- | ------- | -------- |
| bottom20%    |   0.1089 |  58.88% |  40.82% |  16.32% |     6390 |
| middle60%    |   0.0950 |  56.90% |  42.84% |  13.55% |    19134 |
| top1%        |   0.2622 |  73.39% |  26.61% |  29.91% |      286 |
| top10%       |   0.1429 |  62.81% |  37.00% |  20.88% |     1600 |
| top20%       |   0.1361 |  62.18% |  37.76% |  19.06% |     3195 |
| top5%        |   0.1663 |  62.89% |  37.03% |  21.88% |     1280 |

  spread top10-bot20 = 0.0340
  spread top20-bot20 = 0.0272


## 9. Weight Scheme Summary

| scheme                         |  n_rows |      w1 |      w2 |      w3 |  mean_w |
| ------------------------------ | ------- | ------- | ------- | ------- | ------- |
| baseline_no_weight             |  799859 |  799859 |       0 |       0 |   1.000 |
| top10pct_weight_3x             |  799859 |  719369 |       0 |   80490 |   1.201 |
| top20pct_weight_2x             |  799859 |  639339 |  160520 |       0 |   1.201 |
| top10pct_3x_top20pct_2x        |  799859 |  639339 |   80030 |   80490 |   1.301 |

## 10. 结论（smoke test，等待全量）

*Pending full run result — this is a smoke test.*
