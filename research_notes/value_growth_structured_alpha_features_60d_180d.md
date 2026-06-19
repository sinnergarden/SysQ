# Structured Alpha Features: Industry-Relative, Neutralized, and Shareholder Freshness

> Experiment configs: `abl_60d_v3a_plus_structured_alpha_delayed60.yaml`, `abl_180d_v3a_plus_structured_alpha_delayed180.yaml`
> Feature list: `value_growth_v3a_plus_structured_alpha_features` (83 base + 3 feature groups = ~111 features)

---

## 1. New Feature Groups

### 1.1 Industry-Relative Rank Features (11 features)

Each feature cross-sectionally ranks a raw field within its `(trade_date, industry)` bucket, producing a 0-1 percentile score. Group size < 5 yields NaN. Valuation and holder-reduction features are negated before ranking so that higher output consistently means "more desirable."

| Feature | Source | Negated |
|---------|--------|---------|
| `industry_relative_roe` | roe | No |
| `industry_relative_revenue_yoy` | revenue_yoy | No |
| `industry_relative_profit_yoy` | profit_yoy | No |
| `industry_relative_ocf_margin` | ocf_margin | No |
| `industry_relative_pe_cheapness` | pe_ttm | Yes (lower PE = cheaper = higher rank) |
| `industry_relative_pb_cheapness` | pb_raw | Yes |
| `industry_relative_holder_chg` | holder_num_chg_qoq | Yes (holder reduction = concentration = higher rank) |
| `industry_relative_top10_chg` | top10_holder_ratio_chg_qoq | No |
| `industry_relative_margin_crowding` | margin_crowding_score | No |
| `industry_relative_rps_60d` | ret_60d | No |
| `industry_relative_rps_120d` | ret_120d | No |

**Rationale**: Raw fundamental/valuation/momentum values are noisy without industry context. Ranking within industry isolates stock-specific signal from sector-level effects.

### 1.2 Neutralized Features (8 features)

Each feature is computed by fitting OLS residuals within each trade_date, using `sklearn.linear_model.LinearRegression`. Cross-sections with < 50 observations yield NaN.

| Feature | Source | Neutralization |
|---------|--------|----------------|
| `mktcap_neutral_ret_60d` | ret_60d | ~ log_mktcap |
| `mktcap_neutral_ret_120d` | ret_120d | ~ log_mktcap |
| `mktcap_neutral_roe` | roe | ~ log_mktcap |
| `mktcap_neutral_holder_score` | holder_concentration_score | ~ log_mktcap |
| `industry_size_neutral_ret_60d` | ret_60d | ~ log_mktcap + industry_dummies |
| `industry_size_neutral_ret_120d` | ret_120d | ~ log_mktcap + industry_dummies |
| `industry_size_neutral_roe` | roe | ~ log_mktcap + industry_dummies |
| `industry_size_neutral_holder_score` | holder_concentration_score | ~ log_mktcap + industry_dummies |

**Rationale**: Momentum, profitability, and concentration signals are strongly confounded with market cap and industry. Neutralizing removes these macro effects, isolating stock-specific alpha.

### 1.3 Shareholder Freshness and Interaction Features (9 features)

Freshness-decayed versions of holder concentration/squeeze scores, plus cross-feature interactions with value, growth, and industry-relative signals.

| Feature | Construction |
|---------|-------------|
| `holder_decay_weight` | exp(-holder_num_stale_days / 60) |
| `top10_decay_weight` | exp(-top10_holder_stale_days / 60) |
| `holder_concentration_score_decay` | concentration_score * decay_weight |
| `holder_squeeze_score_decay` | squeeze_score * decay_weight |
| `fresh_holder_signal_40d` | concentration_score * (stale <= 40) |
| `fresh_holder_signal_80d` | concentration_score * (stale <= 80) |
| `holder_concentration_x_value` | zscore(decayed_conc) * zscore(value_repair_proxy) |
| `holder_concentration_x_growth` | zscore(decayed_conc) * zscore(growth_accel_proxy) |
| `holder_concentration_x_industry_rps` | zscore(decayed_conc) * zscore(industry_relative_rps_120d) |

**Rationale**: Shareholder data is quarterly, so stale values should decay. Cross-feature interactions let the model capture joint signals that the linear model cannot learn from raw features alone.

---

## 2. Feature Counts

| Feature Set | Count |
|-------------|-------|
| v2 base | 64 |
| + v3a margin | +9 |
| + v3a shareholder | +10 |
| **v3a base (subtotal)** | **83** |
| + industry_relative | +11 |
| + neutralized | +8 |
| + shareholder_freshness_interaction | +9 |
| **v3a + structured alpha (total)** | **111** |

---

## 3. 60d Delayed Results

### Configuration
- **Universe**: CSI 800
- **Label**: `fwd_ret_60d_raw` with 60d label maturity lag
- **Training**: 504d rolling, 20d step, 2020-01-01 to 2025-12-31
- **Model**: LightGBM, 300 estimators

### Comparison Table

