# FEATURE: post_close_reconciliation

## Goal

- 为 SysQ 补齐盘后闭环：人工执行完挂单后，可以用统一 CSV 回填真实账户状态与成交结果，并自动生成 real vs shadow 的差异报告。
- 让“生成计划 -> 人工执行 -> 盘后回填 -> 偏差跟踪”成为可重复运行的日常流程。

## Use Cases

- 用例 1：交易日收盘后，操作者从券商导出 CSV，运行盘后脚本，同步真实账户状态，并生成当日 real/shadow 差异报告。
- 用例 2：真实账户只发生部分成交或出现费用/税费偏差，系统仍能记录真实成交，并在报告中暴露与 shadow 的差异。
- 用例 3：若 CSV 缺少关键字段，脚本应立即报错，不写入不完整状态。

## API Change

- 是否新增 API：是。
- 是否修改现有 API：是。
- 变更点列表：
  - 新增 `scripts/run_post_close.py`
  - 新增 `qsys/live/reconciliation.py`
  - 扩展 `qsys/live/account.py`：支持 trade log 读写
  - 扩展 `qsys/live/simulation.py`：shadow 模拟成交时记录 trade log
- 向后兼容策略：
  - 保留现有 `run_daily_trading.py` 盘前工作流
  - 新增盘后入口，不强行并入旧入口

## UI

- 无。

## Constraints

- 技术约束：
  - 当前版本先走 CSV 回填，不接 broker API。
  - 盘后对账基于 SQLite `real_account.db`。
  - scripts 仅负责编排，核心对账逻辑下沉到 `qsys/live/`。
- 业务约束：
  - 输入 CSV 必须至少提供：`symbol, amount, price, cost_basis, cash, total_assets`
  - 若包含成交信息，建议提供：`side, filled_amount, filled_price, fee, tax, total_cost, order_id`
- 禁止改动：
  - 本次不改交易核心规则，不接自动下单。
  - 本次不重写 `run_daily_trading.py` 为超大统一入口。

## Done Criteria

- 可以通过 `scripts/run_post_close.py` 完成真实账户回填与对账。
- shadow 模拟成交会记录到 `trade_log`。
- 会输出结构化 CSV：summary / positions / real_trades / real_sync_snapshot。
- 相关测试通过：`tests/test_live_trading.py`
- 文档更新完成：`docs/RUNBOOK.md`

## Test Plan

- 单元测试：
  - `tests/test_live_trading.py` 覆盖 trade log 写入、CSV 回填、real/shadow 对账。
- 集成测试：
  - 用临时 SQLite + 临时 CSV，完整跑一遍 shadow -> real sync -> reconcile。
- 回归测试：
  - `python -m unittest tests.test_live_trading`
  - `python -m unittest discover tests`

## Rollback Plan

- 回滚策略：按 commit 回滚。
- 回滚触发条件：
  - 真实账户回填导致状态写错
  - 对账输出不稳定或明显错误
  - 影响现有 shadow 模拟幂等性

## Notes

- 当前版本优先强调“能稳定运营”，而不是一步到位接入所有券商细节。
- 后续可扩展：
  - broker API 接入
  - 对账结果入库
  - 偏差日报/周报
  - 周末训练前自动汇总 real vs shadow 偏差
