# RESEARCH_STRATEGY_SOP

本文档是从策略想法到 Candidate 候选的研发路径和检查点。
系统设计见 `docs/ARCHITECTURE.md`，契约见 `docs/CONTRACTS.md`。

> 本文档不替代代码教程。研究工具的具体用法参考对应 `docs/features/` 文档。

---

## Purpose

从一个策略想法出发，完成 feature / model / signal / strategy 的研究验证，并判断是否进入 Candidate/Shadow。

---

## Research Flow

```
idea
  → data readiness → feature set → label definition
  → model training / prediction
  → signal generation
  → signal evaluation
  → backtest
  → experiment comparison
  → debug / refine
  → candidate decision
```

---

## Step 1: Define Idea

明确以下内容：

- 预测目标（如未来 5 日收益 rank IC）
- 预测窗口（如 5 日、10 日、20 日）
- universe（如 CSI800）
- feature 假设（如量价 / 资金流 / 基本面 / 估值 / 组合）
- baseline（至少与当前 production 或 extended feature set 对比）
- 预期增量（相对于 baseline 的 IC 或收益改善）
- 潜在数据泄露风险（feature 是否 PIT、label 是否 lookahead）

---

## Step 2: Build Feature / Label

检查项：

- feature coverage（缺失率、极端值）
- feature 是否 PIT（point-in-time），防止未来数据泄露
- label horizon 和 shift 语义是否清晰
- train / valid / test 时间切分是否无重叠
- extended feature set 的增量是否有观察价值

---

## Step 3: Train / Predict / Generate Signal

要求：

- 时间切分明确，valid / test 为 out-of-sample
- rolling train/predict 输出 OOS signal cache
- 每次运行记录 run_id、model version、feature set、signal expression
- signal 写入可追溯的存储（SignalStore 或 experiments artifact）

---

## Step 4: Signal Evaluation

观察指标（通过 SignalEvaluator / Research Analytics）：

- IC、RankIC、ICIR
- 分组收益（long_short、top/bottom 单调性）
- coverage（信号覆盖范围）
- turnover（信号换手率）
- 不同市场阶段的表现（上涨/下跌/震荡）
- 行业暴露、市值暴露（信号是否只是风格因子）

---

## Step 5: Backtest

使用 BacktestEngine（只消费 signal cache，不重新训练模型）。

观察指标：

- 年化收益、年化波动
- 最大回撤、回撤修复天数
- 换手率（单边 / 双边）
- 交易成本假设
- 持仓集中度
- 空仓 / 满仓比例
- 与 baseline 对比

---

## Step 6: Debug Checklist

### 如果信号差

- label 是否错位（horizon / shift 不正确）？
- feature 是否有未来泄露？
- signal 是否常数或极端值主导？
- coverage 是否不足？
- top/bottom 分组是否单调？
- 是否只是行业或市值暴露？
- 是否只有单一年份有效？

### 如果 IC 好但回测差

- 换手是否过高？
- 交易成本是否吃掉收益？
- top-k 是否过小导致无法分散？
- rebalance 频率是否不合适？
- 信号中是否混入了不可交易信息？

### 如果回测好但 IC 差

- 是否 portfolio construction 规则吃到了 beta？
- 是否持仓过度集中在少数标的？
- 是否只是特定年份的运气窗口？
- 是否成本低估？

---

## Step 7: Candidate Decision

进入 Candidate/Shadow 至少需要满足以下条件：

- [ ] OOS 信号评估可复现（run_id + artifact_path 可追踪）
- [ ] backtest 优于 baseline（指标对比可查）
- [ ] turnover / cost 可接受
- [ ] 没有明显数据泄露
- [ ] 关键报告已生成（包含 eval window、cost assumption）
- [ ] 能转换为标准 artifact（SignalArtifact / CandidateReport）
- [ ] 有最小 CandidateReport 或等价的实验总结

不满足以上条件时，策略停留在 Research 阶段，不进入 daily shadow run。

---

## UI / Analytics Usage

Research UI / DuckDB / ExperimentIndex 辅助观察，而不是替代判断。

- Research Analytics（DuckDB）用于 IC、RankIC、experiment index 的横向查询。
- ExperimentIndex 用于版本间比较。
- Research UI 用于可视化 signal comparison、backtest summary、model improvement。
- 这些工具不直接影响 production ledger，不下单，不改策略。
