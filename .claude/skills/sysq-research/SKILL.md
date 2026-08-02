# sysq-research

## Purpose
SysQ 研究/回测 skill：信号研究、实验、回测、评估（UC_RESEARCH_BACKTEST）。覆盖 feature→model→signal→backtest→evaluation 的研究链路。

## Inputs
- 研究任务（feature set / model / signal / backtest / 评估）
- 对应 UC：UC_RESEARCH_BACKTEST
- 相关 config（configs/research/）、脚本（scripts/research/）、信号/标签产物

## Required reads
- `AGENTS.md`
- `docs/requirements/harness_map.yaml`
- UC_RESEARCH_BACKTEST 定义（`docs/requirements/domains/research.md`）
- 相关 config 与代码（qsys/research/、qsys/signal/、qsys/label/、qsys/backtest/）

## Workflow
1. 归类 UC_RESEARCH_BACKTEST。
2. 声明 SCOPE，通过 EXECUTION_GATE（路径在 allowed_paths、不触 forbidden_paths）。
3. 执行研究链路：
   - 数据 readiness → feature set → rolling train/predict → signal cache → backtest → evaluation。
   - 使用 canonical 入口：`scripts/run_research.py`、`scripts/run_signal_analytics.py`、`scripts/research/backtest_from_signal.py`。
4. **Lookahead 纪律（F01）**：信号 `trade_date=T` 只能使用严格截止于 `data_date=prev_td(T)` 的特征；训练标签必须在前一预测窗前完全实现（maturity gate）。
5. 评估用 IC/RankIC/ICIR + 回测（执行价/成本/T+1）。
6. 运行 harness checks（no-latest、model-boundary、scripts-entrypoints 等）。
7. 按 OUTPUT CONTRACT 输出，LOOP_CHECK 判定是否需 reviewer。

## 规则
- 禁止用未来数据 / lookahead（feature ≤ data_date < trade_date）。
- 禁止 latest / mtime / symlink 解析模型或信号。
- 研究产物不进 daily/shadow/prod（除非走 candidate 晋级）。
- 实验可自由试错，但结论必须带 provenance（config + hash + 日期区间）。

## Never
- 用当日收盘特征做当日开盘信号
- 训练标签延伸进预测窗口
- 把研究结论直接当生产信号
- 改 broker / trader / ledger / deploy

## Required checks
```bash
python harness/checks/check_no_latest_model_resolution.py
python harness/checks/check_model_resolution_boundary.py
python harness/checks/check_scripts_entrypoints.py
# 合并后补充：check_promotion_pointer.py（PR #218，见 F03）
```
