# Candidate Promotion Checklist

> 使用本清单确保候选策略从 Research 阶段晋升到 Candidate 阶段时满足所有要求。

## 1. 策略元信息

| 字段 | 内容 |
|------|------|
| Candidate ID | `candidate_YYYYMMDD_v1` |
| Strategy ID | |
| Research ID | |
| Owner | |
| Date | YYYY-MM-DD |

## 2. 配置与版本追溯

- [ ] **Config hash recorded**: 训练/回测的配置已生成 hash
- [ ] **Model version recorded**: 模型版本已标记（如 `20260515`）
- [ ] **Signal version recorded**: 信号版本已标记（如 `v1.0`）
- [ ] **Data cutoff recorded**: 数据截止日期已记录
- [ ] **Feature schema version recorded**: 特征 schema 版本已记录
- [ ] **Training recipe version recorded**: 训练方案版本已记录

Config hash: ________
Model version: ________
Signal version: ________
Data cutoff: ________
Feature schema version: ________
Training recipe version: ________

## 3. Protected Core 检查

- [ ] **No protected core changes**: 候选策略未修改 Protected Core（见 ADR-005）
- [ ] 如果修改了 Protected Core，填写以下信息：
  - Core Change Reason: ________
  - Semantic Impact: ________
  - Regression Tests: ________
  - Rollback Plan: ________

## 4. Artifact Contract 检查

- [ ] **Artifact contract satisfied**: 输出产物满足 ADR-007 标准
- [ ] SignalArtifact 包含所有必填字段
- [ ] OrderIntentArtifact 包含所有必填字段
- [ ] 缺失字段显式标记为 `null` / `not_available`

## 5. 回测结果验收

| 指标 | 本候选 | Shadow Baseline | 是否达标 |
|------|--------|----------------|---------|
| IC Mean | | 0.039 | |
| RankIC Mean | | 0.054 | |
| Sharpe (CSI300) | | 1.771 | |
| Sharpe (CSI800) | | 2.207 | |
| Max Drawdown | | -16.12% | |

- [ ] Backtest result acceptable：候选策略在关键指标上不显著劣于当前 Shadow Baseline

## 6. Shadow Readiness

- [ ] Can enter daily shadow run
- [ ] No data pipeline issues expected
- [ ] No ledger conflicts with existing accounts
- [ ] Run manifest generation ready

## 7. 已知风险

<!-- 列出候选策略的已知风险，如在某些市场条件下表现不佳、特征缺失处理等 -->

| 风险 | 严重程度 | 缓解措施 |
|------|---------|---------|
| | High/Med/Low | |

## 8. 数据泄漏检查

- [ ] Train/test 时间不重叠
- [ ] 特征无前视偏差
- [ ] 回测使用正确的价格数据

## 9. 决策

| 维度 | 内容 |
|------|------|
| Owner Decision | ✅ Approved / ❌ Rejected / ⏳ Pending |
| Next Action | enter_shadow_observation / refine / archive |
| Notes | |

---

**Owner Signature**: ________

**Date**: ________
