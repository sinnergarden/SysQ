# Archive

> Historical reference only. Not current truth.

## Purpose

Archive 存放非 current truth 的历史文档和脚本，保留历史参考价值，同时降低主导航噪音。

## Rules

- **archive/ 下的文件不属于 current truth**。当前 SOP、入口、构架、配置以仓库根目录和 `docs/` 中的主导航文档为准。
- **agent 不得从 archive/ import 或调用**。archive/ 中的脚本不保证可运行，不保证与当前代码兼容。
- **新代码不得依赖 archive/**。不允许新增 import、exec、subprocess 或文档引用指向 archive/。
- **archive/ 不参与 systemd、daily ops、research SOP、production path**。
- **如果需要恢复**，必须单独 PR 移回主路径，并补测试、文档和 current owner。

## Structure

```
archive/
  README.md          ← 本文件
  docs/
    features/        ← 已暂停/已完成/已过时的功能文档
    research/        ← 历史研究实验与分析
    runbooks/        ← 已被 SOP 替代的旧运行手册
    architecture/    ← 历史架构说明（已被 ARCHITECTURE.md 替代）
    audits/          ← 一次性代码审计报告
    templates/       ← 旧模板（引用了 archive/ 内文档）
    tutorials/       ← 旧教程 / 历史教程
    ops/             ← 非 current 的 SOP 文档
  scripts/
    debug/           ← 一次性调试/分析脚本
    research_legacy/ ← 旧研究实验脚本（非 current SOP）
```

## Archived Items

### docs/

| 原路径 | 归档路径 | 原因 |
|--------|---------|------|
| `docs/features/factor_governance_and_research_migration.md` | `archive/docs/features/` | 因子治理方向已暂停 |
| `docs/features/factor_governance_pr_plan.md` | `archive/docs/features/` | 因子治理方向已暂停 |
| `docs/features/phase1.5-daily-runner-boundary.md` | `archive/docs/features/` | Phase 1.5 已完成 |
| `docs/features/qsys_workflow_adapter_plan.md` | `archive/docs/features/` | 已实施完成 |
| `docs/features/ops_requirements.md` | `archive/docs/features/` | 已完成/过时 |
| `docs/features/data_layout_contract.md` | `archive/docs/features/` | 已被 `docs/REPO_LAYOUT.md` 合并 |
| `docs/runbooks/daily-ops.md` | `archive/docs/runbooks/` | 已被 `docs/ops/DAILY_OPS_SOP.md` 替代 |
| `docs/DATA_LAYOUT.md` | `archive/docs/` | 已被 `docs/REPO_LAYOUT.md` 合并 |
| `docs/features/*.md`（全部）| `archive/docs/features/` | 功能规格 → archive（非 current truth） |
| `docs/research/*.md`（全部）| `archive/docs/research/` | 历史研究实验分析 |
| `docs/runbooks/*.md`（全部）| `archive/docs/runbooks/` | 已被 SOP 替代 |
| `docs/architecture/*.md`（全部）| `archive/docs/architecture/` | 已被 `docs/ARCHITECTURE.md` 合并 |
| `docs/audits/*.md`（全部）| `archive/docs/audits/` | 一次性审计 |
| `docs/templates/*.md`（全部）| `archive/docs/templates/` | 引用了 archive/ 内的文档 |
| `docs/tutorials/*.md`（全部）| `archive/docs/tutorials/` | 教程文档（持续更新中） |
| `docs/ops/DATA_PIPELINE_SOP.md` | `archive/docs/ops/` | 已被 DAILY_OPS_SOP 合并 |
| `docs/ops/FEATURE_PIPELINE_SOP.md` | `archive/docs/ops/` | 非 current |
| `docs/ops/MODEL_OPS_SOP.md` | `archive/docs/ops/` | 非 current |
| `docs/ops/POST_CLOSE_SOP.md` | `archive/docs/ops/` | 已被 DAILY_OPS_SOP 合并 |
| `docs/ops/PRE_OPEN_SOP.md` | `archive/docs/ops/` | 已被 DAILY_OPS_SOP 合并 |
| `docs/ops/READINESS.md` | `archive/docs/ops/` | 非 current |
| `docs/SCRIPT_INVENTORY.md` | `archive/docs/` | 脚本清单（非 current truth） |
| `docs/TESTING.md` | `archive/docs/` | 测试文档（非 current） |
| `docs/STYLEGUIDE.md` | `archive/docs/` | 风格指南（非 current） |
| `docs/README.md` | `archive/docs/` | 已被 `docs/CONTRACTS.md` + `AGENTS.md` 替代 |
| `docs/RUNBOOK.md` | `archive/docs/` | 已被 ops/SOP 替代 |
| `docs/strict-evaluation.md` | `archive/docs/` | 已被评估流程框架替代 |
| `docs/alpha_v1_baseline.md` | `archive/docs/` | 历史基线记录 |
| `docs/alpha_v1_model_debug.md` | `archive/docs/` | 一次性调试分析 |
| `docs/margin_data_batch1.md` | `archive/docs/` | 一次性数据说明 |
| `docs/production-model.md` | `archive/docs/` | 历史生产模型说明 |

### scripts/

| 原路径 | 归档路径 | 原因 |
|--------|---------|------|
| `scripts/audit_formal_backtest.py` | `archive/scripts/debug/` | 一次性审计脚本 |
| `scripts/check_amount.py` | `archive/scripts/debug/` | 一次性数据检查 |
| `scripts/compare_formal_173_before_after.py` | `archive/scripts/research_legacy/` | 一次性对比实验 |
| `scripts/compare_absnorm_variants.py` | `archive/scripts/research_legacy/` | 冗余 wrapper（逻辑在 `run_absnorm_comparison.py`）|
| `scripts/debug_data_quality.py` | `archive/scripts/debug/` | 一次性调试 |
| `scripts/debug_model_performance.py` | `archive/scripts/debug/` | 一次性调试 |
| `scripts/patch_backtest_ui_data.py` | `archive/scripts/debug/` | 一次性补丁 |
| `scripts/run_prod_backtest.py` | `archive/scripts/research_legacy/` | 0 引用，无消费者 |
| `scripts/run_prod_rolling_backtest.py` | `archive/scripts/research_legacy/` | 0 引用，无消费者 |