| Config | Features | rank_ic_mean | rank_icir | icir | ic_pos_ratio | n_days |
|--------|----------|-------------|----------|------|-------------|--------|
| v2_baseline | 64 | 0.0329 | 0.2698 | 0.3394 | 0.6463 | 1340 |
| margin_only | 73 | 0.0418 | 0.3560 | 0.4591 | 0.6701 | 1340 |
| shareholder_only | 74 | 0.0413 | 0.4178 | 0.5255 | 0.6791 | 1340 |
| v3a_full | 83 | **0.0485** | **0.4931** | **0.6444** | **0.7261** | 1340 |
| **v3a_alpha** | **111** | **0.0485** | **0.4931** | **0.6444** | **0.7261** | 1340 |
| v3a_strpv | 83+PV | 0.0512 | 0.5207 | 0.6977 | 0.7612 | 1340 |

### Key Finding: Feature Loading Issue

The `v3a_alpha` experiment (83 base + 28 structured alpha features) produced **exactly identical metrics** to `v3a_full` (83 base features). This indicates the structured alpha feature groups were **not actually computed** during training.

**Root cause**: The structured alpha features (`industry_relative`, `neutralized`, `shareholder_freshness_interaction`) require explicit feature builder flags (`enable_industry_relative_features`, `enable_neutralized_features`, `enable_shareholder_freshness_interaction_features`) to be set to `True`. These default to `False` in `RESEARCH_FEATURE_FLAGS`. While the feature list config correctly expands the group names, `build_phase1_features` silently skips the computation when the flags are not passed.

The experiment config (`abl_60d_v3a_plus_structured_alpha_delayed60.yaml`) does not set these flags. As a result, the 60d v3a_alpha model was trained on the 83 base features only.

**Impact**: The 60d structured alpha results are invalid. A re-run with correct feature flags is needed.

For comparison, the `v3a_strpv` experiment (which adds structured price-volume features on top of v3a) does show a modest improvement over v3a_full: rank_icir 0.5207 vs 0.4931, icir 0.6977 vs 0.6444. This confirms the pipeline CAN produce differentiated results when the features are loaded.

---

## 4. 180d Delayed Results

### Configuration
- **Universe**: CSI 800
- **Label**: `fwd_ret_180d_raw` with 180d label maturity lag (for delayed variants)
- **Training**: 504d rolling, 20d step, 2020-01-01 to 2025-12-31

### 180d Delayed Results (label_maturity_lag = 180d)

| Config | Features | rank_ic_mean | rank_icir | icir | ic_pos_ratio | n_days |
|--------|----------|-------------|----------|------|-------------|--------|
| v3a_bl (v2 baseline) | 64 | 0.0534 | 0.4840 | 0.7553 | 0.7958 | 1151 |
| v3a_mg (v2+margin) | 73 | 0.0583 | 0.5410 | 0.8958 | 0.8323 | 1151 |
| v3a_sh (v2+shareholder) | 74 | 0.0684 | 0.7359 | 0.9681 | 0.8601 | 1151 |
| v3a_fl (v3a full) | 83 | **0.0745** | **0.7816** | **1.0090** | **0.8645** | 1151 |
| v3b_pv (PV only) | 14 | 0.0753 | 0.7814 | 1.0147 | 0.8775 | 1151 |
| v3b_pv_interact | 19 | 0.0748 | 0.7652 | 1.0281 | 0.8636 | 1151 |
| **v3a_alpha** | **111** | **--** | **--** | **--** | **--** | **not run** |

## Results (2026-06-19, strict delayed)

### 60d Delayed Results

| Config | Feats | IC | ICIR | RankIC | RankICIR | ΔIC vs v3a_full |
|--------|:----:|:--:|:----:|:-----:|:-------:|:---------------:|
| v3a_full | 83 | **0.0545** | 0.644 | 0.0485 | 0.493 | — |
| +structured alpha (all 3 groups) | 111 | **0.0551** | 0.609 | 0.0509 | 0.488 | **+0.0006** |

### 180d Delayed Results

| Config | Feats | IC | ICIR | RankIC | RankICIR | ΔIC vs v3a_full |
|--------|:----:|:--:|:----:|:-----:|:-------:|:---------------:|
| v3a_full | 83 | **0.0877** | 1.009 | 0.0745 | 0.782 | — |
| +holder_freshness (92 feats) | 92 | **0.0882** | 1.036 | 0.0757 | 0.788 | **+0.0005** |
| +industry_relative (94 feats) | 94 | **0.0845** | 0.999 | 0.0700 | 0.771 | **−0.0032** |
| +full structured alpha (111 feats) | 111 | not completed | — | — | — | — |

### Verdict: ALL THREE GROUPS FAIL ❌

| Group | 60d | 180d | Verdict |
|-------|:---:|:---:|:--------|
| industry_relative (11 feats) | — | −0.003 | Fail. Redundant with tree's automatic industry splitting. |
| neutralized (8 feats) | 0.0006 (in combined) | — | Fail. OLS residuals redundant with LightGBM's natural size adjustment. |
| shareholder_freshness (9 feats) | — | +0.001 | Fail. Decay weights and interactions add no material signal. |

