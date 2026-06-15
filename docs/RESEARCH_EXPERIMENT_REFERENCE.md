# Research Experiment Reference

> 所有 value-growth 实验的配置、特征数、窗口和信号路径一览。
> 新增实验时必须更新本文件的相应章节。

## 特征集

| ID | 特征数 | 来源 |
|----|--------|------|
| `value_growth_multibagger_v1_features` | 26 | configs/features/ PR-165 |
| `value_growth_multibagger_v2_features` | 64 | configs/features/ PR-170 |
| `value_growth_v2_margin_features` | 73 | auto-generated (v2 + 9 margin) |
| `value_growth_v2_shareholder_features` | 74 | auto-generated (v2 + 10 shareholder) |
| `value_growth_multibagger_v3a_features` | 83 | configs/features/ (v2 + margin + shareholder) |

## 实验记录

### V1 Extended

| 字段 | 值 |
|------|----|
| experiment_id | `value_growth_extended_validation` |
| feature_list_id | `value_growth_multibagger_v1_features` (26 feats) |
| 时间范围 | 2013-01-01 → 2025-12-31 |
| 窗口数 | 155 |
| Config | `configs/research/value_growth_extended_validation.yaml` |
| Signal path | `data/research/signals/fwd_ret_180d_raw__daily_zscore/rolling__value_growth_extended_validation__vgb_ext__fwd_ret_180d_raw__daily_zscore__2013-01-01_2025-12-31/` |

### V2 Extended

| 字段 | 值 |
|------|----|
| experiment_id | `value_growth_v2_extended_validation` |
| feature_list_id | `value_growth_multibagger_v2_features` (64 feats) |
| 时间范围 | 2013-01-01 → 2025-12-31 |
| 窗口数 | 155 |
| Config | `configs/research/value_growth_v2_extended_validation.yaml` |
| Signal path | `data/research/signals/fwd_ret_180d_raw__daily_zscore/rolling__value_growth_v2_extended_validation__v2_ext__fwd_ret_180d_raw__daily_zscore__2013-01-01_2025-12-31/` |

### V2 Smoke

| 字段 | 值 |
|------|----|
| experiment_id | `value_growth_v2_feature_smoke` |
| feature_list_id | `value_growth_multibagger_v2_features` (64 feats) |
| 时间范围 | 2023-01-01 → 2025-12-31 |
| Config | `configs/research/value_growth_v2_feature_smoke.yaml` |
| Signal path | `data/research/signals/fwd_ret_180d_raw__daily_zscore/rolling__value_growth_v2_feature_smoke__v2_smoke__fwd_ret_180d_raw__daily_zscore__2023-01-01_2025-12-31/` |

## V1 vs V2 年份对照表 (strict 20d)

| Year | v1 (26 feat) | v2 (64 feat) | Δ |
|------|:-----------:|:-----------:|:---:|
| 2015 | 0.4407 | 0.4782 | +0.037 |
| 2016 | 0.3881 | 0.4442 | +0.056 |
| 2017 | 0.3324 | 0.4129 | +0.081 |
| 2018 | 0.1869 | 0.2385 | +0.052 |
| 2019 | 0.4366 | 0.4787 | +0.042 |
| 2020 | 0.3085 | 0.3698 | +0.061 |
| 2021 | 0.2891 | 0.2707 | −0.018 |
| 2022 | 0.3620 | 0.3451 | −0.017 |
| 2023 | 0.4747 | 0.5262 | +0.052 |
| 2024 | 0.4337 | 0.4782 | +0.045 |
| 2025 | 0.2545 | 0.4515 | +0.197 |
| **Overall** | **0.3717** | **0.4222** | **+0.051** |

v1: 2013-2025 rolling, 504d train, 20d step, 300 est, fwd_ret_180d_raw
v2: 同上配置，只是 feature list 不同

## Ablation 实验（2020-2025, 504d, 20d step, 300 est）

| experiment_id | 特征集 | 特征数 | Config |
|--------------|--------|--------|--------|
| v3a_bl | v2 baseline | 64 | configs/research/abl_baseline.yaml |
| v3a_mg | v2 + margin | 73 | configs/research/abl_margin.yaml |
| v3a_sh | v2 + shareholder | 74 | configs/research/abl_shareholder.yaml |
| v3a_fl | v2 + margin + shareholder | 83 | configs/research/abl_full.yaml |

### Ablation 年份表（跑完后填入）

| Year | v2 baseline | +margin | +shareholder | +full |
|------|:--------:|:------:|:----------:|:----:|
| 2020 | | | | |
| 2021 | | | | |
| 2022 | | | | |
| 2023 | | | | |
| 2024 | | | | |
| 2025 | | | | |
| **Overall** | | | | |

## 信号路径规则

signal_id: `fwd_ret_180d_raw__daily_zscore`
run_id: `rolling__<experiment_id>__<generator_id>__<label_id>__daily_zscore__<start>_<end>`

```bash
# 读取某次实验的信号
python -c "
import pandas as pd
sig_id='fwd_ret_180d_raw__daily_zscore'
run_id='rolling__<experiment_id>__<generator_id>__<label_id>__daily_zscore__<start>_<end>'
df=pd.read_parquet(f'data/research/signals/{sig_id}/{run_id}/predictions.parquet')
print(df.columns.tolist())
print(df['trade_date'].min(), '->', df['trade_date'].max())
"
```
