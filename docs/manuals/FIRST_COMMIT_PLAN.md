# 首次提交拆分方案（docs / core / tests）

本方案用于把首次提交拆成可审阅、可回滚的三段。

## Commit 1：文档与规则

范围：
- `README.md`
- `context.md`
- `DAILY_TRADING_GUIDE.md`
- `CONTRIBUTING.md`
- `docs/**`
- `.trae/rules/project_rules.md`

建议提交信息：
```text
docs: 完善中文文档体系与开发规则（目标/环境/流程/入口策略）
```

## Commit 2：核心代码与脚本

范围：
- `qsys/live/**`
- `scripts/run_daily_trading.py`
- `scripts/run_plan.py`
- `scripts/run_reconcile.py`
- `.gitignore`

建议提交信息：
```text
refactor: 收敛交易主入口并增强实盘同步与脚本稳定性
```

## Commit 3：测试与回归

范围：
- `tests/**`

建议提交信息：
```text
test: 增强 live 交易链路与数据质量回归覆盖
```

## 提交前统一检查

```bash
python -m compileall qsys scripts tests
python -m unittest discover tests
```