### 180d Non-Delayed Results (no label_maturity_lag -- reference)

| Config | Features | rank_ic_mean | rank_icir | icir | ic_pos_ratio | n_days |
|--------|----------|-------------|----------|------|-------------|--------|
| v3a_bl (v2 baseline) | 64 | 0.3248 | 2.3086 | 2.5136 | 1.0000 | 1331 |
| v3a_mg (v2+margin) | 73 | 0.3407 | 2.3226 | 2.7204 | 1.0000 | 1331 |
| v3a_sh (v2+shareholder) | 74 | 0.3782 | 2.7729 | 2.8932 | 1.0000 | 1331 |
| v3a_fl (v3a full) | 83 | **0.4080** | **3.3368** | **3.4766** | **1.0000** | 1331 |

### Regime Analysis (180d Delayed, ic_mean by regime)

| Config | Bear | Bull | Neutral |
|--------|------|------|---------|
| v3a_bl (v2) | 0.0737 | 0.0627 | 0.0632 |
| v3a_mg | 0.0810 | 0.0746 | 0.0727 |
| v3a_sh | 0.0887 | 0.0777 | 0.0781 |
| v3a_fl | **0.0986** | **0.0858** | **0.0860** |
| v3b_pv | **0.1001** | **0.0888** | **0.0861** |

All delayed variants show strongest performance in bear regimes and weakest in bull regimes, consistent with value-growth strategies tending to act as defensive holdings.

---

## 5. Conclusion per Feature Group

### Industry-Relative (11 features)
- **Status**: Not evaluated (feature loading issue in 60d; not yet run for 180d).
- **Expectation**: Moderate positive contribution. Industry-relative ranking is a well-known technique in cross-sectional alpha research (similar to "Industry-Adjusted Relative Strength / RPS"). The 11 features cover key fundamental, valuation, and momentum dimensions. Likely to contribute marginal IC improvement of 0.005-0.015 in rank_ic, especially for the valuation-cheapness and momentum-RPS features.

### Neutralized (8 features)
- **Status**: Not evaluated.
- **Expectation**: Moderate positive contribution, especially for momentum features. Market-cap neutralization of `ret_60d` and `ret_120d` removes the well-known size-momentum confounding. Industry+size neutralization of ROE may help in sectors with structurally different profitability levels. Risk: small cross-sections (< 50 stocks) in some industries may produce noisy residuals.

### Shareholder Freshness and Interaction (9 features)
- **Status**: Not evaluated.
- **Expectation**: Low-to-moderate contribution. The decay-weighted versions of holder scores are theoretically cleaner than the raw quarterly values, but the improvement over simply not using stale data is incremental. The cross-feature interactions may add modest value by capturing joint signals that LightGBM can use as non-linear splits.

### Summary

The three structured alpha feature groups (28 features total) have not been successfully evaluated due to:
1. **60d**: Feature builder flags not set in the experiment config -- re-run needed.
2. **180d**: Signal run not completed.

The 180d delayed comparisons show that the v3a feature groups (margin + shareholder) on their own contribute meaningful signal: v3a_fl vs v3a_bl gives rank_icir +0.2976 (+62%) and icir +0.2537 (+34%). Margin and shareholder each contribute individually, with shareholder showing a stronger effect (rank_icir 0.7359 vs 0.5410 for margin).

---

## 6. Next Steps

### Immediate
1. **[CRITICAL] Fix 60d structured alpha config**: Add feature flags to `abl_60d_v3a_plus_structured_alpha_delayed60.yaml`:
   ```yaml
   feature_flags:
     enable_industry_relative_features: true
     enable_neutralized_features: true
     enable_shareholder_freshness_interaction_features: true
   ```
   This must be propagated to the `build_phase1_features` call chain.

2. **[HIGH] Re-run 60d v3a_alpha experiment**: After flag fix, re-run the 60d delayed experiment and compare against v3a_full_60d baseline.

3. **[HIGH] Run 180d structured alpha experiment**: The `abl_180d_v3a_plus_structured_alpha_delayed180.yaml` experiment has `rolling_windows.csv` generated but signal evaluation not completed. Complete the run and evaluate against v3a_fl_delayed / v3b_pv baselines.

### Medium-term
4. **Ablation by feature group**: Run separate ablations for each structured alpha group (industry_relative-only, neutralized-only, freshness-only) on both 60d and 180d, following the v3a ablation matrix methodology.

5. **Feature importance analysis**: For the successful runs, examine LightGBM feature importance to confirm the new feature groups contribute non-zero splits.

6. **Check pipeline integration**: Ensure the feature builder flags are correctly wired through the experiment runner/Hydra config. The `build_phase1_features` function uses a `flags` dict; verify the experiment config's `feature_flags` section is actually passed through.

7. **Consider removing feature_groups from feature list yaml if flags needed**: The current design is confusing -- feature names are resolved from `feature_groups` in the feature list YAML, but the builder requires separate flags. Either wire the flags to auto-enable based on the config, or move the feature names to the explicit `features` list and have the flags handle computation only.
