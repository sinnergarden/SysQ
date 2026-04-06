# Data Layout Contract

## Goal

- 收敛 `data/`、`daily/`、`experiments/`、`runs/` 的职责边界。
- 把 daily ops 产物稳定收口到 `daily/{date}/pre_open|post_close/`。
- 用 `data/derived/` 保存少量长期结构化汇总表。

## Current Contract

- 默认账户主库：`data/meta/real_account.db`
- 盘前默认目录：
  - `plans/`
  - `order_intents/`
  - `signals/`
  - `reports/`
  - `manifests/`
- 盘后默认目录：
  - `reconciliation/`
  - `snapshots/`
  - `reports/`
  - `manifests/`
- 研究与训练输出默认目录：`experiments/`
- 结构化 JSON 报告默认目录：`experiments/reports/`
- `data/derived/` 当前支持：
  - `signal_baskets.csv`
  - `order_intents.csv`
  - `reconciliation_summary.csv`
  - `position_gaps.csv`

## Done Criteria

- daily 目录不再依赖额外的 legacy 子目录。
- 账户主库不再保留根目录双份默认路径。
- 文档只描述当前有效布局。
- 最小测试覆盖 daily layout 与 rollup 行为。
