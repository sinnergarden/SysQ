# FEATURE: mainline_rolling_and_readiness

## Goal

把当前三条主线对象的 rolling 研究链路固定成一个最小、稳定、可重复执行的入口，并补上只服务于这三条对象的 readiness / coverage / degradation 闭环。

本文件只覆盖：

- `feature_173`
- `feature_254`
- `feature_254_absnorm`

不试图把它扩成通用研究平台。

## Mainline Objects

三条主线对象：

- `feature_173`：历史 `extended` 主线，当前 baseline + candidate
- `feature_254`：历史 `semantic_all_features` 主线，当前 research_only
- `feature_254_absnorm`：历史 `semantic_all_features_absnorm` 主线，当前 research_only

## Stable Rolling Entry

固定入口：

- `scripts/run_mainline_rolling_pipeline.py`

它串起四步：

1. `scripts/run_mainline_rolling_eval.py`
2. `scripts/run_mainline_rolling_comparison.py`
3. `scripts/update_mainline_decision_evidence.py`
4. `scripts/publish_mainline_rolling_ui_reports.py`

默认输出目录：

- rolling outputs：`experiments/mainline_rolling/`
- UI reports：`experiments/reports/`

## Rolling Artifact Contract

每个主线对象都固定产出：

- `rolling_summary.json`
- `rolling_windows.csv`
- `rolling_metrics.csv`

整体比较固定产出：

- `comparison_summary.csv`
- `comparison_summary.md`

## UI Recognition Contract

rolling 结果会被最小发布成现有 UI 可识别的 report：

- `backtest_mainline_rolling_feature_173.json`
- `backtest_mainline_rolling_feature_254.json`
- `backtest_mainline_rolling_feature_254_absnorm.json`

UI 能识别它们的原因不是新增页面，而是：

- 这些文件被发布到现有 `experiments/reports/`
- 文件名遵循现有 `backtest_*.json` 扫描约定
- `model_info.mainline_object_name` 用于把三条主线 run 区分开
- 每个 report 还挂了最小 `daily_result`，所以现有 `/api/backtest-runs/<run_id>/daily` 也能工作

## Readiness Entry

固定入口：

- `scripts/run_mainline_readiness_audit.py`

默认输出目录：

- `experiments/mainline_readiness/`

每个主线对象固定产出：

- `feature_readiness_summary.json`
- `feature_coverage_by_field.csv`
- `feature_dead_or_constant.csv`
- `feature_missingness_summary.json`

## Degradation Rule

只保留三层：

- `core_ok`
- `extended_warn`
- `extended_blocked`

含义：

- `core_ok`：主链路字段覆盖与可训练性正常
- `extended_warn`：扩展层字段有覆盖/缺失/死因子问题，但 core 仍可跑；主流程应降级告警，不应阻断
- `extended_blocked`：只有 core 自身字段坏到不可训练，才允许阻断主流程

当前这套规则只服务于三条主线对象的 readiness 解释，不引申到 broader ops framework。
