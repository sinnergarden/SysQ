# FEATURE: minimal_production_kernel

## Goal

- 为日常交易闭环补齐一个最小生产内核：可重跑、可审计、可断点续跑。
- 用最少的新骨架把运行状态、产物索引和交易账本固定下来，减少脚本式流程的隐式状态。

## Use Cases

- 用例 1：操作者按交易日运行固定步骤，系统在 `runs/{date}/manifest.json` 中记录每一步状态和产物，失败后可从指定步骤继续跑。
- 用例 2：操作者需要回看某个交易日的 broker 账户/持仓快照、订单、成交和指标，可从 `data/trade.db` 与运行目录直接追溯。
- 用例 3：尚未接通真实下单时，runner 也能完整跑完 stub 步骤，并留下可观察 artifact，便于后续逐步替换业务实现。

## API Change

- 是否新增 API：是。
- 是否修改现有 API：否。
- 变更点列表：
  - 新增 `qsys/trader/database.py`，提供最小交易账本初始化与写入接口。
  - 新增 `qsys/core/runner.py`，提供 manifest 驱动的状态机 runner 和 CLI。
  - 新增 `qsys/broker/gateway.py`，提供只读 broker gateway 和快照 artifact 输出。
  - 新增最小入口脚本，演示如何本地执行一次生产内核。

## UI

- 无。

## Constraints

- 技术约束：优先使用标准库 `sqlite3`，不引入 repository/service/manager 分层，不做隐式配置扫描。
- 技术约束：`runs/{date}/manifest.json` 是 runner 的唯一真相源，步骤状态必须结构化保存。
- 业务约束：仅实现 broker 只读接口，不实现真实下单。
- 禁止改动：不修改 `qsys.live.account.RealAccount`、`qsys.live.manager.LiveManager.run_daily_plan`、`qsys.live.simulation.ShadowSimulator.simulate_execution`、`qsys.model.base.IModel.fit/predict/save/load` 的公开契约。

## Done Criteria

- `data/trade.db` 可被初始化，并包含 `pipeline_runs`、`position_snapshots`、`orders`、`fills`、`daily_metrics`。
- runner 能写出 `runs/{date}/manifest.json`，成功步骤默认跳过，失败步骤记录错误，支持 `--from-step` 和 `--force`。
- broker gateway 能把账户和持仓写到 `runs/{date}/02_broker/broker_snapshot.json` 并注册到 manifest。
- 新增最小测试覆盖账本初始化、runner 跳过已成功步骤、broker snapshot artifact 写出。
- 新增最小可运行示例，能在本地跑出 manifest、broker artifact 和 sqlite 数据库。

## Test Plan

- 单元测试：新增 `tests/test_minimal_production_kernel.py`。
- 集成测试：运行最小示例 CLI，一次生成 `runs/{date}/manifest.json`、`runs/{date}/02_broker/broker_snapshot.json`、`data/trade.db`。
- 回归测试：
  - `python -m compileall qsys scripts tests`
  - `python -m unittest tests.test_minimal_production_kernel`

## Rollback Plan

- 回滚策略：按本次 commit 整体回滚。
- 回滚触发条件：runner 无法重复运行同一交易日、manifest 状态写入不稳定、broker 快照无法落盘、SQLite 初始化失败。

## Notes

- 本次只补最小生产骨架，不触碰已有大流程编排。
- 后续可逐步把真实数据检查、训练诊断、回测摘要、对账细节接入当前 runner 的各步骤实现。
