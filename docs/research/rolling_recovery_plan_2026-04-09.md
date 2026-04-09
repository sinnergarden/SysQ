# Rolling Recovery Plan (2026-04-09)

## Goal

把当前混杂的 formal rolling / feature / UI 问题收敛成可执行的工程拆分，避免继续在同一条长线上反复叠加修改。

## Tracks

### PR1 - formal rolling / zero-cost 对齐

目标：让 `zero_cost` 真正成为和正式曲线同计划、同持仓约束、同账户路径、仅成本参数不同的对照曲线。

范围：
- `scripts/run_formal_rolling_backfill.py`
- 如需要，`qsys/live/simulation.py`
- 如需要，`qsys/live/account.py`
- formal rolling 相关测试/验证脚本

当前已知问题：
- `zero_cost` 曲线当前出现 `top5` 却跑出 `8/9` 持仓，说明对照并不干净。
- 当前 `zero_cost` 更像宽松执行曲线，而不是严格零成本版本。

验收：
- 含成本 / 零成本曲线在相同交易日具有一致的目标持仓数约束。
- 除 `fee/tax/slippage` 外，不应引入额外成交或持仓差异。

### PR2 - feature loader / snapshot / health 对齐

目标：统一训练、UI snapshot、feature health 的特征读取口径，先消除“snapshot 有值但 health 报空”的假冲突。

范围：
- `qsys/research_ui/assembler.py`
- `qsys/research_ui/api.py`
- `qsys/data/*` / `qsys/feature/*` 中与 on-demand 特征读取相关的路径
- 对应测试

当前已知问题：
- `feature snapshot` 与 `feature health` 对 semantic / derived feature 的覆盖结论不一致。
- 一部分 alpha158 字段在 snapshot 中也确实为空，需要继续区分“真空”与“假空”。

验收：
- 同一 trade_date / instrument / feature list 下，snapshot 与 health 对是否有值的判定一致。
- 能清楚区分：字段不存在、字段当日无值、字段因历史窗口不足无值。

### PR3 - feature inventory 清理与收敛

目标：扫描当前 400+ 注册 feature，按业务 idea 与线上可用性收敛，形成 canonical naming 和清理清单。

范围：
- feature registry / feature library / model meta / online snapshot / health / 训练矩阵审计
- 先产出 audit，再做真正清理

建议产物：
- `docs/research/feature_inventory_audit_2026-04-09.md`
- `scratch/feature_inventory_audit.csv`

建议字段：
- `feature_name`
- `canonical_name`
- `source_layer`
- `group_name`
- `idea_family`
- `alias_of`
- `used_in_models`
- `snapshot_non_null_ratio`
- `health_coverage`
- `train_matrix_ready`
- `online_ready`
- `action`
- `notes`

`action` 值建议：
- `keep`
- `merge`
- `rename`
- `drop`
- `investigate`

### Research Report - old backtest vs formal rolling audit

目标：解释旧 `phase123` / rolling 结果与当前 formal rolling 之间的差距来源，为后续 feature 清理与执行策略修复提供依据。

不做 PR，单独形成研究报告。

问题清单：
- 旧 `phase123` 长窗结果到底由哪个脚本、哪套参数产生？
- 是否周级重训 / 周级换仓？
- 与当前 formal rolling 的主要口径差异是什么？
- 差异中有多少来自成本、多少来自特征口径、多少来自执行路径？

## Immediate order

1. PR1 - zero-cost 对齐
2. PR2 - loader / snapshot / health 对齐
3. Research Report - old backtest vs formal rolling audit
4. PR3 - feature inventory audit -> cleanup

## Current evidence snapshot

- 旧长窗文件：`experiments/backtest_result_phase123_20250102_20260320.csv`
- 当前 formal rolling：`experiments/backtest_result.csv`
- 当前 zero-cost：`experiments/backtest_result_zero_cost.csv`
- 到 `2026-02-27`：
  - old phase123 approx `+41.57%`
  - current formal approx `-13.30%`
  - current zero-cost approx `+27.17%`

这说明：
- 成本不是全部答案。
- 旧研究回测与当前 formal rolling 之间仍存在明显口径差。
- 当前 zero-cost 实现本身也还需要先校正。
