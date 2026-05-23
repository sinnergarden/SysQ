# Research Report Template

> 使用本模板记录每次策略研究的完整过程和结果。完成后的报告是 candidate promotion 的必要前置条件。

## 1. 基本信息

| 字段 | 内容 |
|------|------|
| Research ID | `research_YYYYMMDD_descriptive_name` |
| Date | YYYY-MM-DD |
| Author | |
| Strategy Family | `alpha_v1` / `alpha_v2` / ... |
| Related Research | 相关研究 ID 列表 |

## 2. 核心假设 (Hypothesis)

<!-- 一句话说明要验证的核心假设 -->

> 例：'使用资金流因子增强现有 clean features 可以提升 top-20 选股的 RankIC'

## 3. 实验设置

### 特征集 (Feature Set)

| 维度 | 内容 |
|------|------|
| Feature Set Name | |
| Feature Count | |
| Feature Groups | |
| 相比 Baseline 的变化 | |

### 标签 (Label)

| 维度 | 内容 |
|------|------|
| Label Definition | |
| Horizon | |
| Normalization | |

### 选股池 (Universe)

| 维度 | 内容 |
|------|------|
| Universe | CSI300 / CSI800 / Custom |
| Filter Criteria | |

### 时间窗口

| 窗口 | 起止日期 |
|------|---------|
| Train | YYYY-MM-DD ~ YYYY-MM-DD |
| Validation | YYYY-MM-DD ~ YYYY-MM-DD |
| Test | YYYY-MM-DD ~ YYYY-MM-DD |

## 4. 模型

| 维度 | 内容 |
|------|------|
| Model Type | LightGBM / XGBoost / NN / Ensemble |
| Hyperparameters | key: value |
| Training Recipe | |

## 5. 评估指标

### IC / RankIC

| 指标 | Train | Valid | Test |
|------|-------|-------|------|
| Mean IC | | | |
| Mean RankIC | | | |
| ICIR | | | |
| RankICIR | | | |
| IC Std | | | |

### 分组收益 (Group Return)

| Group | Test Period Return |
|-------|-------------------|
| Group 1 (top) | |
| Group 2 | |
| Group 3 | |
| Group 4 | |
| Group 5 (bottom) | |

### 多空收益 (Long-Short Return)

| 指标 | 值 |
|------|----|
| Long-Short Return | |
| Long-Short Sharpe | |

### Top-K 收益

| K | Annual Return | Sharpe |
|---|--------------|--------|
| 5 | | |
| 10 | | |
| 20 | | |

## 6. 交易成本分析 (Cost Sensitivity)

| 假设场景 | Annual Return | Sharpe | Turnover |
|---------|--------------|--------|----------|
| 0-cost | | | |
| Current (0.03% comm + 0.1% slip) | | | |
| 2x cost | | | |
| 5x cost | | | |

## 7. 行业 / 市值暴露分析

<!-- 检查策略的行业和市值风格暴露 -->

| 维度 | 分析结果 |
|------|---------|
| 行业集中度 | |
| 市值风格 | |
| 风格漂移 | |

## 8. 数据泄漏检查

| 检查项 | 结果 |
|--------|------|
| Train/Test 时间不重叠 | ✅ / ❌ |
| 未来信息未引入 features | ✅ / ❌ |
| 标签无前视偏差 | ✅ / ❌ |
| 回测使用前复权价格正确 | ✅ / ❌ |

## 9. 结论

<!-- 总结研究发现，明确是否值得推进到 Candidate -->

- [ ] 显著优于 Baseline
- [ ] 有增量贡献但需进一步验证
- [ ] 不推荐推进

## 10. 下一步

- [ ] 提交研究报告
- [ ] 准备 Candidate promotion
- [ ] 进入 Shadow 观察
- [ ] 归档 / 放弃
