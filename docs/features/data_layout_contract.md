# Data Layout Contract

## Goal

- 收敛 `data/`、`daily/`、`experiments/`、`runs/` 的职责边界。
- 把 daily ops 产物完全收口到 `daily/{date}/pre_open|post_close/`。
- 新增一个最小长期汇总层 `data/derived/`，用于横向分析与 debug。

## Use Cases

- 盘前执行 `scripts/run_daily_trading.py` 时，signal basket、plan、order intents、report、manifest 自动写入 `daily/{execution_date}/pre_open/`。
- 盘后执行 `scripts/run_post_close.py` 时，对账 summary、持仓缺口、real sync snapshot、report、manifest 自动写入 `daily/{execution_date}/post_close/`。
- 排障时，先看 `data/derived/` 的长期表，再通过 `artifact_source` 回跳到 `daily/{date}` 原始证据。
- 研究与实验输出默认写入 `experiments/`，不再与生产 evidence 混放。

## API Change

- 不修改受保护的核心接口签名。
- 调整 daily ops CLI 的默认输出路径与默认账本路径：
  - `scripts/run_daily_trading.py`
  - `scripts/run_post_close.py`
  - `scripts/run_signal_quality.py`
- 新增 `scripts/rollup_daily_artifacts.py`，把 daily evidence 抽取到 `data/derived/`。
- 不再提供旧 `data/` 根目录 daily artifacts 的 fallback / migrate 逻辑。

## UI

- 无图形界面变更。
- 文档重点说明目录分层原则、设计动机、关键字段口径，而不是只罗列 tree。

## Constraints

- 不改 `data/raw/`、`data/qlib_bin/`、`data/models/` 本体。
- 长期账户主账本固定为 `data/meta/real_account.db`，不按天复制。
- `daily/` 只放当天 evidence / snapshot，不放主 db。
- `data/derived/` 使用简单稳定的 append-friendly CSV，不引入复杂数据库。
- 不继续兼容 `daily/ops/`、`data/experiments/`、`data/` 根目录散落 daily artifacts。

## Done Criteria

- `daily/ops/` 相关逻辑与文档引用被删除。
- 盘前默认输出收敛到：
  - `plans/`
  - `order_intents/`
  - `signals/`
  - `reports/`
  - `manifests/`
- 盘后默认输出收敛到：
  - `reconciliation/`
  - `snapshots/`
  - `reports/`
  - `manifests/`
- 默认账本路径固定为 `data/meta/real_account.db`。
- `qsys/experiment/manager.py` 默认输出切到 `experiments/`。
- `data/derived/` 至少沉淀 2-3 类长期表，并支持重复 rollup 去重。
- `docs/DATA_LAYOUT.md`、`docs/RUNBOOK.md`、`docs/ops/PRE_OPEN_SOP.md`、`docs/ops/POST_CLOSE_SOP.md` 完成同步。
- 相关测试覆盖默认路径、无 legacy fallback、rollup 最小行为。
